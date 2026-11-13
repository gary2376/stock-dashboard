"""Single source of truth for where the dashboard keeps its data.

STOCK_DASHBOARD_DATA overrides the default <repo>/data (tests, a second watchlist, a demo).
Read once at import: set it in the environment before starting the server or any script."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCK_DASHBOARD_DATA") or ROOT / "data").expanduser().resolve()

# Demo mode (STOCK_DASHBOARD_DEMO=1): serves a synthetic dataset and never reaches the network or an AI CLI.
# Modules read config.DEMO at call time (tests flip it).
DEMO = os.environ.get("STOCK_DASHBOARD_DEMO", "").strip().lower() in ("1", "true", "yes")
DEMO_MESSAGE = "示範模式：此功能需要你登入的 AI CLI 或網路資料，已停用"
