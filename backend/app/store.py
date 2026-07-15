from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Literal, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from .adapters import (
    BalanceAdapter,
    MoralisEvmBalanceAdapter,
    NoopBalanceAdapter,
    OkxBalanceAdapter,
    RawPosition,
)
from .config import get_settings
from .db_models import (
    AssetORM,
    ChainORM,
    DefiPositionSnapshotORM,
    ExchangeAccountORM,
    PositionSnapshotORM,
    SourceORM,
    SyncRunORM,
    WalletORM,
)
from .defi_coverage import MoralisDefiCoverageClient, protocol_display_name
from .models import (
    CreateExchangeSourceRequest,
    CreateWalletSourceRequest,
    DefiCoverageResponse,
    DefiCoverageTarget,
    DefiPortfolio,
    DefiPosition,
    DefiProtocolPositionGroup,
    Holding,
    HistoryPoint,
    PortfolioSummary,
    Source,
    SyncRunResponse,
)
from .protocols.base import DefiProtocolAdapter, RawDefiPosition
from .protocols.aave import (
    AAVE_V3_ETHEREUM_ORACLE,
    AAVE_V3_ETHEREUM_POOL,
    AAVE_V3_ETHEREUM_PROTOCOL_DATA_PROVIDER,
    AaveDeployment,
    AaveV3DefiAdapter,
)
from .protocols.curvance import CURVANCE_CTOKEN_ADDRESSES, CurvanceDefiAdapter
from .protocols.leverup import LEVERUP_DERIVATIVE_TOKEN_ADDRESSES, LeverUpDefiAdapter
from .protocols.neverland import NEVERLAND_DERIVATIVE_TOKEN_ADDRESSES, NeverlandDefiAdapter
from .protocols.perpl import PerplDefiAdapter
from .protocols.townsquare import TownsquareDefiAdapter

logger = logging.getLogger(__name__)

SUPPORTED_EVM_CHAINS: Dict[str, str] = {
    "monad": "Monad",
    "ethereum": "Ethereum",
}
MONAD_DEFI_COVERAGE_TARGETS = ["Neverland", "Leverup", "TownSquare", "Perpl", "Curvance", "Aave"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _short_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _asset_logo_url(asset: AssetORM) -> Optional[str]:
    try:
        external_ids = json.loads(asset.external_ids_json or "{}")
    except json.JSONDecodeError:
        return None

    logo_url = external_ids.get("logo") or external_ids.get("thumbnail")
    return str(logo_url) if logo_url else None


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _normalize_evm_address(address: str) -> str:
    normalized = address.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", normalized):
        raise ValueError("Wallet address must be a valid EVM address: 0x followed by 40 hex characters")
    return normalized.lower()


def _missing_okx_settings() -> List[str]:
    settings = get_settings()
    return [
        name
        for name, value in [
            ("OKX_API_KEY", settings.okx_api_key),
            ("OKX_API_SECRET", settings.okx_api_secret),
            ("OKX_API_PASSPHRASE", settings.okx_api_passphrase),
        ]
        if not value
    ]


def _okx_source_id(api_key: str) -> str:
    return _stable_id("exchange-okx", api_key)


def _okx_source_label() -> str:
    return getattr(get_settings(), "okx_account_label", None) or "OKX Account"


def _upsert_okx_exchange_account(session: Session, source_id: str, api_key: str) -> None:
    account = session.get(ExchangeAccountORM, source_id)
    if account:
        account.exchange = "okx"
        account.credentials_ref = "env:OKX_API_KEY"
        account.api_key_label = _mask_api_key(api_key)
        account.default_enabled = True
        return

    session.add(
        ExchangeAccountORM(
            id=source_id,
            source_id=source_id,
            exchange="okx",
            credentials_ref="env:OKX_API_KEY",
            api_key_label=_mask_api_key(api_key),
            default_enabled=True,
        )
    )


def _format_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _decimal_to_float(value: Decimal, places: int = 8) -> float:
    return float(round(value, places))


def _chain_rows() -> List[ChainORM]:
    return [
        ChainORM(
            id="monad",
            slug="monad",
            name="Monad",
            chain_type="evm",
            native_symbol="MON",
            chain_id=143,
            enabled=True,
        ),
        ChainORM(
            id="ethereum",
            slug="ethereum",
            name="Ethereum",
            chain_type="evm",
            native_symbol="ETH",
            chain_id=1,
            enabled=True,
        )
    ]


def _ensure_chains(session: Session) -> None:
    for chain in _chain_rows():
        session.merge(chain)


def _ensure_okx_source_from_env(session: Session) -> None:
    """Create or refresh a toggleable OKX source from local read-only credentials."""
    settings = get_settings()
    if _missing_okx_settings():
        return
    api_key = settings.okx_api_key
    if api_key is None:
        return

    source_id = _okx_source_id(api_key)
    source_label = _okx_source_label()
    existing_source = session.get(SourceORM, source_id)
    if existing_source:
        existing_source.label = source_label
        existing_source.provider = "OKX"
        existing_source.updated_at = _now()
        _upsert_okx_exchange_account(session, source_id, api_key)
        return

    created_at = _now()
    source = SourceORM(
        id=source_id,
        label=source_label,
        type="exchange",
        provider="OKX",
        enabled=True,
        last_synced_at=created_at,
        status="warning",
        status_message="OKX source loaded from .env. Run Sync now to fetch live OKX balances.",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(source)
    _upsert_okx_exchange_account(session, source_id, api_key)


def _ensure_assets_for_positions(session: Session, positions: Iterable[RawPosition]) -> None:
    positions_by_asset_id = {position.asset_id: position for position in positions}
    if not positions_by_asset_id:
        return

    existing_assets = {
        asset.id: asset
        for asset in session.scalars(
            select(AssetORM).where(AssetORM.id.in_(positions_by_asset_id.keys()))
        ).all()
    }
    new_assets: List[AssetORM] = []

    for asset_id, position in positions_by_asset_id.items():
        existing_asset = existing_assets.get(asset_id)
        if existing_asset:
            existing_asset.symbol = position.asset_symbol
            existing_asset.name = position.asset_name
            existing_asset.chain_id = position.chain_id
            existing_asset.contract_address = position.contract_address
            existing_asset.decimals = position.decimals
            existing_asset.external_ids_json = position.external_ids_json
            continue

        new_assets.append(
            AssetORM(
                id=asset_id,
                asset_class="crypto",
                symbol=position.asset_symbol,
                name=position.asset_name,
                chain_id=position.chain_id,
                contract_address=position.contract_address,
                decimals=position.decimals,
                external_ids_json=position.external_ids_json,
            )
        )

    session.add_all(new_assets)


def _ensure_assets_for_defi_positions(session: Session, positions: Iterable[RawDefiPosition]) -> None:
    positions_by_asset_id = {position.asset_id: position for position in positions}
    if not positions_by_asset_id:
        return

    existing_assets = {
        asset.id: asset
        for asset in session.scalars(
            select(AssetORM).where(AssetORM.id.in_(positions_by_asset_id.keys()))
        ).all()
    }
    new_assets: List[AssetORM] = []

    for asset_id, position in positions_by_asset_id.items():
        existing_asset = existing_assets.get(asset_id)
        if existing_asset:
            existing_asset.symbol = position.asset_symbol
            existing_asset.name = position.asset_name
            existing_asset.chain_id = position.chain_id
            existing_asset.contract_address = position.contract_address
            existing_asset.decimals = position.decimals
            continue

        new_assets.append(
            AssetORM(
                id=asset_id,
                asset_class="crypto",
                symbol=position.asset_symbol,
                name=position.asset_name,
                chain_id=position.chain_id,
                contract_address=position.contract_address,
                decimals=position.decimals,
                external_ids_json="{}",
            )
        )

    session.add_all(new_assets)


def _snapshot_row_from_position(position: RawPosition, timestamp: datetime) -> dict:
    return {
        "timestamp": timestamp,
        "source_id": position.source_id,
        "chain_id": position.chain_id,
        "asset_id": position.asset_id,
        "quantity": position.quantity,
        "price_usd": position.price_usd,
        "value_usd": position.value_usd,
        "provider": position.provider,
        "raw_payload_hash": position.raw_payload_hash,
    }


def _defi_snapshot_row_from_position(position: RawDefiPosition, timestamp: datetime) -> dict:
    return {
        "timestamp": timestamp,
        "source_id": position.source_id,
        "chain_id": position.chain_id,
        "protocol_slug": position.protocol_slug,
        "protocol_name": position.protocol_name,
        "protocol_url": position.protocol_url,
        "category": position.category,
        "asset_id": position.asset_id,
        "quantity": position.quantity,
        "price_usd": position.price_usd,
        "value_usd": position.value_usd,
        "apy": position.apy,
        "health_factor": position.health_factor,
        "provider": position.provider,
        "metadata_json": position.metadata_json,
        "raw_payload_hash": position.raw_payload_hash,
    }


def seed_database(session: Session) -> None:
    """Ensure static reference rows exist without creating portfolio data."""
    _ensure_chains(session)
    _ensure_okx_source_from_env(session)
    session.commit()


def query_monad_defi_coverage() -> DefiCoverageResponse:
    settings = get_settings()
    if not settings.moralis_api_key:
        raise ValueError("MORALIS_API_KEY is not configured")

    try:
        protocols = MoralisDefiCoverageClient(settings.moralis_api_key).get_supported_protocols("monad")
    except Exception:
        logger.exception("Moralis DeFi coverage query failed for chain=monad")
        raise

    protocol_names = sorted({protocol_display_name(protocol) for protocol in protocols})
    lower_names = {name.lower(): name for name in protocol_names}
    targets: List[DefiCoverageTarget] = []
    for target in MONAD_DEFI_COVERAGE_TARGETS:
        target_lower = target.lower()
        matches = [
            original_name
            for lower_name, original_name in lower_names.items()
            if target_lower in lower_name or lower_name in target_lower
        ]
        targets.append(
            DefiCoverageTarget(
                name=target,
                matched=bool(matches),
                matches=matches,
            )
        )

    logger.info(
        "Moralis Monad DeFi coverage: protocol_count=%d targets=%s",
        len(protocol_names),
        ", ".join(f"{target.name}={'yes' if target.matched else 'no'}" for target in targets),
    )
    logger.info("Moralis Monad DeFi protocols: %s", ", ".join(protocol_names) or "none")

    return DefiCoverageResponse(
        chain="monad",
        protocol_count=len(protocol_names),
        protocol_names=protocol_names,
        target_protocols=targets,
    )


def _source_to_api(source: SourceORM, evm_chain_ids: List[str]) -> Source:
    wallet = source.wallet
    exchange_account = source.exchange_account
    return Source(
        id=source.id,
        label=source.label,
        type=source.type,  # type: ignore[arg-type]
        provider=source.provider,
        enabled=source.enabled,
        chain_ids=evm_chain_ids if source.type == "wallet" else [],
        last_synced_at=_format_dt(source.last_synced_at) if source.last_synced_at else None,
        status=source.status,  # type: ignore[arg-type]
        status_message=source.status_message,
        address=wallet.address if wallet else None,
        exchange=exchange_account.exchange if exchange_account else None,  # type: ignore[arg-type]
        api_key_label=exchange_account.api_key_label if exchange_account else None,
    )


def _enabled_evm_chain_ids(session: Session) -> List[str]:
    stmt = (
        select(ChainORM.id)
        .where(ChainORM.chain_type == "evm", ChainORM.enabled.is_(True))
    )
    enabled_ids = set(session.scalars(stmt).all())
    return [chain_id for chain_id in SUPPORTED_EVM_CHAINS if chain_id in enabled_ids]


def list_sources(session: Session) -> List[Source]:
    evm_chain_ids = _enabled_evm_chain_ids(session)
    stmt = (
        select(SourceORM)
        .options(joinedload(SourceORM.wallet), joinedload(SourceORM.exchange_account))
        .order_by(SourceORM.created_at, SourceORM.id)
    )
    return [_source_to_api(source, evm_chain_ids) for source in session.scalars(stmt).all()]


def _latest_snapshot_timestamp(session: Session) -> Optional[datetime]:
    spot_timestamp = session.scalar(select(func.max(PositionSnapshotORM.timestamp)))
    defi_timestamp = session.scalar(select(func.max(DefiPositionSnapshotORM.timestamp)))
    timestamps = [timestamp for timestamp in (spot_timestamp, defi_timestamp) if timestamp is not None]
    return max(timestamps) if timestamps else None


def _latest_spot_snapshot_timestamp(session: Session) -> Optional[datetime]:
    return session.scalar(select(func.max(PositionSnapshotORM.timestamp)))


def _latest_defi_snapshot_timestamp(session: Session) -> Optional[datetime]:
    return session.scalar(select(func.max(DefiPositionSnapshotORM.timestamp)))


def list_holdings(session: Session) -> List[Holding]:
    latest_timestamp = _latest_spot_snapshot_timestamp(session)
    if latest_timestamp is None:
        return []

    stmt = (
        select(PositionSnapshotORM, SourceORM, AssetORM, ChainORM)
        .join(SourceORM, PositionSnapshotORM.source_id == SourceORM.id)
        .join(AssetORM, PositionSnapshotORM.asset_id == AssetORM.id)
        .outerjoin(ChainORM, PositionSnapshotORM.chain_id == ChainORM.id)
        .where(PositionSnapshotORM.timestamp == latest_timestamp)
        .order_by(PositionSnapshotORM.value_usd.desc())
    )
    holdings: List[Holding] = []
    for snapshot, source, asset, chain in session.execute(stmt).all():
        holdings.append(
            Holding(
                id=f"{source.id}-{asset.id}",
                source_id=source.id,
                source_label=source.label,
                source_type=source.type,  # type: ignore[arg-type]
                chain_id=chain.id if chain else None,
                chain_name=chain.name if chain else None,
                asset_symbol=asset.symbol,
                asset_name=asset.name,
                logo_url=_asset_logo_url(asset),
                quantity=_decimal_to_float(snapshot.quantity, 8),
                price_usd=_decimal_to_float(snapshot.price_usd, 8),
                value_usd=_decimal_to_float(snapshot.value_usd, 2),
            )
        )
    return holdings


def get_defi_portfolio(session: Session) -> DefiPortfolio:
    latest_timestamp = _latest_defi_snapshot_timestamp(session)
    if latest_timestamp is None:
        return DefiPortfolio(total_usd=0, protocols=[])

    stmt = (
        select(DefiPositionSnapshotORM, SourceORM, AssetORM, ChainORM)
        .join(SourceORM, DefiPositionSnapshotORM.source_id == SourceORM.id)
        .join(AssetORM, DefiPositionSnapshotORM.asset_id == AssetORM.id)
        .join(ChainORM, DefiPositionSnapshotORM.chain_id == ChainORM.id)
        .where(DefiPositionSnapshotORM.timestamp == latest_timestamp)
        .order_by(
            DefiPositionSnapshotORM.protocol_name,
            DefiPositionSnapshotORM.category,
            func.abs(DefiPositionSnapshotORM.value_usd).desc(),
        )
    )

    groups: Dict[str, List[DefiPosition]] = {}
    protocol_meta: Dict[str, tuple[str, str]] = {}
    for snapshot, source, asset, chain in session.execute(stmt).all():
        try:
            metadata = json.loads(snapshot.metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        token_id_value = metadata.get("token_id")
        unlock_time_value = metadata.get("unlock_time")
        position = DefiPosition(
            id=f"{snapshot.id}",
            source_id=source.id,
            source_label=source.label,
            chain_id=chain.id,
            chain_name=chain.name,
            protocol_slug=snapshot.protocol_slug,
            protocol_name=snapshot.protocol_name,
            protocol_url=snapshot.protocol_url,
            category=snapshot.category,  # type: ignore[arg-type]
            asset_symbol=asset.symbol,
            asset_name=asset.name,
            logo_url=_asset_logo_url(asset),
            quantity=_decimal_to_float(snapshot.quantity, 8),
            price_usd=_decimal_to_float(snapshot.price_usd, 8),
            value_usd=_decimal_to_float(snapshot.value_usd, 2),
            display_value_usd=abs(_decimal_to_float(snapshot.value_usd, 2)),
            apy=_decimal_to_float(snapshot.apy, 4) if snapshot.apy is not None else None,
            health_factor=_decimal_to_float(snapshot.health_factor, 4)
            if snapshot.health_factor is not None
            else None,
            token_id=str(token_id_value) if token_id_value is not None else None,
            unlock_time=str(unlock_time_value) if unlock_time_value else None,
        )
        groups.setdefault(snapshot.protocol_slug, []).append(position)
        protocol_meta[snapshot.protocol_slug] = (snapshot.protocol_name, snapshot.protocol_url)

    protocol_groups: List[DefiProtocolPositionGroup] = []
    total_usd = 0.0
    for protocol_slug, positions in groups.items():
        protocol_total = round(sum(position.value_usd for position in positions), 2)
        total_usd += protocol_total
        health_factors = [
            position.health_factor for position in positions if position.health_factor is not None
        ]
        protocol_name, protocol_url = protocol_meta[protocol_slug]
        protocol_groups.append(
            DefiProtocolPositionGroup(
                protocol_slug=protocol_slug,
                protocol_name=protocol_name,
                protocol_url=protocol_url,
                total_value_usd=protocol_total,
                health_factor=min(health_factors) if health_factors else None,
                positions=positions,
            )
        )

    protocol_groups.sort(key=lambda group: abs(group.total_value_usd), reverse=True)
    return DefiPortfolio(total_usd=round(total_usd, 2), protocols=protocol_groups)


def list_history(session: Session) -> List[HistoryPoint]:
    spot_stmt = (
        select(
            PositionSnapshotORM.timestamp,
            PositionSnapshotORM.source_id,
            PositionSnapshotORM.chain_id,
            func.sum(PositionSnapshotORM.value_usd),
        )
        .group_by(PositionSnapshotORM.timestamp, PositionSnapshotORM.source_id, PositionSnapshotORM.chain_id)
        .order_by(PositionSnapshotORM.timestamp, PositionSnapshotORM.source_id, PositionSnapshotORM.chain_id)
    )
    points = [
        HistoryPoint(
            timestamp=_format_dt(timestamp),
            source_id=source_id,
            chain_id=chain_id,
            value_usd=_decimal_to_float(value_usd or Decimal("0"), 2),
        )
        for timestamp, source_id, chain_id, value_usd in session.execute(spot_stmt).all()
    ]

    defi_stmt = (
        select(
            DefiPositionSnapshotORM.timestamp,
            DefiPositionSnapshotORM.source_id,
            DefiPositionSnapshotORM.chain_id,
            func.sum(DefiPositionSnapshotORM.value_usd),
        )
        .group_by(
            DefiPositionSnapshotORM.timestamp,
            DefiPositionSnapshotORM.source_id,
            DefiPositionSnapshotORM.chain_id,
        )
        .order_by(
            DefiPositionSnapshotORM.timestamp,
            DefiPositionSnapshotORM.source_id,
            DefiPositionSnapshotORM.chain_id,
        )
    )
    points.extend(
        HistoryPoint(
            timestamp=_format_dt(timestamp),
            source_id=source_id,
            chain_id=chain_id,
            value_usd=_decimal_to_float(value_usd or Decimal("0"), 2),
        )
        for timestamp, source_id, chain_id, value_usd in session.execute(defi_stmt).all()
    )
    points.sort(key=lambda point: (point.timestamp, point.source_id, point.chain_id or ""))
    return points


def get_summary(session: Session) -> PortfolioSummary:
    latest_timestamp = _latest_snapshot_timestamp(session)
    if latest_timestamp is None:
        return PortfolioSummary(
            total_usd=0,
            change_24h_usd=0,
            change_24h_pct=0,
            updated_at=_format_dt(_now()),
            source_count=0,
            chain_count=0,
            asset_count=0,
        )

    spot_latest_timestamp = _latest_spot_snapshot_timestamp(session)
    defi_latest_timestamp = _latest_defi_snapshot_timestamp(session)
    first_spot_timestamp = session.scalar(select(func.min(PositionSnapshotORM.timestamp)))
    first_defi_timestamp = session.scalar(select(func.min(DefiPositionSnapshotORM.timestamp)))
    first_candidates = [
        timestamp for timestamp in (first_spot_timestamp, first_defi_timestamp) if timestamp is not None
    ]
    first_timestamp = min(first_candidates) if first_candidates else latest_timestamp

    spot_total = session.scalar(
        select(func.sum(PositionSnapshotORM.value_usd)).where(
            PositionSnapshotORM.timestamp == spot_latest_timestamp
        )
    ) or Decimal("0")
    defi_total = session.scalar(
        select(func.sum(DefiPositionSnapshotORM.value_usd)).where(
            DefiPositionSnapshotORM.timestamp == defi_latest_timestamp
        )
    ) or Decimal("0")
    first_spot_total = session.scalar(
        select(func.sum(PositionSnapshotORM.value_usd)).where(
            PositionSnapshotORM.timestamp == first_timestamp
        )
    ) or Decimal("0")
    first_defi_total = session.scalar(
        select(func.sum(DefiPositionSnapshotORM.value_usd)).where(
            DefiPositionSnapshotORM.timestamp == first_timestamp
        )
    ) or Decimal("0")
    total = spot_total + defi_total
    first_total = first_spot_total + first_defi_total
    source_count = session.scalar(select(func.count(SourceORM.id))) or 0
    chain_count = session.scalar(
        select(func.count(func.distinct(PositionSnapshotORM.chain_id))).where(
            PositionSnapshotORM.timestamp == spot_latest_timestamp,
            PositionSnapshotORM.chain_id.is_not(None),
        )
    ) or 0
    defi_chain_ids = set(
        session.scalars(
            select(DefiPositionSnapshotORM.chain_id).where(
                DefiPositionSnapshotORM.timestamp == defi_latest_timestamp
            )
        ).all()
    )
    if defi_chain_ids:
        spot_chain_ids = set(
            session.scalars(
                select(PositionSnapshotORM.chain_id).where(
                    PositionSnapshotORM.timestamp == spot_latest_timestamp,
                    PositionSnapshotORM.chain_id.is_not(None),
                )
            ).all()
        )
        chain_count = len(spot_chain_ids | defi_chain_ids)

    spot_asset_ids = set(
        session.scalars(
            select(PositionSnapshotORM.asset_id).where(
                PositionSnapshotORM.timestamp == spot_latest_timestamp
            )
        ).all()
    )
    defi_asset_ids = set(
        session.scalars(
            select(DefiPositionSnapshotORM.asset_id).where(
                DefiPositionSnapshotORM.timestamp == defi_latest_timestamp
            )
        ).all()
    )
    asset_count = len(spot_asset_ids | defi_asset_ids)

    total_float = float(total)
    first_total_float = float(first_total)
    change_24h_usd = round(total_float - first_total_float, 2)
    change_24h_pct = round((change_24h_usd / first_total_float) * 100, 2) if first_total_float else 0

    return PortfolioSummary(
        total_usd=round(total_float, 2),
        change_24h_usd=change_24h_usd,
        change_24h_pct=change_24h_pct,
        updated_at=_format_dt(latest_timestamp),
        source_count=source_count,
        chain_count=chain_count,
        asset_count=asset_count,
    )


def create_wallet_source(session: Session, payload: CreateWalletSourceRequest) -> Source:
    address = _normalize_evm_address(payload.address)
    existing_wallets = session.scalars(select(WalletORM)).all()
    if any(wallet.address.lower() == address for wallet in existing_wallets):
        raise ValueError("Wallet address is already added")

    created_at = _now()
    label = payload.label.strip() if payload.label else _short_address(address)
    source = SourceORM(
        id=_stable_id("wallet", address),
        label=label,
        type="wallet",
        provider="Moralis",
        enabled=True,
        last_synced_at=created_at,
        status="warning",
        status_message="Not synced yet. Run Sync now to fetch live Monad and Ethereum balances.",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(source)
    session.add(
        WalletORM(
            id=source.id,
            source_id=source.id,
            address=address,
            label=label,
            default_enabled=True,
        )
    )
    session.commit()
    session.refresh(source)
    return _source_to_api(source, _enabled_evm_chain_ids(session))


def create_exchange_source(session: Session, payload: CreateExchangeSourceRequest) -> Source:
    exchange = payload.exchange.lower()
    if exchange != "okx":
        raise ValueError("Only OKX exchange sources are supported right now")

    settings = get_settings()
    missing_settings = _missing_okx_settings()
    if missing_settings:
        raise ValueError(
            "Configure "
            + ", ".join(missing_settings)
            + " in .env, then restart the backend so the OKX source is loaded automatically."
        )
    api_key = settings.okx_api_key
    if api_key is None:
        raise ValueError("Configure OKX_API_KEY in .env, then restart the backend.")

    source_id = _okx_source_id(api_key)
    if session.get(SourceORM, source_id):
        raise ValueError("OKX account from .env is already added")

    created_at = _now()
    label = _okx_source_label()
    source = SourceORM(
        id=source_id,
        label=label,
        type="exchange",
        provider="OKX",
        enabled=True,
        last_synced_at=created_at,
        status="warning",
        status_message=(
            "OKX source added from .env. Run Sync now to fetch live OKX balances."
        ),
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(source)
    _upsert_okx_exchange_account(session, source_id, api_key)
    session.commit()
    session.refresh(source)
    return _source_to_api(source, _enabled_evm_chain_ids(session))


def _balance_adapter_for_source(source: SourceORM) -> BalanceAdapter:
    settings = get_settings()
    if source.type == "wallet" and source.provider == "Moralis" and settings.moralis_api_key:
        return MoralisEvmBalanceAdapter(
            settings.moralis_api_key,
            hidden_contract_addresses={
                "monad": (
                    NEVERLAND_DERIVATIVE_TOKEN_ADDRESSES
                    | LEVERUP_DERIVATIVE_TOKEN_ADDRESSES
                    | CURVANCE_CTOKEN_ADDRESSES
                ),
                "ethereum": set(),
            },
        )
    if source.type == "wallet" and source.provider == "Moralis":
        return NoopBalanceAdapter("MORALIS_API_KEY is not configured; live EVM wallet sync skipped.")
    if source.type == "exchange" and source.provider == "OKX":
        missing_settings = _missing_okx_settings()
        if missing_settings:
            return NoopBalanceAdapter(
                f"{', '.join(missing_settings)} not configured; live OKX sync skipped."
            )
        api_key = settings.okx_api_key
        api_secret = settings.okx_api_secret
        api_passphrase = settings.okx_api_passphrase
        if api_key is None or api_secret is None or api_passphrase is None:
            return NoopBalanceAdapter("OKX credentials are incomplete; live OKX sync skipped.")
        if (
            source.exchange_account
            and source.exchange_account.api_key_label != _mask_api_key(api_key)
        ):
            return NoopBalanceAdapter(
                "Configured OKX_API_KEY does not match this OKX source; "
                "live OKX sync skipped to avoid valuing the wrong account."
            )
        return OkxBalanceAdapter(
            api_key,
            api_secret,
            api_passphrase,
        )
    return NoopBalanceAdapter(f"{source.provider} live sync is not implemented yet.")


def _defi_adapters_for_source(source: SourceORM) -> List[DefiProtocolAdapter]:
    if source.type != "wallet" or source.wallet is None:
        return []
    settings = get_settings()
    perpl_account = next(
        (
            account
            for account in settings.perpl_accounts
            if account.wallet_address == source.wallet.address.lower()
        ),
        None,
    )
    return [
        NeverlandDefiAdapter(settings.monad_rpc_url, moralis_api_key=settings.moralis_api_key),
        LeverUpDefiAdapter(settings.monad_rpc_url, moralis_api_key=settings.moralis_api_key),
        TownsquareDefiAdapter(settings.monad_rpc_url),
        CurvanceDefiAdapter(settings.monad_rpc_url),
        AaveV3DefiAdapter(
            AaveDeployment(
                local_chain_id="ethereum",
                chain_name="Ethereum",
                rpc_url=settings.ethereum_rpc_url,
                pool_address=AAVE_V3_ETHEREUM_POOL,
                oracle_address=AAVE_V3_ETHEREUM_ORACLE,
                protocol_data_provider_address=AAVE_V3_ETHEREUM_PROTOCOL_DATA_PROVIDER,
            )
        ),
        AaveV3DefiAdapter(
            AaveDeployment(
                local_chain_id="monad",
                chain_name="Monad",
                rpc_url=settings.monad_rpc_url,
                pool_address=settings.aave_monad_pool_address,
                oracle_address=settings.aave_monad_oracle_address,
                protocol_data_provider_address=settings.aave_monad_protocol_data_provider_address,
            )
        ),
        PerplDefiAdapter(
            api_url=settings.perpl_api_url,
            chain_id=settings.perpl_chain_id,
            api_key=perpl_account.api_key if perpl_account else None,
            api_key_secret=perpl_account.api_key_secret if perpl_account else None,
            wallet_address=perpl_account.wallet_address if perpl_account else source.wallet.address,
        ),
    ]


def run_manual_sync(session: Session) -> SyncRunResponse:
    """Fetch a live snapshot for every enabled source with a configured adapter."""
    snapshot_timestamp = _now()
    sources = session.scalars(
        select(SourceORM)
        .options(joinedload(SourceORM.wallet), joinedload(SourceORM.exchange_account))
        .where(SourceORM.enabled.is_(True))
        .order_by(SourceORM.created_at, SourceORM.id)
    ).all()

    sync_run_ids: List[int] = []
    positions_written = 0
    failed_sources = 0
    logger.info("Manual sync started for %d enabled sources", len(sources))

    for source in sources:
        sync_run = SyncRunORM(
            source_id=source.id,
            provider=source.provider,
            started_at=snapshot_timestamp,
            finished_at=None,
            status="running",
            error=None,
        )
        session.add(sync_run)
        session.flush()
        sync_run_ids.append(sync_run.id)

        try:
            adapter = _balance_adapter_for_source(source)
            result = adapter.fetch_positions(session, source, snapshot_timestamp)
            positions = result.positions
            defi_positions: List[RawDefiPosition] = []
            defi_messages: List[str] = []
            defi_failures: List[str] = []

            for defi_adapter in _defi_adapters_for_source(source):
                try:
                    defi_result = defi_adapter.fetch_positions(session, source, snapshot_timestamp)
                    defi_positions.extend(defi_result.positions)
                    if defi_result.status_message:
                        defi_messages.append(defi_result.status_message)
                except Exception as exc:
                    failure_message = f"{defi_adapter.__class__.__name__} sync failed: {exc}"
                    defi_messages.append(failure_message)
                    defi_failures.append(failure_message)
                    logger.exception(
                        "DeFi sync failed for source_id=%s provider=%s adapter=%s",
                        source.id,
                        source.provider,
                        defi_adapter.__class__.__name__,
                    )

            status_messages = [
                message for message in [result.status_message, *defi_messages] if message
            ]
            if defi_failures:
                raise RuntimeError(
                    "Skipped database update because one or more DeFi adapters failed. "
                    + " ".join(status_messages)
                )

            _ensure_assets_for_positions(session, positions)
            _ensure_assets_for_defi_positions(session, defi_positions)
            session.flush()
            session.bulk_insert_mappings(
                PositionSnapshotORM,
                [_snapshot_row_from_position(position, snapshot_timestamp) for position in positions],
            )
            session.bulk_insert_mappings(
                DefiPositionSnapshotORM,
                [
                    _defi_snapshot_row_from_position(position, snapshot_timestamp)
                    for position in defi_positions
                ],
            )
            positions_written += len(positions) + len(defi_positions)
            source.status = "warning" if result.status == "warning" else "ok"
            source.status_message = " ".join(status_messages) if status_messages else None
            sync_run.status = source.status
            if source.status == "warning":
                logger.warning(
                    "Sync warning for source_id=%s provider=%s: %s",
                    source.id,
                    source.provider,
                    source.status_message,
                )
            else:
                logger.info(
                    "Sync completed for source_id=%s provider=%s positions=%d",
                    source.id,
                    source.provider,
                    len(positions) + len(defi_positions),
                )
        except Exception as exc:  # pragma: no cover - defensive path for future real adapters.
            failed_sources += 1
            source.status = "error"
            source.status_message = str(exc)
            sync_run.status = "error"
            sync_run.error = str(exc)
            logger.exception(
                "Sync failed for source_id=%s provider=%s",
                source.id,
                source.provider,
            )
        finally:
            source.updated_at = snapshot_timestamp
            sync_run.finished_at = snapshot_timestamp

    status_value: Literal["ok", "partial", "error"]
    if failed_sources == 0:
        status_value = "ok"
    elif failed_sources == len(sources):
        status_value = "error"
    else:
        status_value = "partial"

    if failed_sources > 0 and positions_written > 0:
        session.execute(
            delete(PositionSnapshotORM).where(PositionSnapshotORM.timestamp == snapshot_timestamp)
        )
        session.execute(
            delete(DefiPositionSnapshotORM).where(DefiPositionSnapshotORM.timestamp == snapshot_timestamp)
        )
        logger.warning(
            "Discarded %d position rows for partial sync timestamp=%s to avoid incomplete portfolio history",
            positions_written,
            snapshot_timestamp.isoformat(),
        )
        positions_written = 0

    if status_value == "ok":
        for source in sources:
            source.last_synced_at = snapshot_timestamp

    session.commit()

    logger.info(
        "Manual sync finished status=%s sources_synced=%d positions_written=%d",
        status_value,
        len(sources) - failed_sources,
        positions_written,
    )

    return SyncRunResponse(
        status=status_value,
        timestamp=_format_dt(snapshot_timestamp),
        sources_synced=len(sources) - failed_sources,
        positions_written=positions_written,
        sync_run_ids=sync_run_ids,
    )


def delete_source(session: Session, source_id: str) -> bool:
    source = session.get(SourceORM, source_id)
    if source is None:
        return False

    session.execute(delete(PositionSnapshotORM).where(PositionSnapshotORM.source_id == source_id))
    session.execute(delete(DefiPositionSnapshotORM).where(DefiPositionSnapshotORM.source_id == source_id))
    session.execute(delete(SyncRunORM).where(SyncRunORM.source_id == source_id))
    session.execute(delete(WalletORM).where(WalletORM.source_id == source_id))
    session.execute(delete(ExchangeAccountORM).where(ExchangeAccountORM.source_id == source_id))
    session.delete(source)
    session.commit()
    return True
