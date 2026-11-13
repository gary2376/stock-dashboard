#!/bin/bash
# Demo mode: a fully synthetic dataset (fictional tickers, canned analysis) on 127.0.0.1:8013.
# No AI CLI, no network, never touches your real data/. Build the data once, then serve it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=python3
[ -x "$ROOT/.venv/bin/python3" ] && PY="$ROOT/.venv/bin/python3"  # prefer the project venv when there is one
DEMO_DIR="$ROOT/data_demo"
[ -f "$DEMO_DIR/.demo_dataset" ] || "$PY" "$ROOT/scripts/load_demo.py" "$DEMO_DIR"
export STOCK_DASHBOARD_DATA="$DEMO_DIR"
export STOCK_DASHBOARD_DEMO=1
cd "$ROOT/backend"
exec "$PY" -m uvicorn app:app --host 127.0.0.1 --port 8013
