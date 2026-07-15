export type SourceType = "wallet" | "exchange";
export type SourceStatus = "ok" | "warning" | "error";

// These interfaces mirror the FastAPI/Pydantic response models. Keep them in
// sync with backend/app/models.py until generated API types are introduced.
export interface Source {
  id: string;
  label: string;
  type: SourceType;
  provider: string;
  enabled: boolean;
  chain_ids: string[];
  last_synced_at?: string | null;
  status: SourceStatus;
  status_message?: string | null;
  address?: string | null;
  exchange?: "okx" | null;
  api_key_label?: string | null;
}

export interface CreateWalletSourcePayload {
  address: string;
  label?: string;
}

export interface Holding {
  id: string;
  source_id: string;
  source_label: string;
  source_type: SourceType;
  chain_id: string | null;
  chain_name: string | null;
  asset_symbol: string;
  asset_name: string;
  logo_url?: string | null;
  quantity: number;
  price_usd: number;
  value_usd: number;
}

export interface HistoryPoint {
  timestamp: string;
  source_id: string;
  chain_id: string | null;
  value_usd: number;
}

export interface PortfolioSummary {
  total_usd: number;
  change_24h_usd: number;
  change_24h_pct: number;
  updated_at: string;
  source_count: number;
  chain_count: number;
  asset_count: number;
}

export interface PortfolioHistoryResponse {
  points: HistoryPoint[];
}

export interface PortfolioHoldingsResponse {
  holdings: Holding[];
}

export type DefiPositionCategory = "deposit" | "borrow" | "reward" | "locked" | "staked" | "perp";

export interface DefiPosition {
  id: string;
  source_id: string;
  source_label: string;
  chain_id: string;
  chain_name: string;
  protocol_slug: string;
  protocol_name: string;
  protocol_url: string;
  category: DefiPositionCategory;
  asset_symbol: string;
  asset_name: string;
  logo_url?: string | null;
  quantity: number;
  price_usd: number;
  value_usd: number;
  display_value_usd: number;
  apy?: number | null;
  health_factor?: number | null;
  token_id?: string | null;
  unlock_time?: string | null;
}

export interface DefiProtocolPositionGroup {
  protocol_slug: string;
  protocol_name: string;
  protocol_url: string;
  total_value_usd: number;
  health_factor?: number | null;
  positions: DefiPosition[];
}

export interface DefiPortfolioResponse {
  total_usd: number;
  protocols: DefiProtocolPositionGroup[];
}

export interface SyncRunResponse {
  status: "ok" | "partial" | "error";
  timestamp: string;
  sources_synced: number;
  positions_written: number;
  sync_run_ids: number[];
}

export interface ChartPoint {
  timestamp: string;
  totalUsd: number;
}

// Venue is the UI-level filter grouping: EVM chains and exchange accounts sit
// side by side even though the backend stores exchange holdings with no chain.
export interface VenueOption {
  id: string;
  label: string;
  kind: "chain" | "exchange";
}
