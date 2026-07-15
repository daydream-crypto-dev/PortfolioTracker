from __future__ import annotations

import hashlib
import http.client
import json
import logging
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from ..db_models import SourceORM
from ..rpc import EvmRpcClient, RpcError, call_data, decode_uint256, decode_uint256_words
from .base import DefiCategory, DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "neverland"
PROTOCOL_NAME = "Neverland"
PROTOCOL_URL = "https://neverland.money/"
LOCAL_CHAIN_ID = "monad"
MORALIS_CHAIN = "0x8f"
MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

POOL_ADDRESS = "0x80F00661b13CC5F6ccd3885bE7b4C9c67545D585"
AAVE_ORACLE_ADDRESS = "0x94bbA11004B9877d13bb5E1aE29319b6f7bDEdD4"
DUST_TOKEN_ADDRESS = "0xAD96C3dffCD6374294e2573A7fBBA96097CC8d7c"
DUST_LOCK_ADDRESS = "0xBB4738D05AD1b3Da57a4881baE62Ce9bb1eEeD6C"
ORACLE_PRICE_UNIT = Decimal("100000000")
WAD = Decimal("1000000000000000000")

BALANCE_OF_SELECTOR = "0x70a08231"
DECIMALS_SELECTOR = "0x313ce567"
GET_ASSET_PRICE_SELECTOR = "0xb3596f07"
GET_USER_ACCOUNT_DATA_SELECTOR = "0xbf92857c"
LOCKED_SELECTOR = "0xb45a3c0e"


@dataclass(frozen=True)
class NeverlandReserve:
    symbol: str
    name: str
    underlying_address: str
    n_token_address: Optional[str]
    variable_debt_token_address: Optional[str]


NEVERLAND_RESERVES: List[NeverlandReserve] = [
    NeverlandReserve(
        symbol="WMON",
        name="Wrapped MON",
        underlying_address="0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A",
        n_token_address="0xD0fd2Cf7F6CEff4F96B1161F5E995D5843326154",
        variable_debt_token_address="0x3acA285b9F57832fF55f1e6835966890845c1526",
    ),
    NeverlandReserve(
        symbol="WBTC",
        name="Wrapped BTC",
        underlying_address="0x0555E30da8f98308EdB960aa94C0Db47230d2B9c",
        n_token_address="0x34c43684293963c546b0aB6841008A4d3393B9ab",
        variable_debt_token_address="0x544a5fF071090F4eE3AD879435f4dC1C1eeC1873",
    ),
    NeverlandReserve(
        symbol="WETH",
        name="Wrapped Ether",
        underlying_address="0xEE8c0E9f1BFFb4Eb878d8f15f368A02a35481242",
        n_token_address="0x31f63Ae5a96566b93477191778606BeBDC4CA66f",
        variable_debt_token_address="0xdE6C157e43c5d9B713C635f439a93CA3BE2156B6",
    ),
    NeverlandReserve(
        symbol="AUSD",
        name="AUSD",
        underlying_address="0x00000000eFE302BEAA2b3e6e1b18d08D69a9012a",
        n_token_address="0x784999fc2Dd132a41D1Cc0F1aE9805854BaD1f2D",
        variable_debt_token_address="0x54fC077EAe1006FE3C5d01f1614802eAFCbEe57E",
    ),
    NeverlandReserve(
        symbol="USDC",
        name="USD Coin",
        underlying_address="0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
        n_token_address="0x38648958836eA88b368b4ac23b86Ad44B0fe7508",
        variable_debt_token_address="0xb26FB5e35f6527d6f878F7784EA71774595B249C",
    ),
    NeverlandReserve(
        symbol="USDT0",
        name="USDT0",
        underlying_address="0xe7cd86e13AC4309349F30B3435a9d337750fC82D",
        n_token_address="0x39F901c32b2E0d25AE8DEaa1ee115C748f8f6bDf",
        variable_debt_token_address="0xa2d753458946612376ce6e5704Ab1cc79153d272",
    ),
    NeverlandReserve(
        symbol="sMON",
        name="Staked MON",
        underlying_address="0xA3227C5969757783154C60bF0bC1944180ed81B9",
        n_token_address="0xdFC14d336aea9E49113b1356333FD374e646Bf85",
        variable_debt_token_address=None,
    ),
    NeverlandReserve(
        symbol="gMON",
        name="gMON",
        underlying_address="0x8498312A6B3CbD158bf0c93AbdCF29E6e4F55081",
        n_token_address="0x7f81779736968836582D31D36274Ed82053aD1AE",
        variable_debt_token_address=None,
    ),
    NeverlandReserve(
        symbol="shMON",
        name="shMON",
        underlying_address="0x1B68626dCa36c7fE922fD2d55E4f631d962dE19c",
        n_token_address="0xC64d73Bb8748C6fA7487ace2D0d945B6fBb2EcDe",
        variable_debt_token_address=None,
    ),
    # The docs currently list earnAUSD and nEARNAUSD at the same address. Skip
    # that receipt-token read until the deployment mapping is unambiguous.
    NeverlandReserve(
        symbol="loAZND",
        name="loAZND",
        underlying_address="0x9c82eB49B51F7Dc61e22Ff347931CA32aDc6cd90",
        n_token_address="0x293e2f01a38Fe690Eb8E570AB952b24b225113a7",
        variable_debt_token_address=None,
    ),
]

NEVERLAND_DERIVATIVE_TOKEN_ADDRESSES = {
    address.lower()
    for reserve in NEVERLAND_RESERVES
    for address in (reserve.n_token_address, reserve.variable_debt_token_address)
    if address and address.lower() != reserve.underlying_address.lower()
}


class NeverlandDefiAdapter:
    """Read Neverland Aave V3-style lending positions directly from Monad."""

    def __init__(
        self,
        rpc_url: str,
        moralis_api_key: Optional[str] = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.rpc = EvmRpcClient(rpc_url, timeout_seconds=timeout_seconds)
        self.moralis_api_key = moralis_api_key
        self._decimals_cache: Dict[str, int] = {}
        self._price_cache: Dict[str, Decimal] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message="Neverland skipped source without wallet details")

        wallet_address = source.wallet.address
        health_factor = self._health_factor(wallet_address)
        positions: List[RawDefiPosition] = []

        for reserve in NEVERLAND_RESERVES:
            try:
                positions.extend(
                    self._positions_for_reserve(
                        reserve,
                        source.id,
                        wallet_address,
                        timestamp,
                        health_factor,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Neverland reserve read failed source_id=%s reserve=%s",
                    source.id,
                    reserve.symbol,
                )
                raise RpcError(f"Neverland {reserve.symbol} read failed: {exc}") from exc

        positions.extend(self._locked_dust_positions(source.id, wallet_address, timestamp))
        message = f"Neverland sync wrote {len(positions)} DeFi positions."
        return DefiFetchResult(positions=positions, status_message=message)

    def _positions_for_reserve(
        self,
        reserve: NeverlandReserve,
        source_id: str,
        wallet_address: str,
        timestamp: datetime,
        health_factor: Optional[Decimal],
    ) -> List[RawDefiPosition]:
        decimals = self._token_decimals(reserve.underlying_address)
        price_usd = self._asset_price_usd(reserve.underlying_address)
        positions: List[RawDefiPosition] = []

        if reserve.n_token_address:
            supplied = self._token_balance(reserve.n_token_address, wallet_address, decimals)
            if supplied > 0:
                positions.append(
                    self._raw_position(
                        reserve=reserve,
                        source_id=source_id,
                        category="deposit",
                        quantity=supplied,
                        price_usd=price_usd,
                        value_usd=supplied * price_usd,
                        decimals=decimals,
                        token_address=reserve.underlying_address,
                        balance_contract=reserve.n_token_address,
                        timestamp=timestamp,
                        health_factor=health_factor,
                    )
                )

        if reserve.variable_debt_token_address:
            borrowed = self._token_balance(reserve.variable_debt_token_address, wallet_address, decimals)
            if borrowed > 0:
                positions.append(
                    self._raw_position(
                        reserve=reserve,
                        source_id=source_id,
                        category="borrow",
                        quantity=borrowed,
                        price_usd=price_usd,
                        value_usd=-(borrowed * price_usd),
                        decimals=decimals,
                        token_address=reserve.underlying_address,
                        balance_contract=reserve.variable_debt_token_address,
                        timestamp=timestamp,
                        health_factor=health_factor,
                    )
                )

        return positions

    def _locked_dust_positions(
        self,
        source_id: str,
        wallet_address: str,
        timestamp: datetime,
    ) -> List[RawDefiPosition]:
        token_ids = self._vedust_token_ids(wallet_address)
        if not token_ids:
            return []

        price_usd = self._dust_price_usd()
        positions: List[RawDefiPosition] = []
        for token_id in token_ids:
            result = self.rpc.eth_call(DUST_LOCK_ADDRESS, f"{LOCKED_SELECTOR}{self._uint_arg(token_id)}")
            words = decode_uint256_words(result)
            if len(words) < 3:
                logger.warning("DustLock locked(%s) returned fewer than 3 words", token_id)
                continue

            amount = Decimal(words[0]) / (Decimal(10) ** 18)
            if amount <= 0:
                continue

            effective_start = words[1]
            unlock_time = words[2]
            permanent = bool(words[3]) if len(words) > 3 else False
            metadata = {
                "token_id": str(token_id),
                "nft_contract": DUST_LOCK_ADDRESS,
                "effective_start": effective_start,
                "unlock_time": datetime.fromtimestamp(unlock_time, timezone.utc).isoformat().replace("+00:00", "Z")
                if unlock_time
                else None,
                "unlock_timestamp": unlock_time,
                "permanent": permanent,
            }
            positions.append(
                RawDefiPosition(
                    source_id=source_id,
                    chain_id=LOCAL_CHAIN_ID,
                    protocol_slug=PROTOCOL_SLUG,
                    protocol_name=PROTOCOL_NAME,
                    protocol_url=PROTOCOL_URL,
                    category="locked",
                    asset_id=f"monad-{DUST_TOKEN_ADDRESS.lower()}",
                    asset_symbol="DUST",
                    asset_name="Pixie Dust",
                    decimals=18,
                    contract_address=DUST_TOKEN_ADDRESS.lower(),
                    quantity=amount,
                    price_usd=price_usd,
                    value_usd=amount * price_usd,
                    apy=None,
                    health_factor=None,
                    metadata_json=json.dumps(metadata, sort_keys=True),
                    provider=PROTOCOL_NAME,
                    raw_payload_hash=self._locked_payload_hash(token_id, amount, price_usd, timestamp),
                )
            )

        return positions

    def _raw_position(
        self,
        reserve: NeverlandReserve,
        source_id: str,
        category: DefiCategory,
        quantity: Decimal,
        price_usd: Decimal,
        value_usd: Decimal,
        decimals: int,
        token_address: str,
        balance_contract: str,
        timestamp: datetime,
        health_factor: Optional[Decimal],
    ) -> RawDefiPosition:
        metadata = {
            "balance_contract": balance_contract,
            "oracle": AAVE_ORACLE_ADDRESS,
            "pool": POOL_ADDRESS,
        }
        return RawDefiPosition(
            source_id=source_id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category=category,
            asset_id=f"monad-{token_address.lower()}",
            asset_symbol=reserve.symbol,
            asset_name=reserve.name,
            decimals=decimals,
            contract_address=token_address.lower(),
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            apy=None,
            health_factor=health_factor,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(reserve, category, quantity, price_usd, timestamp),
        )

    def _token_balance(self, token_address: str, owner_address: str, decimals: int) -> Decimal:
        result = self.rpc.eth_call(token_address, call_data(BALANCE_OF_SELECTOR, [owner_address]))
        return Decimal(decode_uint256(result)) / (Decimal(10) ** decimals)

    def _token_decimals(self, token_address: str) -> int:
        normalized = token_address.lower()
        cached = self._decimals_cache.get(normalized)
        if cached is not None:
            return cached
        result = self.rpc.eth_call(token_address, call_data(DECIMALS_SELECTOR))
        decimals = decode_uint256(result)
        self._decimals_cache[normalized] = decimals
        return decimals

    def _asset_price_usd(self, token_address: str) -> Decimal:
        normalized = token_address.lower()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached
        result = self.rpc.eth_call(AAVE_ORACLE_ADDRESS, call_data(GET_ASSET_PRICE_SELECTOR, [token_address]))
        price = Decimal(decode_uint256(result)) / ORACLE_PRICE_UNIT
        self._price_cache[normalized] = price
        return price

    def _dust_price_usd(self) -> Decimal:
        normalized = DUST_TOKEN_ADDRESS.lower()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached

        payload = self._moralis_request_json(
            f"/erc20/{DUST_TOKEN_ADDRESS}/price",
            {"chain": MORALIS_CHAIN},
        )
        price = self._decimal_from_any(payload.get("usdPriceFormatted") or payload.get("usdPrice"))
        self._price_cache[normalized] = price
        return price

    def _health_factor(self, wallet_address: str) -> Optional[Decimal]:
        result = self.rpc.eth_call(POOL_ADDRESS, call_data(GET_USER_ACCOUNT_DATA_SELECTOR, [wallet_address]))
        words = decode_uint256_words(result)
        if len(words) < 6:
            return None
        raw_health_factor = words[5]
        if raw_health_factor == 0 or raw_health_factor > 10**40:
            return None
        return Decimal(raw_health_factor) / WAD

    def _vedust_token_ids(self, wallet_address: str) -> List[int]:
        if not self.moralis_api_key:
            logger.warning("Skipping veDUST locked positions because MORALIS_API_KEY is not configured")
            return []

        token_ids: List[int] = []
        cursor: Optional[str] = None
        while True:
            params = {
                "chain": MORALIS_CHAIN,
                "format": "decimal",
                "limit": "25",
            }
            if cursor:
                params["cursor"] = cursor

            payload = self._moralis_request_json(f"/{wallet_address}/nft", params)
            result = payload.get("result")
            if not isinstance(result, list):
                raise RuntimeError("Moralis NFT response did not include a result list")

            for item in result:
                if not isinstance(item, dict):
                    continue
                token_address = str(item.get("token_address") or "").lower()
                if token_address != DUST_LOCK_ADDRESS.lower():
                    continue
                token_id = item.get("token_id")
                if token_id is not None:
                    token_ids.append(int(str(token_id)))

            cursor_value = payload.get("cursor")
            cursor = str(cursor_value) if cursor_value else None
            if not cursor:
                return sorted(set(token_ids))

    def _moralis_request_json(self, path: str, params: Dict[str, str]) -> Dict[str, object]:
        if not self.moralis_api_key:
            raise RuntimeError("MORALIS_API_KEY is required for Neverland veDUST NFT reads")

        query_string = urlencode(params)
        url = f"{MORALIS_BASE_URL}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        def build_request() -> Request:
            return Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PortfolioTracker/0.5 (+local)",
                    "X-API-Key": self.moralis_api_key,
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
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 5:
                    last_error = exc
                    logger.warning(
                        "Retrying Moralis NFT request after transient HTTP error attempt=%d/5 path=%s error=%s",
                        attempt,
                        path,
                        f"HTTP {exc.code}: {detail[:120]}",
                    )
                    time.sleep(min(3.0, 0.5 * (2 ** (attempt - 1))))
                    continue
                raise RuntimeError(f"Moralis HTTP {exc.code}: {detail[:300]}") from exc
            except transient_errors as exc:
                last_error = exc
                if attempt >= 5:
                    raise RuntimeError(f"Moralis request failed after retries: {exc}") from exc
                logger.warning(
                    "Retrying Moralis NFT request after transient read failure attempt=%d/5 path=%s error=%s",
                    attempt,
                    path,
                    exc,
                )
                time.sleep(min(3.0, 0.5 * (2 ** (attempt - 1))))
        else:
            raise RuntimeError(f"Moralis request failed after retries: {last_error}") from last_error

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
        reserve: NeverlandReserve,
        category: str,
        quantity: Decimal,
        price_usd: Decimal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "reserve": reserve.symbol,
            "category": category,
            "quantity": str(quantity),
            "price_usd": str(price_usd),
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"neverland:{digest}"

    def _locked_payload_hash(
        self,
        token_id: int,
        quantity: Decimal,
        price_usd: Decimal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "category": "locked",
            "token_id": token_id,
            "quantity": str(quantity),
            "price_usd": str(price_usd),
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"neverland:{digest}"
