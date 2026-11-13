#!/bin/bash
# Start the dashboard bound to localhost only (127.0.0.1). The API has no authentication and
# exposes your holdings, so anything wider than localhost must be a deliberate choice.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"
exec python3 -m uvicorn app:app --reload --host 127.0.0.1 --port 8013
