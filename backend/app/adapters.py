from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import base64
import logging
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Literal, Optional, Protocol, Set
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from .db_models import SourceORM

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawPosition:
    """Provider-neutral position shape returned by balance adapters."""

    source_id: str
    chain_id: Optional[str]
    asset_id: str
    asset_symbol: str
    asset_name: str
    decimals: Optional[int]
    contract_address: Optional[str]
    external_ids_json: str
    quantity: Decimal
    price_usd: Decimal
    value_usd: Decimal
    provider: str
    raw_payload_hash: Optional[str]


@dataclass(frozen=True)
class BalanceFetchResult:
    positions: List[RawPosition]
    status: Literal["ok", "warning"] = "ok"
    status_message: Optional[str] = None


class BalanceAdapter(Protocol):
    """Interface future Moralis, OKX, Solana, and stock adapters can implement."""

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> BalanceFetchResult:
        """Return normalized positions for one source at a specific snapshot time."""


class MoralisApiError(RuntimeError):
    """Raised when Moralis returns an error or an unexpected response."""


class OkxApiError(RuntimeError):
    """Raised when OKX returns an error or an unexpected response."""


class NoopBalanceAdapter:
    """Adapter used when a real provider is not configured yet."""

    def __init__(self, status_message: str) -> None:
        self.status_message = status_message

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> BalanceFetchResult:
        return BalanceFetchResult(
            positions=[],
            status="warning",
            status_message=self.status_message,
        )


@dataclass(frozen=True)
class MoralisEvmChainConfig:
    local_chain_id: str
    display_name: str
    moralis_chain: str
    native_symbol: str
    native_asset_id: str


MORALIS_EVM_CHAIN_CONFIGS: List[MoralisEvmChainConfig] = [
    MoralisEvmChainConfig("monad", "Monad", "0x8f", "MON", "monad-mon"),
    MoralisEvmChainConfig("ethereum", "Ethereum", "eth", "ETH", "ethereum-eth"),
]


class MoralisEvmBalanceAdapter:
    """Fetch live EVM wallet token positions from Moralis across supported chains."""

    BASE_URL = "https://deep-index.moralis.io/api/v2.2"
    TOKEN_PAGE_LIMIT = 50

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 20,
        chain_configs: Optional[Iterable[MoralisEvmChainConfig]] = None,
        hidden_contract_addresses: Optional[Dict[str, Iterable[str]]] = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.chain_configs = list(chain_configs or MORALIS_EVM_CHAIN_CONFIGS)
        self.hidden_contract_addresses: Dict[str, Set[str]] = {
            chain_id: {address.lower() for address in addresses}
            for chain_id, addresses in (hidden_contract_addresses or {}).items()
        }

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> BalanceFetchResult:
        if source.wallet is None:
            return BalanceFetchResult(
                positions=[],
                status="warning",
                status_message="Moralis sync skipped source without wallet details",
            )

        address = source.wallet.address
        live_positions: List[RawPosition] = []
        status: Literal["ok", "warning"] = "ok"
        status_messages: List[str] = []

        for chain_config in self.chain_configs:
            token_payloads = self._get_wallet_tokens(address, chain_config)
            all_chain_positions = [
                self._position_from_token(source, token_payload, timestamp, chain_config)
                for token_payload in token_payloads
                if self._has_nonzero_balance(token_payload)
            ]
            hidden_addresses = self.hidden_contract_addresses.get(chain_config.local_chain_id, set())
            chain_positions = [
                position
                for position in all_chain_positions
                if position.contract_address not in hidden_addresses
            ]
            hidden_count = len(all_chain_positions) - len(chain_positions)
            live_positions.extend(chain_positions)

            calculated_net_worth = sum((position.value_usd for position in all_chain_positions), Decimal("0"))
            chain_status, net_worth_message = self._check_net_worth(
                address,
                calculated_net_worth,
                chain_config,
            )
            if chain_status == "warning":
                status = "warning"
            hidden_message = ""
            if hidden_count:
                hidden_message = f" Hid {hidden_count} protocol receipt/debt token positions from spot holdings."
            status_messages.append(
                f"Moralis {chain_config.display_name} sync wrote {len(chain_positions)} live positions. "
                f"{net_worth_message}{hidden_message}"
            )

        return BalanceFetchResult(
            positions=live_positions,
            status=status,
            status_message=" ".join(status_messages),
        )

    def _request_json(self, path: str, params: Dict[str, str]) -> Dict[str, object]:
        query_string = urlencode(params)
        url = f"{self.BASE_URL}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        def build_request() -> Request:
            return Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PortfolioTracker/0.4 (+local)",
                    "X-API-Key": self.api_key,
                },
            )

        last_error: Optional[Exception] = None
        transient_errors = (
            URLError,
            http.client.IncompleteRead,
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            json.JSONDecodeError,
        )

        for attempt in range(1, 6):
            request = build_request()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 5:
                    last_error = exc
                    logger.warning(
                        "Retrying Moralis request after transient HTTP error attempt=%d/5 path=%s error=%s",
                        attempt,
                        path,
                        f"HTTP {exc.code}: {detail[:120]}",
                    )
                    time.sleep(min(3.0, 0.5 * (2 ** (attempt - 1))))
                    continue
                raise MoralisApiError(f"Moralis HTTP {exc.code}: {detail[:300]}") from exc
            except transient_errors as exc:
                last_error = exc
                if attempt >= 5:
                    break
                logger.warning(
                    "Retrying Moralis request after transient read failure attempt=%d/5 path=%s error=%s",
                    attempt,
                    path,
                    exc,
                )
                time.sleep(min(3.0, 0.5 * (2 ** (attempt - 1))))

        raise MoralisApiError(f"Moralis request failed after retries: {last_error}") from last_error

    def _get_wallet_tokens(self, address: str, chain_config: MoralisEvmChainConfig) -> List[Dict[str, object]]:
        tokens: List[Dict[str, object]] = []
        cursor: Optional[str] = None

        while True:
            params = {
                "chain": chain_config.moralis_chain,
                "exclude_native": "false",
                "exclude_spam": "false",
                "limit": str(self.TOKEN_PAGE_LIMIT),
            }
            if cursor:
                params["cursor"] = cursor

            payload = self._request_json(f"/wallets/{address}/tokens", params)
            result = payload.get("result")
            if not isinstance(result, list):
                raise MoralisApiError("Moralis token response did not include a result list")

            tokens.extend(item for item in result if isinstance(item, dict))
            cursor_value = payload.get("cursor")
            cursor = str(cursor_value) if cursor_value else None
            if not cursor:
                return tokens

    def _check_net_worth(
        self,
        address: str,
        calculated_net_worth: Decimal,
        chain_config: MoralisEvmChainConfig,
    ) -> tuple[Literal["ok", "warning"], str]:
        payload = self._request_json(
            f"/wallets/{address}/net-worth",
            {
                "chains": chain_config.moralis_chain,
                "exclude_spam": "false",
            },
        )
        moralis_net_worth = self._decimal_from_any(payload.get("total_networth_usd"))
        difference = abs(calculated_net_worth - moralis_net_worth)
        tolerance = max(Decimal("0.05"), abs(moralis_net_worth) * Decimal("0.001"))

        message = (
            f"{chain_config.display_name} net worth check: calculated ${calculated_net_worth:.2f}, "
            f"Moralis ${moralis_net_worth:.2f}, diff ${difference:.2f}."
        )
        if difference > tolerance:
            return "warning", message
        return "ok", message

    def _position_from_token(
        self,
        source: SourceORM,
        payload: Dict[str, object],
        timestamp: datetime,
        chain_config: MoralisEvmChainConfig,
    ) -> RawPosition:
        symbol = str(payload.get("symbol") or "UNKNOWN")
        name = str(payload.get("name") or symbol)
        decimals = self._int_from_any(payload.get("decimals"))
        contract_address = self._contract_address(payload)
        quantity = self._token_quantity(payload, decimals)
        price_usd = self._decimal_from_any(payload.get("usd_price"))
        value_usd = quantity * price_usd
        asset_id = self._asset_id(chain_config, symbol, contract_address)
        external_ids_json = json.dumps(
            {
                "moralis_chain": chain_config.moralis_chain,
                "moralis_token_address": contract_address,
                "native_token": bool(payload.get("native_token")),
                "possible_spam": bool(payload.get("possible_spam")),
                "verified_contract": bool(payload.get("verified_contract")),
                "logo": payload.get("logo"),
                "thumbnail": payload.get("thumbnail"),
            },
            sort_keys=True,
        )

        return RawPosition(
            source_id=source.id,
            chain_id=chain_config.local_chain_id,
            asset_id=asset_id,
            asset_symbol=symbol,
            asset_name=name,
            decimals=decimals,
            contract_address=contract_address,
            external_ids_json=external_ids_json,
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            provider="Moralis",
            raw_payload_hash=self._payload_hash(payload, timestamp),
        )

    def _asset_id(
        self,
        chain_config: MoralisEvmChainConfig,
        symbol: str,
        contract_address: Optional[str],
    ) -> str:
        if contract_address is None:
            return chain_config.native_asset_id
        return f"{chain_config.local_chain_id}-{contract_address.lower()}"

    def _contract_address(self, payload: Dict[str, object]) -> Optional[str]:
        if bool(payload.get("native_token")):
            return None
        token_address = payload.get("token_address")
        return str(token_address).lower() if token_address else None

    def _token_quantity(self, payload: Dict[str, object], decimals: Optional[int]) -> Decimal:
        balance_formatted = payload.get("balance_formatted")
        if balance_formatted is not None:
            return self._decimal_from_any(balance_formatted)

        balance = self._decimal_from_any(payload.get("balance"))
        if decimals is None:
            return balance
        return balance / (Decimal(10) ** decimals)

    def _has_nonzero_balance(self, payload: Dict[str, object]) -> bool:
        decimals = self._int_from_any(payload.get("decimals"))
        return self._token_quantity(payload, decimals) != Decimal("0")

    def _decimal_from_any(self, value: object) -> Decimal:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))

    def _int_from_any(self, value: object) -> Optional[int]:
        if value is None or value == "":
            return None
        return int(value)

    def _payload_hash(self, payload: Dict[str, object], timestamp: datetime) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{timestamp.isoformat()}:{encoded}".encode("utf-8")).hexdigest()
        return f"moralis:{digest}"


class OkxBalanceAdapter:
    """Fetch live OKX exchange balances and normalize them as portfolio positions."""

    BASE_URL = "https://www.okx.com"
    STABLECOIN_PRICES = {
        "DAI": Decimal("1"),
        "FDUSD": Decimal("1"),
        "TUSD": Decimal("1"),
        "USD": Decimal("1"),
        "USDC": Decimal("1"),
        "USDG": Decimal("1"),
        "USDT": Decimal("1"),
    }

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        timeout_seconds: int = 20,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.timeout_seconds = timeout_seconds
        self._price_cache: Dict[str, Optional[Decimal]] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> BalanceFetchResult:
        if source.exchange_account is None:
            return BalanceFetchResult(
                positions=[],
                status="warning",
                status_message="OKX sync skipped source without exchange account details",
            )

        trading_payload = self._request_json("GET", "/api/v5/account/balance")
        funding_payload = self._request_json("GET", "/api/v5/asset/balances")
        positions, unpriced_currencies = self._positions_from_balances(
            source,
            timestamp,
            trading_payload,
            funding_payload,
        )

        status: Literal["ok", "warning"] = "ok"
        warning = ""
        if unpriced_currencies:
            status = "warning"
            warning = (
                " Missing USD price for funding balances: "
                f"{', '.join(sorted(unpriced_currencies))}."
            )

        status_message = (
            f"OKX sync wrote {len(positions)} live balances "
            f"from trading and funding accounts.{warning}"
        )
        return BalanceFetchResult(
            positions=positions,
            status=status,
            status_message=status_message,
        )

    def _positions_from_balances(
        self,
        source: SourceORM,
        timestamp: datetime,
        trading_payload: Dict[str, object],
        funding_payload: Dict[str, object],
    ) -> tuple[List[RawPosition], set[str]]:
        balances_by_currency: Dict[str, Dict[str, object]] = {}
        unpriced_currencies: set[str] = set()

        for detail in self._trading_details(trading_payload):
            currency = str(detail.get("ccy") or "").upper()
            if not currency:
                continue
            quantity = self._decimal_from_any(detail.get("eq"))
            value_usd = self._decimal_from_any(detail.get("eqUsd"))
            self._merge_currency_balance(
                balances_by_currency,
                currency,
                quantity,
                value_usd,
                "trading",
                detail,
            )

        for detail in self._funding_details(funding_payload):
            currency = str(detail.get("ccy") or "").upper()
            if not currency:
                continue
            quantity = self._decimal_from_any(detail.get("bal"))
            price_usd = self._price_usd_for_currency(currency)
            if quantity != Decimal("0") and price_usd is None:
                unpriced_currencies.add(currency)
                value_usd = Decimal("0")
            else:
                value_usd = quantity * (price_usd or Decimal("0"))
            self._merge_currency_balance(
                balances_by_currency,
                currency,
                quantity,
                value_usd,
                "funding",
                detail,
            )

        positions = [
            self._position_from_currency_balance(source, timestamp, currency, balance)
            for currency, balance in balances_by_currency.items()
            if balance["quantity"] != Decimal("0") or balance["value_usd"] != Decimal("0")
        ]
        positions.sort(key=lambda position: abs(position.value_usd), reverse=True)
        return positions, unpriced_currencies

    def _merge_currency_balance(
        self,
        balances_by_currency: Dict[str, Dict[str, object]],
        currency: str,
        quantity: Decimal,
        value_usd: Decimal,
        account_type: str,
        raw_detail: Dict[str, object],
    ) -> None:
        existing = balances_by_currency.setdefault(
            currency,
            {
                "quantity": Decimal("0"),
                "value_usd": Decimal("0"),
                "accounts": [],
                "raw_details": [],
            },
        )
        existing["quantity"] = existing["quantity"] + quantity
        existing["value_usd"] = existing["value_usd"] + value_usd
        existing["accounts"].append(
            {
                "account_type": account_type,
                "quantity": str(quantity),
                "value_usd": str(value_usd),
            }
        )
        existing["raw_details"].append({"account_type": account_type, "detail": raw_detail})

    def _position_from_currency_balance(
        self,
        source: SourceORM,
        timestamp: datetime,
        currency: str,
        balance: Dict[str, object],
    ) -> RawPosition:
        quantity = balance["quantity"]
        value_usd = balance["value_usd"]
        if not isinstance(quantity, Decimal) or not isinstance(value_usd, Decimal):
            raise OkxApiError("OKX balance aggregation produced invalid numeric values")

        price_usd = value_usd / quantity if quantity != Decimal("0") else Decimal("0")
        external_ids_json = json.dumps(
            {
                "okx_currency": currency,
                "okx_accounts": balance["accounts"],
            },
            sort_keys=True,
        )

        return RawPosition(
            source_id=source.id,
            chain_id=None,
            asset_id=f"okx-{currency.lower()}",
            asset_symbol=currency,
            asset_name=currency,
            decimals=None,
            contract_address=None,
            external_ids_json=external_ids_json,
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            provider="OKX",
            raw_payload_hash=self._payload_hash(balance, timestamp),
        )

    def _request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        params: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        request_path = path
        if params:
            request_path = f"{path}?{urlencode(params)}"
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        timestamp = self._timestamp()
        request = Request(
            f"{self.BASE_URL}{request_path}",
            data=body_text.encode("utf-8") if body_text else None,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "PortfolioTracker/0.5 (+local)",
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": self._sign(timestamp, method, request_path, body_text),
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.api_passphrase,
            },
        )

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise OkxApiError("OKX returned a non-object JSON response")
                    code = str(payload.get("code", "0"))
                    if code != "0":
                        message = str(payload.get("msg") or "unknown OKX API error")
                        raise OkxApiError(f"OKX API error {code}: {message}")
                    return payload
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise OkxApiError(f"OKX HTTP {exc.code}: {detail[:300]}") from exc
            except OkxApiError:
                raise
            except (URLError, http.client.IncompleteRead) as exc:
                last_error = exc
                if attempt == 2:
                    break
            except json.JSONDecodeError as exc:
                raise OkxApiError("OKX returned invalid JSON") from exc

        raise OkxApiError(f"OKX request failed after retries: {last_error}") from last_error

    def _request_public_json(self, path: str, params: Dict[str, str]) -> Dict[str, object]:
        url = f"{self.BASE_URL}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PortfolioTracker/0.5 (+local)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, http.client.IncompleteRead, json.JSONDecodeError):
            return {}

        return payload if isinstance(payload, dict) else {}

    def _price_usd_for_currency(self, currency: str) -> Optional[Decimal]:
        if currency in self._price_cache:
            return self._price_cache[currency]
        if currency in self.STABLECOIN_PRICES:
            self._price_cache[currency] = self.STABLECOIN_PRICES[currency]
            return self._price_cache[currency]

        for quote_currency in ("USDT", "USDC", "USD"):
            payload = self._request_public_json(
                "/api/v5/market/ticker",
                {"instId": f"{currency}-{quote_currency}"},
            )
            data = payload.get("data")
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                continue
            last_price = self._decimal_from_any(data[0].get("last"))
            quote_price = self.STABLECOIN_PRICES.get(quote_currency)
            if last_price > Decimal("0") and quote_price is not None:
                self._price_cache[currency] = last_price * quote_price
                return self._price_cache[currency]

        self._price_cache[currency] = None
        return None

    def _trading_details(self, payload: Dict[str, object]) -> List[Dict[str, object]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise OkxApiError("OKX trading balance response did not include data")

        details: List[Dict[str, object]] = []
        for account in data:
            if not isinstance(account, dict):
                continue
            account_details = account.get("details")
            if isinstance(account_details, list):
                details.extend(item for item in account_details if isinstance(item, dict))
        return details

    def _funding_details(self, payload: Dict[str, object]) -> List[Dict[str, object]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise OkxApiError("OKX funding balance response did not include data")
        return [item for item in data if isinstance(item, dict)]

    def _sign(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _decimal_from_any(self, value: object) -> Decimal:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))

    def _payload_hash(self, payload: Dict[str, object], timestamp: datetime) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{timestamp.isoformat()}:{encoded}".encode("utf-8")).hexdigest()
        return f"okx:{digest}"
