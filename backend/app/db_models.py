from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM tables."""


class ChainORM(Base):
    __tablename__ = "chains"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    chain_type: Mapped[str] = mapped_column(String(32), nullable=False)
    native_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    chain_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    assets: Mapped[List["AssetORM"]] = relationship(back_populates="chain")


class SourceORM(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    wallet: Mapped[Optional["WalletORM"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    exchange_account: Mapped[Optional["ExchangeAccountORM"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class WalletORM(Base):
    __tablename__ = "wallets"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    address: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    source: Mapped[SourceORM] = relationship(back_populates="wallet")


class ExchangeAccountORM(Base):
    __tablename__ = "exchange_accounts"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    credentials_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    api_key_label: Mapped[str] = mapped_column(String(80), nullable=False)
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    source: Mapped[SourceORM] = relationship(back_populates="exchange_account")


class AssetORM(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    chain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("chains.id"), nullable=True)
    contract_address: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decimals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    external_ids_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    chain: Mapped[Optional[ChainORM]] = relationship(back_populates="assets")


class PositionSnapshotORM(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("chains.id"), index=True, nullable=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    value_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_payload_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class DefiPositionSnapshotORM(Base):
    __tablename__ = "defi_position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chain_id: Mapped[str] = mapped_column(ForeignKey("chains.id"), index=True, nullable=False)
    protocol_slug: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    protocol_name: Mapped[str] = mapped_column(String(120), nullable=False)
    protocol_url: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    value_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    apy: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 18), nullable=True)
    health_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 18), nullable=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    raw_payload_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class SyncRunORM(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
