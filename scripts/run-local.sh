#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_PYTHON="$BACKEND_DIR/.venv/bin/python"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [ ! -x "$BACKEND_PYTHON" ]; then
  echo "Creating backend virtualenv at $BACKEND_DIR/.venv"
  python3 -m venv "$BACKEND_DIR/.venv"
  "$BACKEND_PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
fi

(cd "$BACKEND_DIR" && "$BACKEND_PYTHON" - <<'PY'
import sys

from app.config import ConfigurationError, require_moralis_api_key

try:
    require_moralis_api_key()
except ConfigurationError as exc:
    print(f"Configuration error: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "Installing frontend dependencies"
  (cd "$FRONTEND_DIR" && npm install)
fi

pids=()

cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}

trap cleanup INT TERM EXIT

echo "Starting backend on http://127.0.0.1:$BACKEND_PORT"
(cd "$BACKEND_DIR" && "$BACKEND_PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
pids+=("$!")

echo "Starting frontend on http://127.0.0.1:$FRONTEND_PORT"
(cd "$FRONTEND_DIR" && npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT") &
pids+=("$!")

echo "Open http://127.0.0.1:$FRONTEND_PORT"

while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"
      exit $?
    fi
  done
  sleep 1
done
