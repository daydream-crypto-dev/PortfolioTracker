from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_MORALIS_MESSAGE = (
    f"MORALIS_API_KEY is required. Add it to {PROJECT_ROOT / '.env'} before starting "
    "PortfolioTracker."
)


class ConfigurationError(RuntimeError):
    """Raised when required local runtime configuration is missing."""


@dataclass(frozen=True)
class PerplAccountSettings:
    """One Perpl read-only API key bound to one wallet source address."""

    wallet_address: str
    api_key: str
    api_key_secret: str


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables and local .env files."""

    moralis_api_key: Optional[str]
    monad_rpc_url: str
    ethereum_rpc_url: str
    okx_api_key: Optional[str]
    okx_api_secret: Optional[str]
    okx_api_passphrase: Optional[str]
    okx_account_label: Optional[str]
    aave_monad_pool_address: Optional[str]
    aave_monad_oracle_address: Optional[str]
    aave_monad_protocol_data_provider_address: Optional[str]
    perpl_api_url: str
    perpl_chain_id: int
    perpl_accounts: Tuple[PerplAccountSettings, ...]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _load_env_files(paths: Iterable[Path]) -> None:
    for path in paths:
        _load_env_file(path)


def _clean_env_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _load_perpl_accounts(max_accounts: int = 20) -> Tuple[PerplAccountSettings, ...]:
    accounts = []
    seen_wallets = set()

    for index in range(1, max_accounts + 1):
        wallet_address = _clean_env_value(os.getenv(f"PERPL_ACCOUNT_{index}_WALLET_ADDRESS"))
        api_key = _clean_env_value(os.getenv(f"PERPL_ACCOUNT_{index}_API_KEY"))
        api_key_secret = _clean_env_value(os.getenv(f"PERPL_ACCOUNT_{index}_API_KEY_SECRET"))
        if not wallet_address and not api_key and not api_key_secret:
            continue
        if not wallet_address or not api_key or not api_key_secret:
            continue

        normalized_wallet = wallet_address.lower()
        if normalized_wallet in seen_wallets:
            continue
        accounts.append(
            PerplAccountSettings(
                wallet_address=normalized_wallet,
                api_key=api_key,
                api_key_secret=api_key_secret,
            )
        )
        seen_wallets.add(normalized_wallet)

    legacy_wallet_address = _clean_env_value(os.getenv("PERPL_WALLET_ADDRESS"))
    legacy_api_key = _clean_env_value(os.getenv("PERPL_API_KEY"))
    legacy_api_key_secret = _clean_env_value(os.getenv("PERPL_API_KEY_SECRET"))
    if legacy_wallet_address and legacy_api_key and legacy_api_key_secret:
        normalized_legacy_wallet = legacy_wallet_address.lower()
        if normalized_legacy_wallet not in seen_wallets:
            accounts.append(
                PerplAccountSettings(
                    wallet_address=normalized_legacy_wallet,
                    api_key=legacy_api_key,
                    api_key_secret=legacy_api_key_secret,
                )
            )

    return tuple(accounts)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_env_files([PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"])
    api_key = os.getenv("MORALIS_API_KEY")
    okx_api_key = os.getenv("OKX_API_KEY")
    okx_api_secret = os.getenv("OKX_API_SECRET")
    okx_api_passphrase = os.getenv("OKX_API_PASSPHRASE")
    okx_account_label = _clean_env_value(os.getenv("OKX_ACCOUNT_LABEL"))
    aave_monad_pool_address = _clean_env_value(os.getenv("AAVE_MONAD_POOL_ADDRESS"))
    aave_monad_oracle_address = _clean_env_value(os.getenv("AAVE_MONAD_ORACLE_ADDRESS"))
    aave_monad_protocol_data_provider_address = _clean_env_value(
        os.getenv("AAVE_MONAD_PROTOCOL_DATA_PROVIDER_ADDRESS")
    )
    perpl_api_url = os.getenv("PERPL_API_URL") or "https://app.perpl.xyz/api"
    perpl_chain_id = int(os.getenv("PERPL_CHAIN_ID") or "143")
    perpl_accounts = _load_perpl_accounts()
    monad_rpc_url = (
        os.getenv("MONAD_RPC_URL")
        or os.getenv("QUICKNODE_MONAD_RPC_URL")
        or "https://rpc.monad.xyz"
    )
    ethereum_rpc_url = (
        os.getenv("ETHEREUM_RPC_URL")
        or os.getenv("MAINNET_RPC_URL")
        or "https://ethereum-rpc.publicnode.com"
    )
    return Settings(
        moralis_api_key=api_key.strip() if api_key else None,
        monad_rpc_url=monad_rpc_url.strip(),
        ethereum_rpc_url=ethereum_rpc_url.strip(),
        okx_api_key=okx_api_key.strip() if okx_api_key and okx_api_key.strip() else None,
        okx_api_secret=okx_api_secret.strip() if okx_api_secret and okx_api_secret.strip() else None,
        okx_api_passphrase=(
            okx_api_passphrase.strip()
            if okx_api_passphrase and okx_api_passphrase.strip()
            else None
        ),
        okx_account_label=okx_account_label,
        aave_monad_pool_address=aave_monad_pool_address,
        aave_monad_oracle_address=aave_monad_oracle_address,
        aave_monad_protocol_data_provider_address=aave_monad_protocol_data_provider_address,
        perpl_api_url=perpl_api_url.strip().rstrip("/"),
        perpl_chain_id=perpl_chain_id,
        perpl_accounts=perpl_accounts,
    )


def require_moralis_api_key(settings: Optional[Settings] = None) -> str:
    """Return the Moralis key or fail fast with a user-actionable startup error."""
    loaded_settings = settings or get_settings()
    if not loaded_settings.moralis_api_key:
        raise ConfigurationError(REQUIRED_MORALIS_MESSAGE)
    return loaded_settings.moralis_api_key
