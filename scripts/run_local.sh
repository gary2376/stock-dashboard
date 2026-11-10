#!/bin/bash
# Start the dashboard bound to localhost only (127.0.0.1). The API has no authentication and
# exposes your holdings, so anything wider than localhost must be a deliberate choice.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# a profile you keep under data/profiles/personal (git-ignored) is picked up automatically;
# set STOCK_DASHBOARD_PROFILE yourself to use another one
if [ -z "${STOCK_DASHBOARD_PROFILE:-}" ] && [ -d "$ROOT/data/profiles/personal" ]; then
  export STOCK_DASHBOARD_PROFILE="$ROOT/data/profiles/personal"
fi
cd "$ROOT/backend"
exec python3 -m uvicorn app:app --reload --host 127.0.0.1 --port 8013
