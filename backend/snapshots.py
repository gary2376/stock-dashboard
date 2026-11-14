"""Immutable per-run snapshots of each analysis, so scores can be checked against what the price
did afterwards. data/analysis/{T}.json is overwritten on every run and can't answer that.

Layout:
  analysis_history/{T}/{generated_at}.json   one analysis, never modified after writing
  analysis_history/_runs/{run_id}.json        requested tickers + which succeeded/failed/skipped
                                              (the evaluation universe can't be rebuilt from today's list)
Times are UTC; market_date is the last trading day in the price data (the machine's local
date is not the trading date across Taipei/New York, DST, or an unfinished intraday bar)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from atomic import write_text_exclusive
from config import DATA_DIR

SCHEMA_VERSION = 1
HISTORY_DIR = DATA_DIR / "analysis_history"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def snapshot_of(data: dict, *, generated_at: str, market_date: str | None, engine: str,
                price: float | None = None, profile: str = "default", run_id: str | None = None) -> dict:
    """Keeps the numbers and short reasons; drops news/macro bodies and the bulky fact blocks."""
    snap = {
        "schema_version": SCHEMA_VERSION,
        "ticker": data.get("ticker"),
        "generated_at": generated_at,
        "run_id": run_id,
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


KEEP_PER_TICKER = 200
KEEP_RUNS = 500


def _prune(directory, keep: int) -> None:
    """Best-effort: keep the newest `keep` *.json files (names are UTC timestamps, so name order = age)."""
    try:
        files = sorted(p.name for p in directory.glob("*.json"))
        for name in files[:-keep] if len(files) > keep else []:
            (directory / name).unlink()
    except OSError:
        pass


def save_snapshot(snap: dict) -> None:
    d = HISTORY_DIR / snap["ticker"]
    write_text_exclusive(d / f"{snap['generated_at']}.json", json.dumps(snap, ensure_ascii=False, indent=2))
    _prune(d, KEEP_PER_TICKER)


def save_run(run_id: str, requested: list[str], ok: list[str], failed: list[str],
             skipped: list[str], explicit: bool) -> None:
    """`requested` = the tickers this run was asked to do (whole watchlist, or the explicit CLI args
    when explicit=True). skipped = another process held the ticker's lock, so no snapshot was made.
    Each snapshot carries the same run_id."""
    write_text_exclusive(HISTORY_DIR / "_runs" / f"{run_id}.json", json.dumps({
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "explicit_tickers": explicit,
        "requested": requested, "ok": ok, "failed": failed, "skipped": skipped,
    }, ensure_ascii=False, indent=2))
    _prune(HISTORY_DIR / "_runs", KEEP_RUNS)
