# Personal Portfolio Tracker Spec

Date: 2026-07-07
Status: Draft v0.1

## Goal

Build a local-first portfolio tracker for personal finance with Python as the main programming language.

The first version should track crypto holdings across:

- Monad wallets
- Ethereum mainnet wallets
- OKX exchange balances

The system should support multiple wallet addresses, let each wallet and chain be toggled on or off, show total USD portfolio value, and store historical portfolio value snapshots for charting.

Future support should be easy to add for:

- More EVM chains
- Non-EVM chains such as Solana
- More exchanges such as Binance
- Stocks and other traditional assets

## Product Requirements

### Portfolio Overview

The dashboard should show:

- Total USD value of the active portfolio
- Breakdown by source, such as each wallet and OKX
- Breakdown by chain, initially Monad and Ethereum
- Breakdown by asset, such as MON, ETH, USDC, USDT
- Staleness indicator per source, so it is clear when a source last synced
- Errors per source, without breaking the entire dashboard

### Toggles and Views

The user should be able to:

- Enable or disable each wallet
- Enable or disable each chain
- Enable or disable OKX
- Toggle chart series, including total, per-chain, per-wallet, and per-asset
- Save common portfolio views, such as "All", "Monad only", "Ethereum only", or "Cold wallets"

### Historical Value Chart

The tracker should:

- Store periodic balance snapshots, probably every 15 minutes or hourly
- Store asset balances, USD prices, and computed USD values at snapshot time
- Display historical portfolio value using a clean interactive graph
- Recompute displayed history based on active wallet, chain, exchange, and asset toggles
- Start history from the day the tracker is deployed

Historical backfill is not required for MVP. It can be added later using transfer history, historical balances, and historical prices.

### Useful Extra Features

Recommended but not required for the first MVP:

- Asset allocation pie chart
- Chain allocation chart
- Stablecoin versus volatile asset split
- Top movers since the previous snapshot
- Manual asset entries for assets that APIs cannot see
- CSV export
- Alerts for large portfolio value moves or stale syncs
- Provider provenance, showing which API produced each balance or price
- Optional NFT tracking, off by default

## Recommended Stack

- Backend: Python 3.12, FastAPI
- Storage: SQLite for v1, using SQLModel or SQLAlchemy with a Postgres-compatible schema
- Jobs: APScheduler for local periodic syncs
- Frontend: React, TypeScript, and Vite
- Styling and UI: Tailwind CSS and shadcn/ui
- Charts: Apache ECharts through a React wrapper
- API state: TanStack Query for frontend data fetching, caching, refreshes, loading states, and errors
- Local development: FastAPI on `localhost:8000` and Vite on `localhost:5173`
- Local production mode: FastAPI can serve the built frontend assets
- Secrets: `.env` for development, with a path to OS keyring or encrypted secret storage later
- Numeric precision: Python `Decimal` for balances and values, not floats

## Provider Decisions

### Monad

Use Moralis as the primary Monad data provider for MVP.

Reason: a portfolio tracker needs indexed wallet data, not only raw JSON-RPC. Raw RPC can fetch native balances, but it does not conveniently discover every ERC-20 token held by an address or provide token metadata, prices, and historical wallet data. Moralis is listed in Monad's common-data provider table as supporting Monad data for native and ERC-20 balances, prices, transfers, wallet history, and streams or webhooks.

Source:

- Monad common data providers: https://docs.monad.xyz/tooling-and-infra/indexers/common-data
- Moralis EVM Data API overview: https://docs.moralis.com/data-api/evm/overview

Use QuickNode as the raw RPC fallback for Monad.

Reason: if the tracker needs low-level JSON-RPC reads, direct chain checks, webhooks, or historical backfills, QuickNode supports Monad mainnet and testnet and has strong general-purpose infrastructure.

Source:

- Monad RPC providers: https://docs.monad.xyz/tooling-and-infra/rpc-providers
- QuickNode Monad page: https://www.quicknode.com/chains/monad

Do not use Alchemy as the primary Monad mainnet provider unless the user's Alchemy dashboard confirms mainnet support. Alchemy's own Monad quickstart currently says it supports Monad testnet.

Source:

- Alchemy Monad quickstart: https://www.alchemy.com/docs/reference/monad-api-quickstart

### Ethereum Mainnet

Use an EVM indexed-data provider for balances and token metadata. Since Moralis is already needed for Monad, it is a good first choice for Ethereum as well. This keeps the adapter surface smaller in MVP.

Alchemy remains a reasonable alternative for Ethereum token balances and prices if needed, especially if the user already has an Alchemy account.

Sources:

- Alchemy tokens by wallet: https://www.alchemy.com/docs/data/portfolio-apis/portfolio-api-endpoints/portfolio-api-endpoints/get-tokens-by-address
- Moralis EVM Data API overview: https://docs.moralis.com/data-api/evm/overview

### OKX

Use OKX read-only API credentials to fetch exchange account balances.

The tracker should require API permissions for reading balances only. It should not require trading or withdrawal permissions.

Source:

- OKX API docs: https://www.okx.com/docs-v5/en/

### Prices

Use provider-returned USD prices when available from Moralis or another indexed source.

Use CoinGecko as the fallback historical price provider, especially for chart reconstruction and historical valuation. CoinGecko's market chart range endpoint returns timestamped prices and supports hourly or daily granularity depending on the query range and plan.

Source:

- CoinGecko market chart range endpoint: https://docs.coingecko.com/reference/coins-id-market-chart-range

## Architecture

The system should be built around adapters. The core portfolio engine should not care whether positions come from Monad, Ethereum, OKX, Binance, Solana, or a stock broker.

The web app should be split into a Python backend and a browser frontend:

```text
backend/
  app/
    api/
    core/
    db/
    adapters/
    services/

frontend/
  src/
    api/
    charts/
    components/
    pages/
    state/
```

### Core Concepts

- Source: a wallet, exchange account, manual account, or future broker account
- Chain: Monad, Ethereum, Solana, and future networks
- Asset: native token, ERC-20, exchange asset, stock, or manual asset
- Position snapshot: balance of one asset from one source at one point in time
- Price point: USD price for one asset at one point in time
- Portfolio view: saved toggle and filter state

### Adapter Interfaces

```python
class BalanceAdapter:
    source_type: str

    def fetch_positions(self, source, as_of) -> list[RawPosition]:
        ...


class PriceAdapter:
    def get_current_prices(self, assets) -> dict[AssetId, PricePoint]:
        ...

    def get_historical_prices(self, assets, start, end) -> list[PricePoint]:
        ...


class ExchangeAdapter:
    exchange: str

    def fetch_balances(self, credentials) -> list[RawPosition]:
        ...
```

### Initial Adapters

- `MoralisEvmBalanceAdapter`
  - Handles Monad and Ethereum wallet balances.
  - Fetches native token and ERC-20 balances.
  - Includes token metadata and USD prices when available.

- `QuickNodeMonadRpcAdapter`
  - Optional fallback for raw Monad JSON-RPC reads.
  - Useful for native balance verification and future low-level features.

- `OkxExchangeAdapter`
  - Fetches OKX balances using read-only API credentials.
  - Normalizes exchange balances into the same position model as wallet balances.

- `CoinGeckoPriceAdapter`
  - Fetches current and historical prices when the balance provider does not provide suitable pricing.

## Data Model

### `chains`

- `id`
- `slug`
- `name`
- `chain_type`, such as `evm`, `solana`, `stock_market`
- `native_symbol`
- `chain_id`, nullable for non-EVM chains
- `enabled`

### `sources`

- `id`
- `type`, such as `wallet`, `exchange`, `manual`, `broker`
- `label`
- `enabled`
- `created_at`
- `updated_at`

### `wallets`

- `id`
- `source_id`
- `address`
- `label`
- `default_enabled`

### `exchange_accounts`

- `id`
- `source_id`
- `exchange`, initially `okx`
- `credentials_ref`
- `default_enabled`

### `assets`

- `id`
- `asset_class`, such as `crypto`, `stock`, `cash`
- `symbol`
- `name`
- `chain_id`, nullable for exchange-only or stock assets
- `contract_address`, nullable for native tokens and non-token assets
- `decimals`
- `external_ids`, JSON field for CoinGecko ID, Moralis ID, exchange symbols, etc.

### `position_snapshots`

- `id`
- `timestamp`
- `source_id`
- `chain_id`, nullable for exchange-only assets
- `asset_id`
- `quantity`
- `price_usd`
- `value_usd`
- `provider`
- `raw_payload_hash`, optional

### `sync_runs`

- `id`
- `source_id`
- `provider`
- `started_at`
- `finished_at`
- `status`
- `error`

### `saved_views`

- `id`
- `name`
- `filters_json`
- `created_at`
- `updated_at`

## Snapshot and Valuation Flow

1. Scheduler starts a sync run.
2. For each enabled source:
   - Fetch raw balances through the correct adapter.
   - Normalize raw balances into `RawPosition` records.
   - Resolve or create canonical `Asset` records.
   - Attach current USD prices from the source response or price adapter.
   - Save `position_snapshots`.
3. Compute aggregate values at query time based on active toggles.
4. Render charts from stored snapshots rather than calling APIs during chart rendering.

## MVP Scope

Included:

- Add/edit/delete wallet sources
- Add/edit/delete OKX account source
- Sync Monad wallet balances through Moralis
- Sync Ethereum wallet balances through Moralis
- Sync OKX balances through OKX API
- Store snapshots in SQLite
- Display current total USD value
- Display breakdown by source, chain, and asset
- Display historical total value chart
- Toggle wallets, chains, OKX, and chart series

Excluded from MVP:

- Historical backfill before tracker setup date
- NFT valuation
- DeFi LP and lending positions
- Tax lots
- PnL attribution
- Solana
- Binance
- Stocks
- Mobile app

## Credentials Needed

Before implementation, the user should provide or create:

- Moralis API key
- OKX read-only API key, secret, and passphrase
- Wallet address list with human-readable labels
- Optional QuickNode Monad RPC endpoint
- Optional CoinGecko API key

The OKX API key should be read-only. It should not have trading or withdrawal permissions.

## Open Questions

- Snapshot interval: 15 minutes, hourly, or manual-only at first?
- Should tiny balances below a configurable USD threshold be hidden by default?
- Should exchange balances be grouped as one OKX source or split by OKX account/subaccount?
- Should stablecoins be valued at provider price or pinned near 1 USD with deviation warnings?

## Future Extensions

### More Chains

Add a new chain row and a new adapter if the chain is not covered by an existing EVM adapter.

Examples:

- Base or Arbitrum: likely reuse `MoralisEvmBalanceAdapter`
- Solana: add `SolanaBalanceAdapter`
- Bitcoin: add `BitcoinBalanceAdapter`

### More Exchanges

Add one exchange adapter per exchange.

Examples:

- `BinanceExchangeAdapter`
- `CoinbaseExchangeAdapter`
- `KrakenExchangeAdapter`

### Stocks

Add:

- `asset_class = stock`
- `BrokerAdapter`
- A market data provider for current and historical stock prices
- Manual positions or broker API integration

The portfolio engine should continue using the same position snapshot and price point model.
