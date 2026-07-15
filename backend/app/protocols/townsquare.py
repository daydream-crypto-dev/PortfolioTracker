from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from ..db_models import SourceORM
from ..rpc import EvmRpcClient, RpcError, decode_uint256_words, encode_address_arg
from .base import DefiCategory, DefiFetchResult, RawDefiPosition

logger = logging.getLogger(__name__)

PROTOCOL_SLUG = "townsquare"
PROTOCOL_NAME = "TownSquare"
PROTOCOL_URL = "https://townsq.xyz/"
LOCAL_CHAIN_ID = "monad"

ACCOUNT_CONTROLLER_ADDRESS = "0xc2df24203ab3a4f3857d649757a99e18de059a16"
LOAN_CONTROLLER_ADDRESS = "0xc4c20efbefa4bde14091a3040d112cf981d8b2db"
PRICE_FEED_CONTROLLER_ADDRESS = "0x428cfa65310c70bc9e65bddb26c65fe4ca490376"
LOAN_IDS_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/project_cmc8zodsr8oib01x145wf2hw1/"
    "subgraphs/mainnet-loanIds/1.0.0/gn"
)

MONAD_TS_CHAIN_ID = 143
SECONDS_IN_YEAR = 365 * 24 * 60 * 60
WAD_INT = 10**18
WAD = Decimal(WAD_INT)

GET_ACCOUNT_ID_SELECTOR = "0x0a110f55"
RETRIEVE_USER_LOAN_SELECTOR = "0x96d091ea"
GET_DEPOSIT_DATA_SELECTOR = "0x38a42159"
GET_VARIABLE_BORROW_DATA_SELECTOR = "0xf7078e15"
GET_LAST_UPDATE_TIMESTAMP_SELECTOR = "0xe1e46882"
GET_LOAN_POOL_SELECTOR = "0x19e6fc82"
PROCESS_PRICE_FEED_SELECTOR = "0xe206f7e3"

LOAN_TYPE_NAMES = {
    1: "Deposit",
    2: "General",
    3: "MON Efficiency",
    4: "Stable Efficiency",
    5: "BTC Efficiency",
}


@dataclass(frozen=True)
class TownsquareToken:
    ts_token_id: str
    symbol: str
    name: str
    pool_id: int
    pool_address: str
    token_address: Optional[str]
    decimals: int

    @property
    def asset_id(self) -> str:
        if self.token_address is None:
            return "monad-mon"
        return f"monad-{self.token_address.lower()}"


@dataclass(frozen=True)
class TownsquareCollateral:
    balance: int
    reward_index: int


@dataclass(frozen=True)
class TownsquareBorrow:
    amount: int
    balance: int
    last_interest_index: int
    stable_interest_rate: int
    last_stable_update_timestamp: int
    reward_index: int


@dataclass(frozen=True)
class TownsquareLoan:
    loan_id: str
    account_id: str
    loan_type_id: int
    col_pools: List[int]
    bor_pools: List[int]
    collaterals: List[TownsquareCollateral]
    borrows: List[TownsquareBorrow]


@dataclass(frozen=True)
class TownsquarePoolInfo:
    token: TownsquareToken
    deposit_interest_rate: int
    deposit_interest_index: int
    variable_borrow_interest_rate: int
    variable_borrow_interest_index: int
    last_update_timestamp: int
    price_raw: int
    price_decimals: int


@dataclass(frozen=True)
class TownsquareLoanPoolInfo:
    collateral_factor: int
    borrow_factor: int


TOWNSQUARE_TOKENS: List[TownsquareToken] = [
    TownsquareToken("MON", "MON", "Monad", 2, "0x106d0e2bff74b39d09636bdcd5d4189f24d91433", None, 18),
    TownsquareToken(
        "wMON",
        "WMON",
        "Wrapped MON",
        4,
        "0xf358f9e4ba7d210fde8c9a30522bb0063e15c4bb",
        "0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A",
        18,
    ),
    TownsquareToken(
        "USDC",
        "USDC",
        "USD Coin",
        10,
        "0xdb4e67f878289a820046f46f6304fd6ee1449281",
        "0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
        6,
    ),
    TownsquareToken(
        "AUSD",
        "AUSD",
        "AUSD",
        7,
        "0x7f5996865e952bd7892366712d319de59b9ecc6b",
        "0x00000000eFE302BEAA2b3e6e1b18d08D69a9012a",
        6,
    ),
    TownsquareToken(
        "earnAUSD",
        "earnAUSD",
        "earnAUSD",
        19,
        "0x7d99267be583d46273803b2b1c5edb98bff6538d",
        "0x103222f020e98Bba0AD9809A011FDF8e6F067496",
        6,
    ),
    TownsquareToken(
        "USD1",
        "USD1",
        "USD1",
        18,
        "0x3249df5ca0b825e7c3e7d84a4bb11c2eacd8c0f6",
        "0x111111d2bf19e43C34263401e0CAd979eD1cdb61",
        6,
    ),
    TownsquareToken(
        "WETH",
        "WETH",
        "Wrapped Ether",
        15,
        "0x0394728ef18258ca21f782ce37ebf1a16799d7ef",
        "0xEE8c0E9f1BFFb4Eb878d8f15f368A02a35481242",
        18,
    ),
    TownsquareToken(
        "WBTC",
        "WBTC",
        "Wrapped BTC",
        16,
        "0xd636d6ab7072483de6ddc067f9147f8c1e512f18",
        "0x0555E30da8f98308EdB960aa94C0Db47230d2B9c",
        8,
    ),
    TownsquareToken(
        "shMON",
        "shMON",
        "shMON",
        14,
        "0xd2108dec68089646c3d4d95f01ea42ee1142e7f4",
        "0x1B68626dCa36c7fE922fD2d55E4f631d962dE19c",
        18,
    ),
    TownsquareToken(
        "aprMON",
        "aprMON",
        "aprMON",
        13,
        "0xfdd72592a657775249da1b013ac1371ccd45d885",
        "0x0c65A0BC65a5D819235B71F554D210D3F80E0852",
        18,
    ),
    TownsquareToken(
        "gMON",
        "gMON",
        "gMON",
        12,
        "0x428bebf994c970656854eb66586583fe682cc1d3",
        "0x8498312A6B3CbD158bf0c93AbdCF29E6e4F55081",
        18,
    ),
    TownsquareToken(
        "sMON",
        "sMON",
        "Staked MON",
        8,
        "0xc0fda7f80e772ac3f85735f66ecb1ac964a033f2",
        "0xA3227C5969757783154C60bF0bC1944180ed81B9",
        18,
    ),
    TownsquareToken(
        "USDT",
        "USDT",
        "USDT",
        20,
        "0x7821ba4e39c86ac4bdd2482e853f9c7ba57d01d0",
        "0xe7cd86e13AC4309349F30B3435a9d337750fC82D",
        6,
    ),
    TownsquareToken(
        "sAUSD",
        "sAUSD",
        "sAUSD",
        21,
        "0x4c79b2368d0ffa1bc7399ee0fb3569e220c3f52d",
        "0xD793c04B87386A6bb84ee61D98e0065FdE7fdA5E",
        6,
    ),
    TownsquareToken(
        "syzUSD",
        "syzUSD",
        "syzUSD",
        25,
        "0x8a0f894ec72c879b0f808c6d3fc1fbc7b130cc69",
        "0x484be0540aD49f351eaa04eeB35dF0f937D4E73f",
        18,
    ),
    TownsquareToken(
        "yzUSD",
        "yzUSD",
        "yzUSD",
        24,
        "0x9f2bc225892eee4c2b579d4b7cb3a74859b5d622",
        "0x9dcB0D17eDDE04D27F387c89fECb78654C373858",
        18,
    ),
    TownsquareToken(
        "cbBTC",
        "cbBTC",
        "Coinbase Wrapped BTC",
        26,
        "0x6973eb51c7a2aef62b22208c72869b4440176ebe",
        "0xd18B7EC58Cdf4876f6AFebd3Ed1730e4Ce10414b",
        8,
    ),
    TownsquareToken(
        "enzoBTC",
        "enzoBTC",
        "Enzo BTC",
        31,
        "0x43df57b359141aae021e64375ddaa0b2bb89b148",
        "0xD7aCB868F97F8286D5d3A0Fd5Ef112a8a72eCD90",
        8,
    ),
]


class TownsquareDefiAdapter:
    """Read TownSquare lending positions from Monad using protocol contracts and subgraph loan IDs."""

    def __init__(self, rpc_url: str, timeout_seconds: int = 20) -> None:
        self.rpc = EvmRpcClient(rpc_url, timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.tokens_by_pool_id = {token.pool_id: token for token in TOWNSQUARE_TOKENS}
        self._pool_info_cache: Dict[int, TownsquarePoolInfo] = {}
        self._loan_pool_cache: Dict[tuple[int, int], TownsquareLoanPoolInfo] = {}

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        if source.wallet is None:
            return DefiFetchResult(positions=[], status_message="TownSquare skipped source without wallet details")

        account_id = self._account_id_for_wallet(source.wallet.address)
        if account_id is None:
            return DefiFetchResult(positions=[], status_message="TownSquare sync found no account for wallet.")

        loan_refs = self._loan_refs(account_id)
        if not loan_refs:
            return DefiFetchResult(positions=[], status_message="TownSquare sync found no open loans.")

        loans = [self._retrieve_user_loan(loan_ref["loan_id"]) for loan_ref in loan_refs]
        positions = self._positions_from_loans(source.id, loans, timestamp)
        return DefiFetchResult(
            positions=positions,
            status_message=f"TownSquare sync wrote {len(positions)} lending positions.",
        )

    def _positions_from_loans(
        self,
        source_id: str,
        loans: List[TownsquareLoan],
        timestamp: datetime,
    ) -> List[RawDefiPosition]:
        loan_health = {loan.loan_id: self._health_factor(loan) for loan in loans}
        positions: List[RawDefiPosition] = []

        for loan in loans:
            for index, collateral in enumerate(loan.collaterals):
                pool_id = loan.col_pools[index]
                token = self.tokens_by_pool_id.get(pool_id)
                if token is None or collateral.balance == 0:
                    continue
                pool_info = self._pool_info(pool_id)
                token_balance = self._to_underlying_amount(
                    collateral.balance,
                    pool_info.deposit_interest_index,
                )
                if token_balance == 0:
                    continue
                positions.append(
                    self._raw_position(
                        source_id=source_id,
                        loan=loan,
                        token=token,
                        category="deposit",
                        raw_quantity=token_balance,
                        pool_info=pool_info,
                        apy=self._rate_to_percent(pool_info.deposit_interest_rate),
                        health_factor=loan_health[loan.loan_id],
                        metadata={
                            "loan_id": loan.loan_id,
                            "account_id": loan.account_id,
                            "loan_type_id": loan.loan_type_id,
                            "loan_type": LOAN_TYPE_NAMES.get(loan.loan_type_id, "Unknown"),
                            "pool_id": pool_id,
                            "f_token_balance": str(collateral.balance),
                            "reward_index": str(collateral.reward_index),
                            "deposit_interest_index": str(pool_info.deposit_interest_index),
                        },
                        timestamp=timestamp,
                    )
                )

            for index, borrow in enumerate(loan.borrows):
                pool_id = loan.bor_pools[index]
                token = self.tokens_by_pool_id.get(pool_id)
                if token is None or borrow.balance == 0:
                    continue
                pool_info = self._pool_info(pool_id)
                borrow_balance = self._borrow_balance(borrow, pool_info)
                if borrow_balance == 0:
                    continue
                positions.append(
                    self._raw_position(
                        source_id=source_id,
                        loan=loan,
                        token=token,
                        category="borrow",
                        raw_quantity=borrow_balance,
                        pool_info=pool_info,
                        apy=self._rate_to_percent(pool_info.variable_borrow_interest_rate),
                        health_factor=loan_health[loan.loan_id],
                        metadata={
                            "loan_id": loan.loan_id,
                            "account_id": loan.account_id,
                            "loan_type_id": loan.loan_type_id,
                            "loan_type": LOAN_TYPE_NAMES.get(loan.loan_type_id, "Unknown"),
                            "pool_id": pool_id,
                            "borrowed_amount": str(borrow.amount),
                            "stored_borrow_balance": str(borrow.balance),
                            "last_interest_index": str(borrow.last_interest_index),
                            "stable_interest_rate": str(borrow.stable_interest_rate),
                            "last_stable_update_timestamp": str(borrow.last_stable_update_timestamp),
                            "reward_index": str(borrow.reward_index),
                            "variable_borrow_interest_index": str(pool_info.variable_borrow_interest_index),
                        },
                        timestamp=timestamp,
                    )
                )

        return positions

    def _raw_position(
        self,
        source_id: str,
        loan: TownsquareLoan,
        token: TownsquareToken,
        category: DefiCategory,
        raw_quantity: int,
        pool_info: TownsquarePoolInfo,
        apy: Decimal,
        health_factor: Optional[Decimal],
        metadata: Dict[str, object],
        timestamp: datetime,
    ) -> RawDefiPosition:
        quantity = Decimal(raw_quantity) / (Decimal(10) ** token.decimals)
        price_usd = Decimal(pool_info.price_raw) / WAD
        value_usd = quantity * price_usd
        if category == "borrow":
            value_usd = -value_usd

        metadata = {
            **metadata,
            "pool_address": token.pool_address,
            "oracle": PRICE_FEED_CONTROLLER_ADDRESS,
            "price_raw": str(pool_info.price_raw),
            "price_decimals": pool_info.price_decimals,
        }
        metadata_json = json.dumps(metadata, sort_keys=True)
        return RawDefiPosition(
            source_id=source_id,
            chain_id=LOCAL_CHAIN_ID,
            protocol_slug=PROTOCOL_SLUG,
            protocol_name=PROTOCOL_NAME,
            protocol_url=PROTOCOL_URL,
            category=category,
            asset_id=token.asset_id,
            asset_symbol=token.symbol,
            asset_name=token.name,
            decimals=token.decimals,
            contract_address=token.token_address.lower() if token.token_address else None,
            quantity=quantity,
            price_usd=price_usd,
            value_usd=value_usd,
            apy=apy,
            health_factor=health_factor,
            metadata_json=metadata_json,
            provider=PROTOCOL_NAME,
            raw_payload_hash=self._payload_hash(loan.loan_id, category, raw_quantity, metadata_json, timestamp),
        )

    def _account_id_for_wallet(self, wallet_address: str) -> Optional[str]:
        data = (
            GET_ACCOUNT_ID_SELECTOR
            + encode_address_arg(wallet_address)
            + self._uint_arg(MONAD_TS_CHAIN_ID)
        )
        try:
            result = self.rpc.eth_call(ACCOUNT_CONTROLLER_ADDRESS, data)
        except RpcError as exc:
            if "execution reverted" in str(exc).lower() or "noaccount" in str(exc).lower():
                logger.info("TownSquare account lookup found no account for wallet=%s", wallet_address)
                return None
            raise

        account_id = self._bytes32_word(result)
        if account_id == "0x" + ("0" * 64):
            return None
        return account_id

    def _loan_refs(self, account_id: str) -> List[Dict[str, object]]:
        query = """
        query TownsquareOpenLoans($accountId: Bytes!) {
          users(where: { accountId: $accountId }) {
            accountId
            userLoans(where: { status: Open }) {
              loanId
              loanTypeId
              status
            }
          }
        }
        """
        payload = self._graphql_request(query, {"accountId": account_id.lower()})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("TownSquare subgraph response did not include data")
        users = data.get("users")
        if not isinstance(users, list) or not users:
            return []

        loan_refs: List[Dict[str, object]] = []
        for user in users:
            if not isinstance(user, dict):
                continue
            loans = user.get("userLoans")
            if isinstance(loans, list):
                for loan in loans:
                    if isinstance(loan, dict) and isinstance(loan.get("loanId"), str):
                        loan_refs.append(
                            {
                                "loan_id": str(loan["loanId"]),
                                "loan_type_id": int(loan.get("loanTypeId") or 0),
                            }
                        )
        return loan_refs

    def _retrieve_user_loan(self, loan_id: str) -> TownsquareLoan:
        result = self.rpc.eth_call(
            LOAN_CONTROLLER_ADDRESS,
            f"{RETRIEVE_USER_LOAN_SELECTOR}{self._bytes32_arg(loan_id)}",
        )
        decoded = self._decode_user_loan(result)
        return TownsquareLoan(
            loan_id=loan_id.lower(),
            account_id=decoded["account_id"],
            loan_type_id=decoded["loan_type_id"],
            col_pools=decoded["col_pools"],
            bor_pools=decoded["bor_pools"],
            collaterals=decoded["collaterals"],
            borrows=decoded["borrows"],
        )

    def _pool_info(self, pool_id: int) -> TownsquarePoolInfo:
        cached = self._pool_info_cache.get(pool_id)
        if cached:
            return cached
        token = self.tokens_by_pool_id[pool_id]

        last_update_timestamp = decode_uint256_words(
            self.rpc.eth_call(token.pool_address, GET_LAST_UPDATE_TIMESTAMP_SELECTOR)
        )[0]
        deposit_words = decode_uint256_words(
            self.rpc.eth_call(token.pool_address, GET_DEPOSIT_DATA_SELECTOR)
        )
        variable_borrow_words = decode_uint256_words(
            self.rpc.eth_call(token.pool_address, GET_VARIABLE_BORROW_DATA_SELECTOR)
        )
        price_words = decode_uint256_words(
            self.rpc.eth_call(
                PRICE_FEED_CONTROLLER_ADDRESS,
                f"{PROCESS_PRICE_FEED_SELECTOR}{self._uint_arg(pool_id)}",
            )
        )

        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        deposit_interest_index = self._deposit_interest_index(
            deposit_words[2],
            deposit_words[3],
            last_update_timestamp,
            current_timestamp,
        )
        variable_borrow_interest_index = self._borrow_interest_index(
            variable_borrow_words[4],
            variable_borrow_words[5],
            last_update_timestamp,
            current_timestamp,
        )
        pool_info = TownsquarePoolInfo(
            token=token,
            deposit_interest_rate=deposit_words[2],
            deposit_interest_index=deposit_interest_index,
            variable_borrow_interest_rate=variable_borrow_words[4],
            variable_borrow_interest_index=variable_borrow_interest_index,
            last_update_timestamp=last_update_timestamp,
            price_raw=price_words[0],
            price_decimals=price_words[1],
        )
        self._pool_info_cache[pool_id] = pool_info
        return pool_info

    def _loan_pool_info(self, loan_type_id: int, pool_id: int) -> TownsquareLoanPoolInfo:
        key = (loan_type_id, pool_id)
        cached = self._loan_pool_cache.get(key)
        if cached:
            return cached
        data = (
            GET_LOAN_POOL_SELECTOR
            + self._uint_arg(loan_type_id)
            + self._uint_arg(pool_id)
        )
        words = decode_uint256_words(self.rpc.eth_call(LOAN_CONTROLLER_ADDRESS, data))
        info = TownsquareLoanPoolInfo(collateral_factor=words[4], borrow_factor=words[5])
        self._loan_pool_cache[key] = info
        return info

    def _health_factor(self, loan: TownsquareLoan) -> Optional[Decimal]:
        effective_collateral = Decimal("0")
        effective_borrow = Decimal("0")
        for index, collateral in enumerate(loan.collaterals):
            pool_id = loan.col_pools[index]
            token = self.tokens_by_pool_id.get(pool_id)
            if token is None:
                continue
            pool_info = self._pool_info(pool_id)
            loan_pool_info = self._loan_pool_info(loan.loan_type_id, pool_id)
            token_balance = self._to_underlying_amount(collateral.balance, pool_info.deposit_interest_index)
            value = self._usd_value(token_balance, token.decimals, pool_info.price_raw)
            effective_collateral += value * Decimal(loan_pool_info.collateral_factor) / Decimal("10000")

        for index, borrow in enumerate(loan.borrows):
            pool_id = loan.bor_pools[index]
            token = self.tokens_by_pool_id.get(pool_id)
            if token is None:
                continue
            pool_info = self._pool_info(pool_id)
            loan_pool_info = self._loan_pool_info(loan.loan_type_id, pool_id)
            borrow_balance = self._borrow_balance(borrow, pool_info)
            value = self._usd_value(borrow_balance, token.decimals, pool_info.price_raw)
            effective_borrow += value * Decimal(loan_pool_info.borrow_factor) / Decimal("10000")

        if effective_borrow == 0:
            return None
        return effective_collateral / effective_borrow

    def _graphql_request(self, query: str, variables: Dict[str, object]) -> Dict[str, object]:
        request = Request(
            LOAN_IDS_SUBGRAPH_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "PortfolioTracker/0.5 (+local)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TownSquare subgraph HTTP {exc.code}: {detail[:300]}") from exc
        except URLError as exc:
            raise RuntimeError(f"TownSquare subgraph request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("TownSquare subgraph returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("TownSquare subgraph returned a non-object JSON body")
        if payload.get("errors"):
            raise RuntimeError(f"TownSquare subgraph returned errors: {payload['errors']}")
        return payload

    def _decode_user_loan(self, result: str) -> Dict[str, object]:
        raw = result.removeprefix("0x")
        if len(raw) < 64 * 6:
            raise RpcError("TownSquare retrieveUserLoan response was too short")
        words = [raw[index:index + 64] for index in range(0, len(raw), 64)]

        account_id = f"0x{words[0]}"
        loan_type_id = int(words[1], 16)
        col_pools = self._decode_uint_array(words, int(words[2], 16))
        bor_pools = self._decode_uint_array(words, int(words[3], 16))
        collaterals = [
            TownsquareCollateral(balance=item[0], reward_index=item[1])
            for item in self._decode_static_tuple_array(words, int(words[4], 16), 2)
        ]
        borrows = [
            TownsquareBorrow(
                amount=item[0],
                balance=item[1],
                last_interest_index=item[2],
                stable_interest_rate=item[3],
                last_stable_update_timestamp=item[4],
                reward_index=item[5],
            )
            for item in self._decode_static_tuple_array(words, int(words[5], 16), 6)
        ]

        if len(col_pools) != len(collaterals) or len(bor_pools) != len(borrows):
            raise RpcError("TownSquare retrieveUserLoan response had mismatched pool and position arrays")
        return {
            "account_id": account_id,
            "loan_type_id": loan_type_id,
            "col_pools": col_pools,
            "bor_pools": bor_pools,
            "collaterals": collaterals,
            "borrows": borrows,
        }

    def _decode_uint_array(self, words: List[str], offset_bytes: int) -> List[int]:
        start = offset_bytes // 32
        length = int(words[start], 16)
        return [int(words[start + 1 + index], 16) for index in range(length)]

    def _decode_static_tuple_array(
        self,
        words: List[str],
        offset_bytes: int,
        tuple_size: int,
    ) -> List[List[int]]:
        start = offset_bytes // 32
        length = int(words[start], 16)
        cursor = start + 1
        tuples: List[List[int]] = []
        for _ in range(length):
            tuples.append([int(word, 16) for word in words[cursor:cursor + tuple_size]])
            cursor += tuple_size
        return tuples

    def _deposit_interest_index(
        self,
        interest_rate: int,
        old_interest_index: int,
        last_update_timestamp: int,
        current_timestamp: int,
    ) -> int:
        delta = max(current_timestamp - last_update_timestamp, 0)
        growth = WAD_INT + (interest_rate * delta // SECONDS_IN_YEAR)
        return old_interest_index * growth // WAD_INT

    def _borrow_interest_index(
        self,
        interest_rate: int,
        old_interest_index: int,
        last_update_timestamp: int,
        current_timestamp: int,
    ) -> int:
        delta = max(current_timestamp - last_update_timestamp, 0)
        base = WAD_INT + (interest_rate // SECONDS_IN_YEAR)
        return old_interest_index * self._fixed_exp_by_squaring(base, delta) // WAD_INT

    def _fixed_exp_by_squaring(self, value: int, exponent: int) -> int:
        if exponent == 0:
            return WAD_INT
        result = WAD_INT
        while exponent > 1:
            if exponent % 2:
                result = value * result // WAD_INT
                exponent = (exponent - 1) // 2
            else:
                exponent //= 2
            value = value * value // WAD_INT
        return value * result // WAD_INT

    def _to_underlying_amount(self, f_amount: int, deposit_interest_index: int) -> int:
        return f_amount * deposit_interest_index // WAD_INT

    def _borrow_balance(self, borrow: TownsquareBorrow, pool_info: TownsquarePoolInfo) -> int:
        if borrow.last_stable_update_timestamp > 0:
            borrow_index = self._borrow_interest_index(
                borrow.stable_interest_rate,
                borrow.last_interest_index,
                borrow.last_stable_update_timestamp,
                int(datetime.now(timezone.utc).timestamp()),
            )
        else:
            borrow_index = pool_info.variable_borrow_interest_index
        if borrow.last_interest_index == 0:
            return borrow.balance
        return self._ceil_div(borrow.balance * borrow_index, borrow.last_interest_index)

    def _usd_value(self, raw_quantity: int, token_decimals: int, price_raw: int) -> Decimal:
        quantity = Decimal(raw_quantity) / (Decimal(10) ** token_decimals)
        price_usd = Decimal(price_raw) / WAD
        return quantity * price_usd

    def _rate_to_percent(self, rate_raw: int) -> Decimal:
        return Decimal(rate_raw) * Decimal("100") / WAD

    def _ceil_div(self, numerator: int, denominator: int) -> int:
        return -(-numerator // denominator)

    def _bytes32_arg(self, value: str) -> str:
        raw = value.removeprefix("0x").lower()
        if len(raw) > 64:
            raise ValueError(f"Invalid bytes32 value: {value}")
        int(raw, 16)
        return raw.rjust(64, "0")

    def _bytes32_word(self, result: str) -> str:
        words = decode_uint256_words(result)
        if not words:
            return "0x" + ("0" * 64)
        return f"0x{words[0]:064x}"

    def _uint_arg(self, value: int) -> str:
        return hex(value)[2:].rjust(64, "0")

    def _payload_hash(
        self,
        loan_id: str,
        category: str,
        raw_quantity: int,
        metadata_json: str,
        timestamp: datetime,
    ) -> str:
        payload = {
            "protocol": PROTOCOL_SLUG,
            "loan_id": loan_id,
            "category": category,
            "raw_quantity": str(raw_quantity),
            "metadata": metadata_json,
            "timestamp": timestamp.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"townsquare:{digest}"
