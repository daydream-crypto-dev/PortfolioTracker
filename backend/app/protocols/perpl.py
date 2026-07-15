from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import os
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from ..db_models import SourceORM
from .base import DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "perpl"
PROTOCOL_NAME = "Perpl"
PROTOCOL_URL = "https://perpl.xyz/"
LOCAL_CHAIN_ID = "monad"

COLLATERAL_TOKEN_ADDRESS = "0x00000000eFE302BEAA2b3e6e1b18d08D69a9012a"
COLLATERAL_SYMBOL = "AUSD"
COLLATERAL_NAME = "AUSD"
COLLATERAL_DECIMALS = 6
DEFAULT_API_URL = "https://app.perpl.xyz/api"


class PerplApiError(RuntimeError):
    """Raised when Perpl's API rejects a request or returns invalid data."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PerplMarket:
    market_id: int
    symbol: str
    name: str
    price_decimals: int
    size_decimals: int
    mark_price_usd: Decimal


class PerplDefiAdapter:
    """Read Perpl account collateral and open perp positions using a read-only API key."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        chain_id: int = 143,
        api_key: Optional[str] = None,
        api_key_secret: Optional[str] = None,
        wallet_address: Optional[str] = None,
        timeout_seconds: int = 20,
        max_history_pages: int = 50,
        max_request_attempts: int = 3,
    ) -> None:
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/")
        self.chain_id = chain_id
        self.api_key = api_key
        self.api_key_secret = api_key_secret
        self.wallet_address = self._normalize_address(wallet_address) if wallet_address else None
        self.timeout_seconds = timeout_seconds
        self.max_history_pages = max_history_pages
        self.max_request_attempts = max(1, max_request_attempts)

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message="Perpl skipped source without wallet details")

        missing = [
            name
            for name, value in [
                ("api_key", self.api_key),
                ("api_key_secret", self.api_key_secret),
                ("wallet_address", self.wallet_address),
            ]
            if not value
        ]
        if missing:
            return DefiFetchResult(
                positions=[],
                status_message="No complete Perpl API credential block configured for this wallet; Perpl sync skipped.",
            )

        source_address = self._normalize_address(source.wallet.address)
        if source_address != self.wallet_address:
            return DefiFetchResult(
                positions=[],
                status_message="Perpl skipped source because the configured Perpl wallet address does not match this wallet.",
            )

        context = self._public_get_json("/v1/pub/context")
        markets = self._markets_from_context(context)
        positions: List[RawDefiPosition] = []

        account_events = self._fetch_authenticated_page("/v1/trading/account-history", count=1)
        account_position = self._account_collateral_position(source.id, account_events, timestamp)
        if account_position is not None:
            positions.append(account_position)

        history_rows = self._fetch_authenticated_pages("/v1/trading/position-history", count=25)
        skipped_rows = 0
        for row in self._latest_open_position_rows(history_rows):
            position = self._perp_position(source.id, row, markets, timestamp)
            if position is None:
                skipped_rows += 1
                logger.warning("Skipping unrecognized Perpl position row: %s", row)
                continue
            positions.append(position)

        status_message = f"Perpl sync wrote {len(positions)} positions."
        if skipped_rows:
            status_message = f"{status_message} Skipped {skipped_rows} unrecognized position rows."
        return DefiFetchResult(positions=positions, status_message=status_message)

    def _account_collateral_position(
        self,
        source_id: str,
        account_events: List[Dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[RawDefiPosition]:
        if not account_events:
            return None

        latest_event = account_events[0]
        balance = self._collateral_value_from_fields(latest_event, ["b", "balance"])
        locked_balance = self._collateral_value_from_fields(latest_event, ["lb", "locked_balance", "lockedBalance"])
        if balance is None or balance == 0:
            return None

        metadata = {
            "kind": "account_collateral",
            "latest_account_event": latest_event,
            "locked_balance": str(locked_balance or Decimal("0")),
        }
        return RawDefiPosition(
            source_id=source_id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category="deposit",
            asset_id=f"monad-{COLLATERAL_TOKEN_ADDRESS.lower()}",
            asset_symbol=COLLATERAL_SYMBOL,
            asset_name=COLLATERAL_NAME,
            decimals=COLLATERAL_DECIMALS,
            contract_address=COLLATERAL_TOKEN_ADDRESS.lower(),
            quantity=balance,
            price_usd=Decimal("1"),
            value_usd=balance,
            apy=None,
            health_factor=None,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash("collateral", latest_event, timestamp),
        )

    def _perp_position(
        self,
        source_id: str,
        row: Dict[str, Any],
        markets: Dict[int, PerplMarket],
        timestamp: datetime,
    ) -> Optional[RawDefiPosition]:
        market_id = self._int_from_fields(
            row,
            ["mkt", "m", "market_id", "marketId", "perpetual_id", "perpetualId", "perp_id", "perpId"],
        )
        if market_id is None:
            return None

        market = markets.get(market_id)
        if market is None:
            logger.warning("Perpl position referenced unknown market_id=%s", market_id)
            return None

        size_raw = self._decimal_from_fields(row, ["s", "size", "sz", "lot", "lot_lns", "lotLNS"])
        if size_raw is None or size_raw == 0:
            return None
        quantity = self._scale_down(size_raw, market.size_decimals)

        side = self._side_label(row)
        entry_price = self._entry_price(row, market)
        deposit = self._collateral_value_from_fields(
            row,
            ["d", "dep", "deposit", "deposit_cns", "depositCNS", "margin", "collateral", "a", "amount"],
        ) or Decimal("0")

        explicit_pnl = self._collateral_value_from_fields(
            row,
            ["pnl", "upnl", "unrealized_pnl", "unrealizedPnl", "unrealized"],
        )
        if explicit_pnl is not None:
            pnl = explicit_pnl
        else:
            delta_pnl = self._collateral_value_from_fields(
                row,
                ["dpnl", "delta_pnl", "deltaPnl", "delta_pnl_cns", "deltaPnlCNS"],
            )
            premium_pnl = self._collateral_value_from_fields(
                row,
                ["ppnl", "premium_pnl", "premiumPnl", "premium_pnl_cns", "premiumPnlCNS"],
            ) or Decimal("0")
            if delta_pnl is None:
                delta_pnl = self._mark_to_market_pnl(side, entry_price, market.mark_price_usd, quantity)
            pnl = delta_pnl + premium_pnl

        equity = deposit + pnl
        if equity == 0:
            return None

        symbol = f"{market.symbol}-PERP"
        metadata = {
            "kind": "perpetual_position",
            "market_id": market.market_id,
            "market_symbol": market.symbol,
            "side": side,
            "entry_price_usd": str(entry_price) if entry_price is not None else None,
            "mark_price_usd": str(market.mark_price_usd),
            "position_deposit_usd": str(deposit),
            "unrealized_pnl_usd": str(pnl),
            "raw_position": row,
        }
        return RawDefiPosition(
            source_id=source_id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category="perp",
            asset_id=f"perpl-{market.market_id}-{side.lower()}",
            asset_symbol=symbol,
            asset_name=f"Perpl {market.symbol} {side}",
            decimals=market.size_decimals,
            contract_address=None,
            quantity=quantity,
            price_usd=market.mark_price_usd,
            value_usd=equity,
            apy=None,
            health_factor=None,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(f"perp-{market.market_id}-{side}", row, timestamp),
        )

    def _markets_from_context(self, context: Dict[str, Any]) -> Dict[int, PerplMarket]:
        raw_markets = context.get("markets")
        if not isinstance(raw_markets, list):
            raise PerplApiError("Perpl context response did not include markets")

        markets: Dict[int, PerplMarket] = {}
        for raw_market in raw_markets:
            if not isinstance(raw_market, dict):
                continue
            market_id = self._int_from_fields(raw_market, ["id", "market_id", "marketId", "perpetual_id"])
            if market_id is None:
                continue
            config = raw_market.get("config") if isinstance(raw_market.get("config"), dict) else {}
            state = raw_market.get("state") if isinstance(raw_market.get("state"), dict) else {}
            price_decimals = self._int_from_fields(config, ["price_decimals", "priceDecimals"]) or 0
            size_decimals = self._int_from_fields(config, ["size_decimals", "sizeDecimals"]) or 0
            mark_raw = self._decimal_from_fields(state, ["mrk", "mark", "mark_price", "markPrice", "lst", "last"])
            mark_price = self._scale_down(mark_raw or Decimal("0"), price_decimals)
            symbol = str(raw_market.get("symbol") or raw_market.get("name") or raw_market.get("size_units") or market_id)
            if not symbol:
                symbol = str(raw_market.get("name") or market_id)
            markets[market_id] = PerplMarket(
                market_id=market_id,
                symbol=symbol,
                name=str(raw_market.get("name") or symbol),
                price_decimals=price_decimals,
                size_decimals=size_decimals,
                mark_price_usd=mark_price,
            )
        return markets

    def _latest_open_position_rows(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        latest_rows: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in rows:
            key = self._position_key(row)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if self._is_open_position(row):
                latest_rows.append(row)
        return latest_rows

    def _position_key(self, row: Dict[str, Any]) -> str:
        position_id = self._first_present(row, ["id", "position_id", "positionId", "lp"])
        if position_id is not None:
            return f"position:{position_id}"
        market_id = self._first_present(row, ["mkt", "m", "market_id", "marketId", "perpetual_id", "perpetualId"])
        side = self._first_present(row, ["sd", "side", "position_type", "positionType", "type"])
        return f"market:{market_id}:side:{side}"

    def _is_open_position(self, row: Dict[str, Any]) -> bool:
        removed = self._first_present(row, ["r", "removed"])
        if removed is True:
            return False

        status = self._first_present(row, ["st", "status"])
        if status is None:
            return True
        if isinstance(status, str):
            normalized = status.strip().lower()
            if normalized == "open":
                return True
            if normalized in {"closed", "liquidated", "deleveraged", "unwound", "failed"}:
                return False
        status_int = self._to_int(status)
        # Perpl API docs define PositionStatus Open as 1.
        return status_int == 1 if status_int is not None else False

    def _entry_price(self, row: Dict[str, Any], market: PerplMarket) -> Optional[Decimal]:
        raw = self._decimal_from_fields(
            row,
            ["ep", "entry", "entry_price", "entryPrice", "avg_entry_price", "avgEntryPrice", "price"],
        )
        if raw is None and "id" in row:
            # Compact payloads commonly use p for price; avoid this fallback
            # when p could be the only position identifier.
            raw = self._decimal_from_fields(row, ["p"])
        if raw is None:
            return None
        return self._scale_down(raw, market.price_decimals)

    def _mark_to_market_pnl(
        self,
        side: str,
        entry_price: Optional[Decimal],
        mark_price: Decimal,
        quantity: Decimal,
    ) -> Decimal:
        if entry_price is None:
            return Decimal("0")
        if side == "Short":
            return (entry_price - mark_price) * quantity
        return (mark_price - entry_price) * quantity

    def _side_label(self, row: Dict[str, Any]) -> str:
        value = self._first_present(row, ["sd", "side", "position_type", "positionType", "type"])
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"short", "sell", "ask"}:
                return "Short"
            if normalized in {"long", "buy", "bid"}:
                return "Long"
        side_int = self._to_int(value)
        if side_int == 2:
            return "Short"
        return "Long"

    def _fetch_authenticated_page(self, path: str, count: int = 100) -> List[Dict[str, Any]]:
        return self._fetch_authenticated_pages(path, count=count, max_pages=1)

    def _fetch_authenticated_pages(
        self,
        path: str,
        count: int = 100,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        pages = 0
        page_limit = max_pages or self.max_history_pages
        warn_on_page_limit = max_pages is None
        while pages < page_limit:
            params = {"count": str(count)}
            if cursor:
                params["page"] = cursor
            target = f"{path}?{urlencode(params)}"
            try:
                payload = self._signed_get_json(target)
            except PerplApiError as exc:
                if exc.status_code == 404:
                    return rows
                raise

            page_rows = payload.get("d")
            if isinstance(page_rows, list):
                rows.extend(item for item in page_rows if isinstance(item, dict))
            cursor_value = payload.get("np")
            cursor = str(cursor_value) if cursor_value else None
            pages += 1
            if not cursor:
                break

        if warn_on_page_limit and pages >= page_limit and cursor:
            logger.warning("Perpl history pagination stopped after %d pages for %s", page_limit, path)
        return rows

    def _public_get_json(self, target: str) -> Dict[str, Any]:
        url = f"{self.api_url}{target}"
        return self._send_json_request(
            lambda: Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "PortfolioTracker/0.6 (+local)"},
            )
        )

    def _signed_get_json(self, target: str) -> Dict[str, Any]:
        if not self.api_key or not self.api_key_secret:
            raise PerplApiError("PERPL_API_KEY and PERPL_API_KEY_SECRET are required for Perpl signed reads")

        def build_request() -> Request:
            timestamp_ms = str(int(time.time() * 1000))
            nonce = self._b64url(os.urandom(16))
            body_hash = hashlib.sha256(b"").hexdigest()
            canonical = "\n".join([str(self.chain_id), "GET", target, timestamp_ms, nonce, body_hash])
            signature = self._sign_canonical(canonical)
            return Request(
                f"{self.api_url}{target}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PortfolioTracker/0.6 (+local)",
                    "X-API-Key": self.api_key,
                    "X-API-Timestamp": timestamp_ms,
                    "X-API-Nonce": nonce,
                    "X-API-Signature": signature,
                },
            )

        return self._send_json_request(build_request)

    def _send_json_request(self, request_factory: Callable[[], Request]) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        transient_errors = (URLError, http.client.IncompleteRead, TimeoutError, socket.timeout, ssl.SSLError)

        for attempt in range(1, self.max_request_attempts + 1):
            request = request_factory()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise PerplApiError(f"Perpl HTTP {exc.code}: {detail[:300]}", status_code=exc.code) from exc
            except transient_errors as exc:
                last_error = exc
                if attempt >= self.max_request_attempts:
                    break
                logger.warning(
                    "Retrying Perpl request after transient read failure attempt=%d/%d url=%s error=%s",
                    attempt,
                    self.max_request_attempts,
                    request.full_url,
                    exc,
                )
                time.sleep(min(2.0, 0.35 * (2 ** (attempt - 1))))
                continue
            except json.JSONDecodeError as exc:
                raise PerplApiError("Perpl returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise PerplApiError("Perpl returned a non-object JSON body")
            return payload

        raise PerplApiError(
            f"Perpl request failed after {self.max_request_attempts} attempts: {last_error}"
        ) from last_error

    def _sign_canonical(self, canonical: str) -> str:
        try:
            from nacl.signing import SigningKey
        except ImportError as exc:
            raise PerplApiError("PyNaCl is required for Perpl API signing. Run pip install -r backend/requirements.txt") from exc

        if not self.api_key_secret:
            raise PerplApiError("PERPL_API_KEY_SECRET is required for Perpl API signing")
        secret = self.api_key_secret.removeprefix("0x")
        try:
            seed = bytes.fromhex(secret)
        except ValueError as exc:
            raise PerplApiError("PERPL_API_KEY_SECRET must be a hex-encoded 32-byte Ed25519 seed") from exc
        if len(seed) != 32:
            raise PerplApiError(f"PERPL_API_KEY_SECRET must be 32 bytes, got {len(seed)}")

        signature = SigningKey(seed).sign(canonical.encode("utf-8")).signature
        return self._b64url(signature)

    def _collateral_value_from_fields(self, payload: Dict[str, Any], fields: Iterable[str]) -> Optional[Decimal]:
        raw = self._decimal_from_fields(payload, fields)
        if raw is None:
            return None
        return self._scale_down(raw, COLLATERAL_DECIMALS)

    def _decimal_from_fields(self, payload: Dict[str, Any], fields: Iterable[str]) -> Optional[Decimal]:
        return self._to_decimal(self._first_present(payload, fields))

    def _int_from_fields(self, payload: Dict[str, Any], fields: Iterable[str]) -> Optional[int]:
        return self._to_int(self._first_present(payload, fields))

    def _first_present(self, payload: Dict[str, Any], fields: Iterable[str]) -> Optional[Any]:
        for field in fields:
            value = payload.get(field)
            if value is not None and value != "":
                return value
        return None

    def _to_decimal(self, value: Any) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(str(value))
        except ValueError:
            return None

    def _scale_down(self, raw: Decimal, decimals: int) -> Decimal:
        return raw / (Decimal(10) ** decimals)

    def _normalize_address(self, address: Optional[str]) -> str:
        return (address or "").strip().lower()

    def _b64url(self, payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    def _payload_hash(self, kind: str, payload: Dict[str, Any], timestamp: datetime) -> str:
        digest_payload = {
            "protocol": PROTOCOL_SLUG,
            "kind": kind,
            "payload": payload,
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"perpl:{digest}"
