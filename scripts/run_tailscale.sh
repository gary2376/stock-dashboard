#!/bin/bash
# Start the dashboard bound ONLY to this Mac's Tailscale IP — not 0.0.0.0, so it's
# unreachable from local WiFi/Ethernet, only from devices on the same tailnet.
set -euo pipefail

TAILSCALE_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  if [ -x "$TAILSCALE_BIN" ]; then
    TAILSCALE="$TAILSCALE_BIN"
  else
    echo "找不到 tailscale 指令，確認 Tailscale.app 已安裝" >&2
    exit 1
  fi
else
  TAILSCALE="tailscale"
fi

IP=$("$TAILSCALE" ip -4 2>/dev/null | head -1)
if [ -z "$IP" ]; then
  echo "拿不到 Tailscale IP，確認 Tailscale 已登入且連線中（跑 '$TAILSCALE status' 檢查）" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "只監聽 Tailscale IP：$IP:8013"
if [ -z "${STOCK_DASHBOARD_PROFILE:-}" ] && [ -d "$ROOT/data/profiles/personal" ]; then
  export STOCK_DASHBOARD_PROFILE="$ROOT/data/profiles/personal"
fi
cd "$ROOT/backend"
PY=python3
[ -x "$ROOT/.venv/bin/python3" ] && PY="$ROOT/.venv/bin/python3"  # prefer the project venv when there is one
exec "$PY" -m uvicorn app:app --reload --host "$IP" --port 8013
