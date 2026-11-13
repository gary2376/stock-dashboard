"""First-run seed: a fresh clone has an empty data/, so the first start gets one starter ticker and the example
strategy notes. Runs only from the server's startup hook and only when nothing of the user's exists yet.

Never overwrites or deletes, never raises (a warning is logged), idempotent via the .seeded marker."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config
import strategy
from atomic import write_text_exclusive
from config import DATA_DIR

logger = logging.getLogger("seed")
EXAMPLES = config.ROOT / "examples" / "strategy"
STARTER_TICKERS = ["NVDA"]
MARKER = ".seeded"


def _has_files(d: Path) -> bool:
    try:
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return True  # unreadable: assume it is someone's data


def _fresh() -> bool:
    d = DATA_DIR
    if (d / MARKER).exists() or (d / "tickers.json").exists() or (d / "holdings.json").exists():
        return False
    return not (_has_files(d / "analysis") or _has_files(d / "strategy"))


def maybe_seed():
    """-> the marker dict when something was seeded, else None."""
    try:
        if config.DEMO or os.environ.get("SD_NO_SEED") == "1" or not _fresh():
            return None
        notes = []
        try:
            for p in sorted(EXAMPLES.glob("*")):
                if not strategy.NAME_RE.fullmatch(p.name) or p.name.startswith(".") or not p.is_file() or p.is_symlink():
                    continue
                data = p.read_bytes()
                if len(data) > strategy.MAX_FILE or len(notes) >= strategy.MAX_FILES:
                    continue
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                try:
                    write_text_exclusive(DATA_DIR / "strategy" / p.name, data.decode("utf-8"))
                    notes.append(p.name)
                except FileExistsError:
                    pass
        except OSError as e:
            logger.warning("seeding example notes failed: %s", e)
        seeded = {"seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "tickers": [], "notes": notes}
        try:
            write_text_exclusive(DATA_DIR / "tickers.json", json.dumps(STARTER_TICKERS, ensure_ascii=False, indent=2))
            seeded["tickers"] = list(STARTER_TICKERS)
        except OSError as e:
            logger.warning("seeding the starter ticker failed: %s", e)
        write_text_exclusive(DATA_DIR / MARKER, json.dumps(seeded, ensure_ascii=False, indent=2))
        return seeded
    except Exception as e:  # noqa: BLE001 — starting the server matters more than the starter data
        logger.warning("first-run seed skipped: %s", e)
        return None
