#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
BACKEND_PYTHON="$BACKEND_DIR/.venv/bin/python"
LOCK_DIR="$ROOT_DIR/data/.sync.lock"

mkdir -p "$ROOT_DIR/data/logs"

if [ ! -x "$BACKEND_PYTHON" ]; then
  echo "Backend virtualenv not found at $BACKEND_PYTHON" >&2
  echo "Run scripts/run-local.sh once, or create backend/.venv and install backend/requirements.txt." >&2
  exit 1
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Sync already running; skipping this invocation."
  exit 75
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

cd "$BACKEND_DIR"
"$BACKEND_PYTHON" - <<'PY'
import sys

from app.config import ConfigurationError, require_moralis_api_key
from app.database import SessionLocal, init_db
from app.logging_config import configure_logging
from app.store import run_manual_sync, seed_database

configure_logging()

try:
    require_moralis_api_key()
except ConfigurationError as exc:
    print(f"Configuration error: {exc}", file=sys.stderr)
    raise SystemExit(1)

init_db()

with SessionLocal() as session:
    seed_database(session)
    result = run_manual_sync(session)
    print(result.model_dump())
    if result.status != "ok":
        raise SystemExit(2)
PY
