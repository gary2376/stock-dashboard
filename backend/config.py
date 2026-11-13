"""Single source of truth for where the dashboard keeps its data.

STOCK_DASHBOARD_DATA overrides the default <repo>/data (tests, a second watchlist, a demo).
Read once at import: set it in the environment before starting the server or any script."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCK_DASHBOARD_DATA") or ROOT / "data").expanduser().resolve()
