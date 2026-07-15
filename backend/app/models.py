from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]


# Source is the user-facing connection: either a wallet address or an exchange
# account. Exchange secrets are never returned by this model.
class Source(BaseModel):
    id: str
    label: str
    type: Literal["wallet", "exchange"]
    provider: str
    enabled: bool
    chain_ids: List[str]
    last_synced_at: Optional[str] = None
    status: Literal["ok", "warning", "error"]
    status_message: Optional[str] = None
    address: Optional[str] = None
    exchange: Optional[Literal["okx"]] = None
    api_key_label: Optional[str] = None


class CreateWalletSourceRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$")
    label: Optional[str] = None


class CreateExchangeSourceRequest(BaseModel):
    exchange: Literal["okx"] = "okx"


# Holdings are normalized positions. Chain-specific wallets and exchange assets
# share this shape so the frontend can aggregate them uniformly.
class Holding(BaseModel):
    id: str
    source_id: str
    source_label: str
    source_type: Literal["wallet", "exchange"]
    chain_id: Optional[str]
    chain_name: Optional[str]
    asset_symbol: str
    asset_name: str
    logo_url: Optional[str] = None
    quantity: float
    price_usd: float
    value_usd: float


# History points stay at source plus chain/exchange granularity so toggles can
# recompute the displayed chart without another API call.
class HistoryPoint(BaseModel):
    timestamp: str
    source_id: str
    chain_id: Optional[str]
    value_usd: float


class PortfolioSummary(BaseModel):
    total_usd: float
    change_24h_usd: float
    change_24h_pct: float
    updated_at: str
    source_count: int
    chain_count: int
    asset_count: int


class PortfolioHistory(BaseModel):
    points: List[HistoryPoint]


class PortfolioHoldings(BaseModel):
    holdings: List[Holding]


class DefiPosition(BaseModel):
    id: str
    source_id: str
    source_label: str
    chain_id: str
    chain_name: str
    protocol_slug: str
    protocol_name: str
    protocol_url: str
    category: Literal["deposit", "borrow", "reward", "locked", "staked", "perp"]
    asset_symbol: str
    asset_name: str
    logo_url: Optional[str] = None
    quantity: float
    price_usd: float
    value_usd: float
    display_value_usd: float
    apy: Optional[float] = None
    health_factor: Optional[float] = None
    token_id: Optional[str] = None
    unlock_time: Optional[str] = None


class DefiProtocolPositionGroup(BaseModel):
    protocol_slug: str
    protocol_name: str
    protocol_url: str
    total_value_usd: float
    health_factor: Optional[float] = None
    positions: List[DefiPosition]


class DefiPortfolio(BaseModel):
    total_usd: float
    protocols: List[DefiProtocolPositionGroup]


class SyncRunResponse(BaseModel):
    status: Literal["ok", "partial", "error"]
    timestamp: str
    sources_synced: int
    positions_written: int
    sync_run_ids: List[int]


class DefiCoverageTarget(BaseModel):
    name: str
    matched: bool
    matches: List[str]


class DefiCoverageResponse(BaseModel):
    chain: str
    protocol_count: int
    protocol_names: List[str]
    target_protocols: List[DefiCoverageTarget]
