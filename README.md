# PortfolioTracker

Local-first web app for tracking a personal crypto portfolio across Monad, Ethereum, and OKX.

## Current Status

The app uses a SQLite-backed local dashboard with live snapshot sync.

- Monad and Ethereum wallet token balances are fetched through Moralis.
- OKX read-only account balances are fetched through the OKX v5 API.
- Monad DeFi positions are fetched through protocol adapters for Neverland, LeverUp, TownSquare, Perpl, and Curvance.
- Aave positions are fetched on Ethereum. Aave on Monad is also supported once official Monad deployment addresses are configured in `.env`.
- Portfolio history is built from successful sync snapshots only. If a source or DeFi adapter fails during a sync, partial rows are discarded so the chart does not record an incomplete portfolio value.

## Environment

Copy the example env file and fill in the credentials you want enabled:

```bash
cp .env.example .env
```

Required for live wallet reads:

```bash
MORALIS_API_KEY=
```

The app intentionally aborts startup when `MORALIS_API_KEY` is missing. This is the primary data provider for wallet balances and several DeFi price reads, so running without it would produce misleading partial data.

Optional RPC overrides:

```bash
MONAD_RPC_URL=
ETHEREUM_RPC_URL=
```

Optional Aave on Monad deployment addresses:

```bash
AAVE_MONAD_POOL_ADDRESS=
AAVE_MONAD_ORACLE_ADDRESS=
AAVE_MONAD_PROTOCOL_DATA_PROVIDER_ADDRESS=
```

Optional OKX read-only balances:

```bash
OKX_ACCOUNT_LABEL=
OKX_API_KEY=
OKX_API_SECRET=
OKX_API_PASSPHRASE=
```

OKX credentials and the optional display label are not entered in the browser UI.
Configure them in `.env`, then restart the backend. The OKX source is created
or refreshed automatically on startup.

Optional Perpl credentials, one block per wallet that has a Perpl account:

```bash
PERPL_ACCOUNT_1_WALLET_ADDRESS=0x...
PERPL_ACCOUNT_1_API_KEY=
PERPL_ACCOUNT_1_API_KEY_SECRET=

PERPL_ACCOUNT_2_WALLET_ADDRESS=0x...
PERPL_ACCOUNT_2_API_KEY=
PERPL_ACCOUNT_2_API_KEY_SECRET=
```

Legacy single-wallet `PERPL_WALLET_ADDRESS`, `PERPL_API_KEY`, and
`PERPL_API_KEY_SECRET` are still accepted as a fallback.

## Run Locally

The simplest way to start the app is the combined local runner:

```bash
./scripts/run-local.sh
```

It creates the backend virtualenv when needed, installs frontend dependencies
when needed, then starts both services:

- Frontend: http://127.0.0.1:5173
- Backend health: http://127.0.0.1:8000/health

You can override ports if needed:

```bash
BACKEND_PORT=8001 FRONTEND_PORT=5174 ./scripts/run-local.sh
```

## Sync

Run one manual sync without starting the web server:

```bash
./scripts/sync-now.sh
```

The sync script initializes logging and SQLite, opens a backend DB session, and calls the same backend sync path used by the web app.

Alternative, just fire up the web page and click on "Sync Now" to sync once

Install the twice-daily cron scheduler:

```bash
./scripts/install-cron.sh
```

The installed cron entry runs `scripts/sync-due.py` every 15 minutes. That wrapper only performs a portfolio sync when the latest local noon or midnight slot has not completed successfully. If the computer is asleep, offline, or a sync fails, the same missed slot is retried the next time cron can run.

The backend server does not need to stay open for cron syncs. Cron calls the backend sync code directly through `scripts/sync-now.sh`.

On macOS, cron may need Privacy & Security access before it can run code from `~/Desktop`. If `data/logs/cron.log` shows `Operation not permitted`, grant Full Disk Access to `/usr/sbin/cron` and the Python executable used by cron, or move the project to a non-protected folder such as `~/Code/PortfolioTracker` and run `./scripts/install-cron.sh` again.

## Local API Endpoints

- `GET /health`
- `GET /api/sources`
- `POST /api/sources/wallets`
- `POST /api/sources/exchanges`
- `DELETE /api/sources/{source_id}`
- `POST /api/sync/run`
- `GET /api/defi/coverage/monad`
- `GET /api/defi/positions`
- `GET /api/portfolio/summary`
- `GET /api/portfolio/history`
- `GET /api/portfolio/holdings`

## Logs

Backend logs are printed to the terminal. General logs are saved to `data/logs/app.log`; errors are also saved to
`data/logs/errors.log`. Cron stdout and stderr are saved to `data/logs/cron.log`.

To watch logs while running locally:

```bash
tail -f data/logs/app.log
tail -f data/logs/errors.log
tail -f data/logs/cron.log
```

SQLite data is stored at `data/portfolio_tracker.sqlite3`. Startup only ensures reference chain metadata exists; it does not create demo accounts or synthetic portfolio history.
