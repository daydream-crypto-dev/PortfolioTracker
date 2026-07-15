#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/data/logs"
BEGIN_MARKER="# PortfolioTracker sync cron start"
END_MARKER="# PortfolioTracker sync cron end"
CRON_LINE="*/15 * * * * PYTHONPYCACHEPREFIX=/tmp/portfoliotracker-pycache /usr/bin/python3 $ROOT_DIR/scripts/sync-due.py >> $LOG_DIR/cron.log 2>&1"

mkdir -p "$LOG_DIR"

existing_cron="$(crontab -l 2>/dev/null || true)"
filtered_cron="$(printf '%s\n' "$existing_cron" | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
  $0 == begin { skip = 1; next }
  $0 == end { skip = 0; next }
  skip != 1 { print }
')"

{
  printf '%s\n' "$filtered_cron" | sed '/^$/N;/^\n$/D'
  printf '%s\n' "$BEGIN_MARKER"
  printf '%s\n' "$CRON_LINE"
  printf '%s\n' "$END_MARKER"
} | crontab -

echo "Installed PortfolioTracker cron scheduler:"
echo "$CRON_LINE"
