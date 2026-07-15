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
    decode_address_words,
    decode_string,
    decode_uint256,
    decode_uint256_words,
    encode_address_arg,
    encode_bool_arg,
    encode_uint256_arg,
)
from .base import DefiCategory, DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "curvance"
PROTOCOL_NAME = "Curvance"
PROTOCOL_URL = "https://curvance.com/"
LOCAL_CHAIN_ID = "monad"
PROTOCOL_READER_ADDRESS = "0x55C7c1fe1DACB014aD3b21951728B5E580662268"
WAD = Decimal("1000000000000000000")

ASSET_SELECTOR = "0x38d52e0f"
BALANCE_OF_SELECTOR = "0x70a08231"
COLLATERAL_POSTED_SELECTOR = "0xe28d591d"
CONVERT_TO_ASSETS_SELECTOR = "0x07a2d13a"
DEBT_BALANCE_SELECTOR = "0x11005b07"
DECIMALS_SELECTOR = "0x313ce567"
GET_PRICE_SELECTOR = "0x5ae69163"
IS_BORROWABLE_SELECTOR = "0x45d7b97a"
NAME_SELECTOR = "0x06fdde03"
SYMBOL_SELECTOR = "0x95d89b41"


@dataclass(frozen=True)
class CurvanceCToken:
    market_name: str
    symbol: str
    address: str


@dataclass(frozen=True)
class CurvanceAccountCTokenState:
    share_balance_raw: int
    collateral_shares_raw: int
    borrowable: bool
    debt_raw: int


CURVANCE_CTOKENS: List[CurvanceCToken] = [
    CurvanceCToken("WMON | AUSD", "cWMON", "0xE01d426B589c7834a5F6B20D7e992A705d3c22ED"),
    CurvanceCToken("WMON | AUSD", "cAUSD", "0x6E182EB501800C555bd5E662E6D350D627F504D8"),
    CurvanceCToken("WMON | USDC", "cWMON", "0x1e240E30E51491546deC3aF16B0b4EAC8Dd110D4"),
    CurvanceCToken("WMON | USDC", "cUSDC", "0x8EE9FC28B8Da872c38A496e9dDB9700bb7261774"),
    CurvanceCToken("WBTC | USDC", "cWBTC", "0x3D2Ff9F862D89Ba526a0fC166bD56ABe04EF28d5"),
    CurvanceCToken("WBTC | USDC", "cUSDC", "0x7C9d4f1695C6282Da5e5509Aa51fC9fb417C6f1d"),
    CurvanceCToken("WETH | USDC", "cWETH", "0x8Af00fbbb2601A8F7636EabbF6243B30BEA47D50"),
    CurvanceCToken("WETH | USDC", "cUSDC", "0x21aDBb60a5fB909e7F1fB48aACC4569615CD97b5"),
    CurvanceCToken("ezETH | WETH", "cezETH", "0x20f1A13BfbF85a22Aa59D189861790981372220b"),
    CurvanceCToken("ezETH | WETH", "cWETH", "0xa206D51C02c0202a2Eed8E6A757b49Ab13930227"),
    CurvanceCToken("sAUSD | AUSD", "csAUSD", "0x84C5aF20b58818631164Bb7d798E457fcFACD9Ac"),
    CurvanceCToken("sAUSD | AUSD", "cAUSD", "0xfD493ce1A0ae986e09d17004B7E748817a47d73c"),
    CurvanceCToken("earnAUSD | AUSD", "cearnAUSD", "0x852FF1EC21D63b405eC431e04AE3AC760e29263D"),
    CurvanceCToken("earnAUSD | AUSD", "cAUSD", "0xAd4AA2a713fB86FBb6b60dE2aF9E32a11DB6Abf2"),
    CurvanceCToken("syzUSD | AUSD", "csyzUSD", "0x7EdA3cb060Ff7B650eB227971dbfEBD3513b11D5"),
    CurvanceCToken("syzUSD | AUSD", "cAUSD", "0x8E94704607E857eB3E10Bd21D90bf8C1Ecba0452"),
    CurvanceCToken("YZM | AUSD", "cYZM", "0x8626B8f4F64CAeee9549Af8ebbFA591A7425e5ba"),
    CurvanceCToken("YZM | AUSD", "cAUSD", "0xcdc9D2c4EaD8f2A9FD3D6F5a00bA4e6001ab7898"),
    CurvanceCToken("wsrUSD | AUSD", "cwsrUSD", "0x251B67Ae7e90fDc6a7B080Ee601913A8B2746A28"),
    CurvanceCToken("wsrUSD | AUSD", "cAUSD", "0x88e0994E8130EF72bf614CBBcF722839B167c8d1"),
    CurvanceCToken("vUSD | AUSD", "cvUSD", "0x42369AFe4bA4225b800b8024Acc5F14f42A3836C"),
    CurvanceCToken("vUSD | AUSD", "cAUSD", "0x4806902Ec0320e5334c2B2679FFB58C830348F1c"),
    CurvanceCToken("eBTC | WBTC", "ceBTC", "0x2840772E14fFbe337aB966727B7D1Dd09BDc76E4"),
    CurvanceCToken("eBTC | WBTC", "cWBTC", "0xdB3e888c3b50771821226d30Ab6eC14eB5ba85bA"),
    CurvanceCToken("savUSD | USDC", "csavUSD", "0x3afB9A1cC0d2b0D62502B84B070601fF0DC84363"),
    CurvanceCToken("savUSD | USDC", "cUSDC", "0x9891178A1178E4C740Fa61Fd6e30A9D92D897590"),
    CurvanceCToken("savUSD | AUSD", "csavUSD", "0x2552232caBd544b67eEa900A951346D3272c7c2f"),
    CurvanceCToken("savUSD | AUSD", "cAUSD", "0xD1BFEA1728ffe98F515f26082fACfcc3341691D4"),
    CurvanceCToken("muBOND | AUSD", "cmuBOND", "0x92EE4b4d33Dc61bd93a88601F29131B08aCedBF1"),
    CurvanceCToken("muBOND | AUSD", "cAUSD", "0x2B4e0232F46E6DB4af35474c140B968EeFCB09Ec"),
    CurvanceCToken("loAZND | AUSD", "cloAZND", "0xf7a6AB4aF86966C141D3C5633DF658E5CDb0a735"),
    CurvanceCToken("loAZND | AUSD", "cAUSD", "0xDaDbB2D8f9802DC458F5D7F133D053087Ba8983d"),
    CurvanceCToken("shMON | WMON", "cshMON", "0x926C101Cf0a3dE8725Eb24a93E980f9FE34d6230"),
    CurvanceCToken("shMON | WMON", "cWMON", "0x0fcEd51b526BfA5619F83d97b54a57e3327eB183"),
    CurvanceCToken("aprMON | WMON", "caprMON", "0xD9E2025b907E95EcC963A5018f56B87575B4aB26"),
    CurvanceCToken("aprMON | WMON", "cWMON", "0xF32B334042DC1EB9732454cc9bc1a06205d184f2"),
    CurvanceCToken("sMON | WMON", "csMON", "0x494876051B0E85dCe5ecd5822B1aD39b9660c928"),
    CurvanceCToken("sMON | WMON", "cWMON", "0xebE45A6ceA7760a71D8e0fa5a0AE80a75320D708"),
    CurvanceCToken("gMON | WMON", "cgMON", "0x5ca6966543c0786f547446234492D2F11C82f11f"),
    CurvanceCToken("gMON | WMON", "cWMON", "0xf473568b26B8C5aadCa9fbC0eA17E1728d5ec925"),
]

CURVANCE_CTOKEN_ADDRESSES = {token.address.lower() for token in CURVANCE_CTOKENS}


class CurvanceDefiAdapter:
    """Read Curvance Monad cToken deposits and borrows without paid DeFi APIs."""

    def __init__(
        self,
        rpc_url: str,
        protocol_reader_address: str = PROTOCOL_READER_ADDRESS,
        timeout_seconds: int = 20,
    ) -> None:
        self.rpc = EvmRpcClient(rpc_url, timeout_seconds=timeout_seconds)
        self.protocol_reader_address = protocol_reader_address
        self._asset_cache: Dict[str, str] = {}
        self._borrowable_cache: Dict[str, bool] = {}
        self._decimals_cache: Dict[str, int] = {}
        self._metadata_cache: Dict[str, tuple[str, str]] = {}
        self._price_cache: Dict[str, Decimal] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message="Curvance skipped source without wallet details")

        wallet_address = source.wallet.address
        positions: List[RawDefiPosition] = []
        account_states = self._account_ctoken_states(wallet_address)
        for ctoken in CURVANCE_CTOKENS:
            try:
                positions.extend(
                    self._positions_for_ctoken(
                        ctoken,
                        account_states[ctoken.address.lower()],
                        source.id,
                        timestamp,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Curvance cToken read failed source_id=%s ctoken=%s market=%s",
                    source.id,
                    ctoken.symbol,
                    ctoken.market_name,
                )
                raise RpcError(f"Curvance {ctoken.symbol} read failed: {exc}") from exc

        return DefiFetchResult(
            positions=positions,
            status_message=f"Curvance sync wrote {len(positions)} DeFi positions.",
        )

    def _positions_for_ctoken(
        self,
        ctoken: CurvanceCToken,
        account_state: CurvanceAccountCTokenState,
        source_id: str,
        timestamp: datetime,
    ) -> List[RawDefiPosition]:
        total_shares_raw = max(account_state.share_balance_raw, account_state.collateral_shares_raw)
        if total_shares_raw <= 0 and account_state.debt_raw <= 0:
            return []

        underlying_address = self._underlying_asset(ctoken.address)
        decimals = self._token_decimals(underlying_address)
        symbol, name = self._token_metadata(underlying_address, ctoken.symbol)
        price_usd = self._asset_price_usd(underlying_address)
        positions: List[RawDefiPosition] = []

        if total_shares_raw > 0:
            assets_raw = self._convert_to_assets(ctoken.address, total_shares_raw)
            quantity = Decimal(assets_raw) / (Decimal(10) ** decimals)
            if quantity > 0:
                positions.append(
                    self._raw_position(
                        ctoken=ctoken,
                        source_id=source_id,
                        category="deposit",
                        symbol=symbol,
                        name=name,
                        decimals=decimals,
                        token_address=underlying_address,
                        quantity=quantity,
                        price_usd=price_usd,
                        value_usd=quantity * price_usd,
                        timestamp=timestamp,
                        extra_metadata={
                            "share_balance_raw": str(account_state.share_balance_raw),
                            "collateral_shares_raw": str(account_state.collateral_shares_raw),
                        },
                    )
                )

        if account_state.debt_raw > 0:
            quantity = Decimal(account_state.debt_raw) / (Decimal(10) ** decimals)
            positions.append(
                self._raw_position(
                    ctoken=ctoken,
                    source_id=source_id,
                    category="borrow",
                    symbol=symbol,
                    name=name,
                    decimals=decimals,
                    token_address=underlying_address,
                    quantity=quantity,
                    price_usd=price_usd,
                    value_usd=-(quantity * price_usd),
                    timestamp=timestamp,
                    extra_metadata={"debt_raw": str(account_state.debt_raw)},
                )
            )

        return positions

    def _account_ctoken_states(self, wallet_address: str) -> Dict[str, CurvanceAccountCTokenState]:
        account_calls = []
        for ctoken in CURVANCE_CTOKENS:
            account_calls.extend(
                [
                    {"to": ctoken.address, "data": call_data(BALANCE_OF_SELECTOR, [wallet_address])},
                    {"to": ctoken.address, "data": f"{COLLATERAL_POSTED_SELECTOR}{encode_address_arg(wallet_address)}"},
                    {"to": ctoken.address, "data": IS_BORROWABLE_SELECTOR},
                ]
            )

        raw_results = self.rpc.eth_batch_call(account_calls)
        partial_states: Dict[str, Dict[str, object]] = {}
        for index, ctoken in enumerate(CURVANCE_CTOKENS):
            base_index = index * 3
            ctoken_key = ctoken.address.lower()
            partial_states[ctoken_key] = {
                "share_balance_raw": self._decode_optional_uint(raw_results[base_index]),
                "collateral_shares_raw": self._decode_optional_uint(raw_results[base_index + 1]),
                "borrowable": self._decode_optional_uint(raw_results[base_index + 2]) != 0,
                "debt_raw": 0,
            }

        debt_calls = [
            {
                "to": ctoken.address,
                "data": f"{DEBT_BALANCE_SELECTOR}{encode_address_arg(wallet_address)}",
            }
            for ctoken in CURVANCE_CTOKENS
            if bool(partial_states[ctoken.address.lower()]["borrowable"])
        ]
        debt_tokens = [
            ctoken
            for ctoken in CURVANCE_CTOKENS
            if bool(partial_states[ctoken.address.lower()]["borrowable"])
        ]
        if debt_calls:
            debt_results = self.rpc.eth_batch_call(debt_calls)
            for ctoken, result in zip(debt_tokens, debt_results):
                partial_states[ctoken.address.lower()]["debt_raw"] = self._decode_optional_uint(result)

        return {
            key: CurvanceAccountCTokenState(
                share_balance_raw=int(value["share_balance_raw"]),
                collateral_shares_raw=int(value["collateral_shares_raw"]),
                borrowable=bool(value["borrowable"]),
                debt_raw=int(value["debt_raw"]),
            )
            for key, value in partial_states.items()
        }

    def _raw_position(
        self,
        ctoken: CurvanceCToken,
        source_id: str,
        category: DefiCategory,
        symbol: str,
        name: str,
        decimals: int,
        token_address: str,
        quantity: Decimal,
        price_usd: Decimal,
        value_usd: Decimal,
        timestamp: datetime,
        extra_metadata: Dict[str, str],
    ) -> RawDefiPosition:
        metadata = {
            "ctoken": ctoken.address.lower(),
            "ctoken_symbol": ctoken.symbol,
            "market_name": ctoken.market_name,
            "protocol_reader": self.protocol_reader_address,
            **extra_metadata,
        }
        return RawDefiPosition(
            source_id=source_id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category=category,
            asset_id=f"monad-{token_address.lower()}",
            asset_symbol=symbol,
            asset_name=name,
            decimals=decimals,
            contract_address=token_address.lower(),
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            apy=None,
            health_factor=None,
            metadata_json=json.dumps(metadata, sort_keys=True),
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(ctoken, category, quantity, price_usd, timestamp),
        )

    def _underlying_asset(self, ctoken_address: str) -> str:
        normalized = ctoken_address.lower()
        cached = self._asset_cache.get(normalized)
        if cached is not None:
            return cached
        addresses = decode_address_words(self.rpc.eth_call(ctoken_address, ASSET_SELECTOR))
        if not addresses:
            raise RpcError("Curvance asset() returned no address")
        self._asset_cache[normalized] = addresses[0]
        return addresses[0]

    def _raw_token_balance(self, token_address: str, owner_address: str) -> int:
        return decode_uint256(self.rpc.eth_call(token_address, call_data(BALANCE_OF_SELECTOR, [owner_address])))

    def _collateral_posted(self, ctoken_address: str, owner_address: str) -> int:
        return decode_uint256(
            self.rpc.eth_call(
                ctoken_address,
                f"{COLLATERAL_POSTED_SELECTOR}{encode_address_arg(owner_address)}",
            )
        )

    def _convert_to_assets(self, ctoken_address: str, shares_raw: int) -> int:
        return decode_uint256(
            self.rpc.eth_call(
                ctoken_address,
                f"{CONVERT_TO_ASSETS_SELECTOR}{encode_uint256_arg(shares_raw)}",
            )
        )

    def _debt_balance(self, ctoken_address: str, owner_address: str) -> int:
        return decode_uint256(
            self.rpc.eth_call(
                ctoken_address,
                f"{DEBT_BALANCE_SELECTOR}{encode_address_arg(owner_address)}",
            )
        )

    def _is_borrowable(self, ctoken_address: str) -> bool:
        normalized = ctoken_address.lower()
        cached = self._borrowable_cache.get(normalized)
        if cached is not None:
            return cached
        try:
            borrowable = decode_uint256(self.rpc.eth_call(ctoken_address, IS_BORROWABLE_SELECTOR)) != 0
        except Exception:
            borrowable = False
        self._borrowable_cache[normalized] = borrowable
        return borrowable

    def _asset_price_usd(self, token_address: str) -> Decimal:
        normalized = token_address.lower()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached
        data = (
            f"{GET_PRICE_SELECTOR}"
            f"{encode_address_arg(token_address)}"
            f"{encode_bool_arg(True)}"
            f"{encode_bool_arg(False)}"
        )
        words = decode_uint256_words(self.rpc.eth_call(self.protocol_reader_address, data))
        if len(words) < 2:
            raise RpcError("Curvance getPrice returned fewer than 2 words")
        if words[1] == 2:
            logger.warning("Curvance severe oracle error for token=%s", token_address)
        price = Decimal(words[0]) / WAD
        self._price_cache[normalized] = price
        return price

    def _token_decimals(self, token_address: str) -> int:
        normalized = token_address.lower()
        cached = self._decimals_cache.get(normalized)
        if cached is not None:
            return cached
        decimals = decode_uint256(self.rpc.eth_call(token_address, DECIMALS_SELECTOR))
        self._decimals_cache[normalized] = decimals
        return decimals

    def _token_metadata(self, token_address: str, fallback_ctoken_symbol: str) -> tuple[str, str]:
        normalized = token_address.lower()
        cached = self._metadata_cache.get(normalized)
        if cached is not None:
            return cached
        fallback_symbol = fallback_ctoken_symbol[1:] if fallback_ctoken_symbol.lower().startswith("c") else fallback_ctoken_symbol
        symbol = self._token_string(token_address, SYMBOL_SELECTOR) or fallback_symbol
        name = self._token_string(token_address, NAME_SELECTOR) or symbol
        self._metadata_cache[normalized] = (symbol, name)
        return symbol, name

    def _token_string(self, token_address: str, selector: str) -> Optional[str]:
        try:
            return decode_string(self.rpc.eth_call(token_address, selector))
        except Exception:
            return None

    def _decode_optional_uint(self, result: Optional[str]) -> int:
        if result is None:
            return 0
        return decode_uint256(result)

    def _payload_hash(
        self,
        ctoken: CurvanceCToken,
        category: str,
        quantity: Decimal,
        price_usd: Decimal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "ctoken": ctoken.address.lower(),
            "category": category,
            "quantity": str(quantity),
            "price_usd": str(price_usd),
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"curvance:{digest}"
