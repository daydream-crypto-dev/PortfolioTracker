from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from ..db_models import SourceORM
from ..rpc import EvmRpcClient, call_data, decode_uint256
from .base import DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "leverup"
PROTOCOL_NAME = "LeverUp"
PROTOCOL_URL = "https://leverup.xyz/"
LOCAL_CHAIN_ID = "monad"
MORALIS_CHAIN = "0x8f"
MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

LVMON_TOKEN_ADDRESS = "0x91b81bfbe3A747230F0529Aa28d8b2Bc898E6D56"
SLVMON_TOKEN_ADDRESS = "0x61b29efef2e6f866ba4aaefdb87d2837c6a22b9c"

BALANCE_OF_SELECTOR = "0x70a08231"
DECIMALS_SELECTOR = "0x313ce567"
CONVERT_TO_ASSETS_SELECTOR = "0x07a2d13a"

LEVERUP_DERIVATIVE_TOKEN_ADDRESSES = {SLVMON_TOKEN_ADDRESS.lower()}


class LeverUpDefiAdapter:
    """Read LeverUp sLVMON staking receipt balances and value them as LVMON."""

    def __init__(
        self,
        rpc_url: str,
        moralis_api_key: Optional[str] = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.rpc = EvmRpcClient(rpc_url, timeout_seconds=timeout_seconds)
        self.moralis_api_key = moralis_api_key
        self._price_cache: Dict[str, Decimal] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message="LeverUp skipped source without wallet details")

        wallet_address = source.wallet.address
        share_decimals = self._token_decimals(SLVMON_TOKEN_ADDRESS)
        asset_decimals = self._token_decimals(LVMON_TOKEN_ADDRESS)
        share_balance_raw = self._token_balance_raw(SLVMON_TOKEN_ADDRESS, wallet_address)
        if share_balance_raw == 0:
            return DefiFetchResult(positions=[], status_message="LeverUp sync found no sLVMON staking position.")

        underlying_raw = self._convert_to_assets(share_balance_raw)
        quantity = Decimal(underlying_raw) / (Decimal(10) ** asset_decimals)
        share_quantity = Decimal(share_balance_raw) / (Decimal(10) ** share_decimals)
        price_usd = self._lvmon_price_usd()

        metadata = {
            "receipt_token": SLVMON_TOKEN_ADDRESS,
            "receipt_symbol": "sLVMON",
            "receipt_quantity": str(share_quantity),
            "underlying_token": LVMON_TOKEN_ADDRESS,
        }
        position = RawDefiPosition(
            source_id=source.id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category="staked",
            asset_id=f"monad-{LVMON_TOKEN_ADDRESS.lower()}",
            asset_symbol="LVMON",
            asset_name="LeverUp MON",
            decimals=asset_decimals,
            contract_address=LVMON_TOKEN_ADDRESS.lower(),
            quantity=quantity,
            price_usd=price_usd,
            value_usd=quantity * price_usd,
            apy=None,
            health_factor=None,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(share_balance_raw, underlying_raw, price_usd, timestamp),
        )
        return DefiFetchResult(
            positions=[position],
            status_message="LeverUp sync wrote 1 sLVMON staking position.",
        )

    def _token_balance_raw(self, token_address: str, owner_address: str) -> int:
        result = self.rpc.eth_call(token_address, call_data(BALANCE_OF_SELECTOR, [owner_address]))
        return decode_uint256(result)

    def _token_decimals(self, token_address: str) -> int:
        result = self.rpc.eth_call(token_address, call_data(DECIMALS_SELECTOR))
        return decode_uint256(result)

    def _convert_to_assets(self, shares_raw: int) -> int:
        result = self.rpc.eth_call(
            SLVMON_TOKEN_ADDRESS,
            f"{CONVERT_TO_ASSETS_SELECTOR}{self._uint_arg(shares_raw)}",
        )
        return decode_uint256(result)

    def _lvmon_price_usd(self) -> Decimal:
        normalized = LVMON_TOKEN_ADDRESS.lower()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached

        payload = self._moralis_request_json(
            f"/erc20/{LVMON_TOKEN_ADDRESS}/price",
            {"chain": MORALIS_CHAIN},
        )
        price = self._decimal_from_any(payload.get("usdPriceFormatted") or payload.get("usdPrice"))
        self._price_cache[normalized] = price
        return price

    def _moralis_request_json(self, path: str, params: Dict[str, str]) -> Dict[str, object]:
        if not self.moralis_api_key:
            raise RuntimeError("MORALIS_API_KEY is required for LeverUp LVMON price reads")

        query_string = urlencode(params)
        url = f"{MORALIS_BASE_URL}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PortfolioTracker/0.5 (+local)",
                "X-API-Key": self.moralis_api_key,
            },
        )

        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Moralis HTTP {exc.code}: {detail[:300]}") from exc
        except URLError as exc:
            raise RuntimeError(f"Moralis request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Moralis returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Moralis returned a non-object JSON body")
        return payload

    def _decimal_from_any(self, value: object) -> Decimal:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))

    def _uint_arg(self, value: int) -> str:
        return hex(value)[2:].rjust(64, "0")

    def _payload_hash(
        self,
        shares_raw: int,
        underlying_raw: int,
        price_usd: Decimal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "category": "staked",
            "shares_raw": str(shares_raw),
            "underlying_raw": str(underlying_raw),
            "price_usd": str(price_usd),
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"leverup:{digest}"
