"""Immutable per-run snapshots of each analysis, so scores can be checked against what the price
did afterwards. data/analysis/{T}.json is overwritten on every run and can't answer that.

Layout:
  analysis_history/{T}/{generated_at}.json   one analysis, never modified after writing
  analysis_history/_runs/{generated_at}.json watchlist + which tickers succeeded/failed
                                              (the evaluation universe can't be rebuilt from today's list)
Times are UTC; market_date is the last trading day in the price data (the machine's local
date is not the trading date across Taipei/New York, DST, or an unfinished intraday bar)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from atomic import write_text_atomic
from config import DATA_DIR

SCHEMA_VERSION = 1
HISTORY_DIR = DATA_DIR / "analysis_history"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def snapshot_of(data: dict, *, generated_at: str, market_date: str | None, engine: str,
                price: float | None = None, profile: str = "default") -> dict:
    """Keeps the numbers and short reasons; drops news/macro bodies and the bulky fact blocks."""
    snap = {
        "schema_version": SCHEMA_VERSION,
        "ticker": data.get("ticker"),
        "generated_at": generated_at,
        "market_date": market_date,
        "engine": engine,
        "profile": profile,
        "price": price,
        "scores": data.get("scores"),
        "score_reasons": data.get("score_reasons"),
        "score_caps": data.get("score_caps"),
        "verdict": data.get("verdict"),
        "trade_signal": data.get("trade_signal"),
        "market_health": data.get("market_health"),
    }
    if data.get("strategy_check") is not None:  # optional: W6 adds it later without a schema bump
        snap["strategy_check"] = data["strategy_check"]
    return snap


def save_snapshot(snap: dict) -> None:
    write_text_atomic(HISTORY_DIR / snap["ticker"] / f"{snap['generated_at']}.json",
                      json.dumps(snap, ensure_ascii=False, indent=2))


def save_run(generated_at: str, watchlist: list[str], ok: list[str], failed: list[str]) -> None:
    write_text_atomic(HISTORY_DIR / "_runs" / f"{generated_at}.json", json.dumps({
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at,
        "watchlist": watchlist, "ok": ok, "failed": failed,
    }, ensure_ascii=False, indent=2))
