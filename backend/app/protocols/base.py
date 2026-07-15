from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional, Protocol

from sqlalchemy.orm import Session

from ..db_models import SourceORM


DefiCategory = Literal["deposit", "borrow", "reward", "locked", "staked", "perp"]


@dataclass(frozen=True)
class RawDefiPosition:
    """Provider-neutral DeFi position normalized before it is stored."""

    source_id: str
    chain_id: str
    protocol_slug: str
    protocol_name: str
    protocol_url: str
    category: DefiCategory
    asset_id: str
    asset_symbol: str
    asset_name: str
    decimals: Optional[int]
    contract_address: Optional[str]
    quantity: Decimal
    price_usd: Decimal
    value_usd: Decimal
    apy: Optional[Decimal]
    health_factor: Optional[Decimal]
    metadata_json: str
    provider: str
    raw_payload_hash: Optional[str]


@dataclass(frozen=True)
class DefiFetchResult:
    positions: List[RawDefiPosition]
    status_message: Optional[str] = None


class DefiProtocolAdapter(Protocol):
    """Interface for manual protocol integrations such as Neverland or Leverup."""

    def fetch_positions(
        self,
        session: Session,
        source: SourceORM,
        timestamp: datetime,
    ) -> DefiFetchResult:
        """Return normalized DeFi positions for one wallet source."""
