from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..db_models import SourceORM
from ..rpc import (
    EvmRpcClient,
    RpcError,
    call_data,
    decode_address_array,
    decode_address_words,
    decode_string,
    decode_uint256,
    decode_uint256_words,
    encode_address_arg,
)
from .base import DefiCategory, DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "aave"
PROTOCOL_NAME = "Aave"
PROTOCOL_URL = "https://aave.com/"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
WAD = Decimal("1000000000000000000")

BALANCE_OF_SELECTOR = "0x70a08231"
DECIMALS_SELECTOR = "0x313ce567"
SYMBOL_SELECTOR = "0x95d89b41"
NAME_SELECTOR = "0x06fdde03"
GET_RESERVES_LIST_SELECTOR = "0xd1946dbc"
GET_RESERVE_TOKENS_ADDRESSES_SELECTOR = "0xd2493b6c"
GET_USER_ACCOUNT_DATA_SELECTOR = "0xbf92857c"
GET_ASSET_PRICE_SELECTOR = "0xb3596f07"
BASE_CURRENCY_UNIT_SELECTOR = "0x8c89b64f"

# Official Aave V3 Ethereum deployment addresses from @bgd-labs/aave-address-book 4.44.22.
AAVE_V3_ETHEREUM_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
AAVE_V3_ETHEREUM_ORACLE = "0x54586bE62E3c3580375aE3723C145253060Ca0C2"
AAVE_V3_ETHEREUM_PROTOCOL_DATA_PROVIDER = "0x0a16f2FCC0D44FaE41cc54e079281D84A363bECD"


@dataclass(frozen=True)
class AaveDeployment:
    local_chain_id: str
    chain_name: str
    rpc_url: str
    pool_address: Optional[str]
    oracle_address: Optional[str]
    protocol_data_provider_address: Optional[str]


@dataclass(frozen=True)
class AaveReserve:
    symbol: str
    name: str
    decimals: int
    underlying_address: str
    a_token_address: str
    stable_debt_token_address: Optional[str]
    variable_debt_token_address: Optional[str]


@dataclass(frozen=True)
class AaveReserveTokens:
    underlying_address: str
    a_token_address: str
    stable_debt_token_address: Optional[str]
    variable_debt_token_address: Optional[str]


@dataclass(frozen=True)
class AaveReserveBalances:
    supplied_raw: int
    stable_debt_raw: int
    variable_debt_raw: int


class AaveV3DefiAdapter:
    """Read Aave V3 lending positions from Pool and ProtocolDataProvider contracts."""

    def __init__(
        self,
        deployment: AaveDeployment,
        timeout_seconds: int = 20,
    ) -> None:
        self.deployment = deployment
        self.rpc = EvmRpcClient(deployment.rpc_url, timeout_seconds=timeout_seconds)
        self._base_currency_unit: Optional[Decimal] = None
        self._price_cache: Dict[str, Decimal] = {}
        self._decimals_cache: Dict[str, int] = {}
        self._metadata_cache: Dict[str, tuple[str, str]] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message=f"{PROTOCOL_NAME} skipped source without wallet details")
        if not self._is_configured:
            return DefiFetchResult(
                positions=[],
                status_message=(
                    f"{PROTOCOL_NAME} {self.deployment.chain_name} addresses are not configured; "
                    f"{PROTOCOL_NAME} sync skipped."
                ),
            )

        wallet_address = source.wallet.address
        reserve_tokens = self._reserve_tokens()
        reserve_balances = self._account_reserve_balances(wallet_address, reserve_tokens)
        health_factor = self._health_factor(wallet_address)
        positions: List[RawDefiPosition] = []

        for reserve_token in reserve_tokens:
            balances = reserve_balances[reserve_token.underlying_address.lower()]
            if (
                balances.supplied_raw <= 0
                and balances.stable_debt_raw <= 0
                and balances.variable_debt_raw <= 0
            ):
                continue
            reserve_label = reserve_token.underlying_address
            try:
                reserve = self._reserve_from_tokens(reserve_token)
                reserve_label = reserve.symbol
                positions.extend(
                    self._positions_for_reserve(
                        reserve,
                        balances,
                        source.id,
                        timestamp,
                        health_factor,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Aave reserve read failed chain=%s source_id=%s reserve=%s",
                    self.deployment.local_chain_id,
                    source.id,
                    reserve_label,
                )
                raise RpcError(f"Aave {self.deployment.chain_name} {reserve_label} read failed: {exc}") from exc

        return DefiFetchResult(
            positions=positions,
            status_message=f"Aave {self.deployment.chain_name} sync wrote {len(positions)} DeFi positions.",
        )

    @property
    def _is_configured(self) -> bool:
        return bool(
            self.deployment.rpc_url
            and self.deployment.pool_address
            and self.deployment.oracle_address
            and self.deployment.protocol_data_provider_address
        )

    def _positions_for_reserve(
        self,
        reserve: AaveReserve,
        balances: AaveReserveBalances,
        source_id: str,
        timestamp: datetime,
        health_factor: Optional[Decimal],
    ) -> List[RawDefiPosition]:
        price_usd = self._asset_price_usd(reserve.underlying_address)
        positions: List[RawDefiPosition] = []

        if balances.supplied_raw > 0:
            supplied = Decimal(balances.supplied_raw) / (Decimal(10) ** reserve.decimals)
            positions.append(
                self._raw_position(
                    reserve=reserve,
                    source_id=source_id,
                    category="deposit",
                    quantity=supplied,
                    price_usd=price_usd,
                    value_usd=supplied * price_usd,
                    balance_contract=reserve.a_token_address,
                    timestamp=timestamp,
                    health_factor=health_factor,
                )
            )

        for debt_address, debt_raw in (
            (reserve.stable_debt_token_address, balances.stable_debt_raw),
            (reserve.variable_debt_token_address, balances.variable_debt_raw),
        ):
            if not debt_address or debt_raw <= 0:
                continue
            borrowed = Decimal(debt_raw) / (Decimal(10) ** reserve.decimals)
            positions.append(
                self._raw_position(
                    reserve=reserve,
                    source_id=source_id,
                    category="borrow",
                    quantity=borrowed,
                    price_usd=price_usd,
                    value_usd=-(borrowed * price_usd),
                    balance_contract=debt_address,
                    timestamp=timestamp,
                    health_factor=health_factor,
                )
            )

        return positions

    def _raw_position(
        self,
        reserve: AaveReserve,
        source_id: str,
        category: DefiCategory,
        quantity: Decimal,
        price_usd: Decimal,
        value_usd: Decimal,
        balance_contract: str,
        timestamp: datetime,
        health_factor: Optional[Decimal],
    ) -> RawDefiPosition:
        metadata = {
            "balance_contract": balance_contract.lower(),
            "pool": self.deployment.pool_address,
            "oracle": self.deployment.oracle_address,
            "protocol_data_provider": self.deployment.protocol_data_provider_address,
        }
        return RawDefiPosition(
            source_id=source_id,
            chain_id=self.deployment.local_chain_id,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category=category,
            asset_id=f"{self.deployment.local_chain_id}-{reserve.underlying_address.lower()}",
            asset_symbol=reserve.symbol,
            asset_name=reserve.name,
            decimals=reserve.decimals,
            contract_address=reserve.underlying_address.lower(),
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            apy=None,
            health_factor=health_factor,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(reserve, category, quantity, price_usd, timestamp),
        )

    def _reserve_tokens(self) -> List[AaveReserveTokens]:
        pool_address = self._required(self.deployment.pool_address, "pool")
        provider_address = self._required(self.deployment.protocol_data_provider_address, "protocol data provider")
        reserve_addresses = decode_address_array(self.rpc.eth_call(pool_address, GET_RESERVES_LIST_SELECTOR))
        calls = [
            {
                "to": provider_address,
                "data": f"{GET_RESERVE_TOKENS_ADDRESSES_SELECTOR}{encode_address_arg(underlying_address)}",
            }
            for underlying_address in reserve_addresses
        ]
        token_address_results = self.rpc.eth_batch_call(calls)

        reserves: List[AaveReserveTokens] = []
        for underlying_address, result in zip(reserve_addresses, token_address_results):
            if result is None:
                raise RpcError(f"Aave reserve token lookup failed for {underlying_address}")
            token_addresses = decode_address_words(result)
            if len(token_addresses) < 3:
                raise RpcError(f"Aave reserve token lookup returned {len(token_addresses)} addresses")
            reserves.append(
                AaveReserveTokens(
                    underlying_address=underlying_address,
                    a_token_address=token_addresses[0],
                    stable_debt_token_address=self._nonzero_address(token_addresses[1]),
                    variable_debt_token_address=self._nonzero_address(token_addresses[2]),
                )
            )
        return reserves

    def _account_reserve_balances(
        self,
        wallet_address: str,
        reserves: List[AaveReserveTokens],
    ) -> Dict[str, AaveReserveBalances]:
        calls: List[Dict[str, str]] = []
        refs: List[tuple[str, str]] = []
        for reserve in reserves:
            reserve_key = reserve.underlying_address.lower()
            calls.append({"to": reserve.a_token_address, "data": call_data(BALANCE_OF_SELECTOR, [wallet_address])})
            refs.append((reserve_key, "supplied_raw"))
            if reserve.stable_debt_token_address:
                calls.append(
                    {
                        "to": reserve.stable_debt_token_address,
                        "data": call_data(BALANCE_OF_SELECTOR, [wallet_address]),
                    }
                )
                refs.append((reserve_key, "stable_debt_raw"))
            if reserve.variable_debt_token_address:
                calls.append(
                    {
                        "to": reserve.variable_debt_token_address,
                        "data": call_data(BALANCE_OF_SELECTOR, [wallet_address]),
                    }
                )
                refs.append((reserve_key, "variable_debt_raw"))

        raw_by_reserve = {
            reserve.underlying_address.lower(): {
                "supplied_raw": 0,
                "stable_debt_raw": 0,
                "variable_debt_raw": 0,
            }
            for reserve in reserves
        }
        results = self.rpc.eth_batch_call(calls) if calls else []
        for (reserve_key, field), result in zip(refs, results):
            raw_by_reserve[reserve_key][field] = decode_uint256(result) if result else 0

        return {
            reserve_key: AaveReserveBalances(
                supplied_raw=values["supplied_raw"],
                stable_debt_raw=values["stable_debt_raw"],
                variable_debt_raw=values["variable_debt_raw"],
            )
            for reserve_key, values in raw_by_reserve.items()
        }

    def _reserve_from_tokens(self, reserve_tokens: AaveReserveTokens) -> AaveReserve:
        symbol, name = self._token_metadata(reserve_tokens.underlying_address)
        return AaveReserve(
            symbol=symbol,
            name=name,
            decimals=self._token_decimals(reserve_tokens.underlying_address),
            underlying_address=reserve_tokens.underlying_address,
            a_token_address=reserve_tokens.a_token_address,
            stable_debt_token_address=reserve_tokens.stable_debt_token_address,
            variable_debt_token_address=reserve_tokens.variable_debt_token_address,
        )

    def _reserves(self) -> List[AaveReserve]:
        return [self._reserve_from_tokens(reserve_tokens) for reserve_tokens in self._reserve_tokens()]

    def _token_balance(self, token_address: str, owner_address: str, decimals: int) -> Decimal:
        result = self.rpc.eth_call(token_address, call_data(BALANCE_OF_SELECTOR, [owner_address]))
        return Decimal(decode_uint256(result)) / (Decimal(10) ** decimals)

    def _asset_price_usd(self, token_address: str) -> Decimal:
        normalized = token_address.lower()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached
        oracle_address = self._required(self.deployment.oracle_address, "oracle")
        result = self.rpc.eth_call(oracle_address, call_data(GET_ASSET_PRICE_SELECTOR, [token_address]))
        price = Decimal(decode_uint256(result)) / self._base_unit()
        self._price_cache[normalized] = price
        return price

    def _base_unit(self) -> Decimal:
        if self._base_currency_unit is not None:
            return self._base_currency_unit
        oracle_address = self._required(self.deployment.oracle_address, "oracle")
        try:
            raw_unit = decode_uint256(self.rpc.eth_call(oracle_address, BASE_CURRENCY_UNIT_SELECTOR))
        except Exception:
            raw_unit = 100000000
        self._base_currency_unit = Decimal(raw_unit or 100000000)
        return self._base_currency_unit

    def _health_factor(self, wallet_address: str) -> Optional[Decimal]:
        pool_address = self._required(self.deployment.pool_address, "pool")
        result = self.rpc.eth_call(pool_address, call_data(GET_USER_ACCOUNT_DATA_SELECTOR, [wallet_address]))
        words = decode_uint256_words(result)
        if len(words) < 6:
            return None
        raw_health_factor = words[5]
        if raw_health_factor == 0 or raw_health_factor > 10**40:
            return None
        return Decimal(raw_health_factor) / WAD

    def _token_decimals(self, token_address: str) -> int:
        normalized = token_address.lower()
        cached = self._decimals_cache.get(normalized)
        if cached is not None:
            return cached
        result = self.rpc.eth_call(token_address, DECIMALS_SELECTOR)
        decimals = decode_uint256(result)
        self._decimals_cache[normalized] = decimals
        return decimals

    def _token_metadata(self, token_address: str) -> tuple[str, str]:
        normalized = token_address.lower()
        cached = self._metadata_cache.get(normalized)
        if cached is not None:
            return cached

        symbol = self._token_string(token_address, SYMBOL_SELECTOR) or "UNKNOWN"
        name = self._token_string(token_address, NAME_SELECTOR) or symbol
        self._metadata_cache[normalized] = (symbol, name)
        return symbol, name

    def _token_string(self, token_address: str, selector: str) -> Optional[str]:
        try:
            return decode_string(self.rpc.eth_call(token_address, selector))
        except Exception:
            return None

    def _required(self, value: Optional[str], label: str) -> str:
        if not value:
            raise RpcError(f"Aave {self.deployment.chain_name} {label} address is not configured")
        return value

    def _nonzero_address(self, address: str) -> Optional[str]:
        normalized = address.lower()
        return None if normalized == ZERO_ADDRESS else address

    def _payload_hash(
        self,
        reserve: AaveReserve,
        category: str,
        quantity: Decimal,
        price_usd: Decimal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "chain": self.deployment.local_chain_id,
            "reserve": reserve.symbol,
            "category": category,
            "quantity": str(quantity),
            "price_usd": str(price_usd),
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"aave:{digest}"
