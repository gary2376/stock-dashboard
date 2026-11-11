"""Append-only log of every LLM CLI call (tokens, cost, seconds) so the user can see what the
analysis / strategy checks / chat actually cost. One JSON object per line in data/llm_usage.jsonl.
Token counts come from the CLI itself: claude reports them in its JSON output; codex prints a
'tokens used' total on stderr (no input/output split, no dollar cost)."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

from config import DATA_DIR

_lock = threading.Lock()


def log_file():
    return DATA_DIR / "llm_usage.jsonl"


def parse_claude(stdout: str):
    """-> (text, usage dict). Falls back to (stdout, {}) if the JSON envelope isn't there."""
    try:
        d = json.loads(stdout)
        u = d.get("usage") or {}
        return str(d.get("result", "")), {
            "input_tokens": u.get("input_tokens", 0),
            "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
            "cache_read_tokens": u.get("cache_read_input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "cost_usd": d.get("total_cost_usd"),
        }
    except (ValueError, AttributeError):
        return stdout, {}


def parse_codex(stderr: str) -> dict:
    m = re.findall(r"tokens used\s*\n?\s*([\d,]+)", stderr or "")
    return {"total_tokens": int(m[-1].replace(",", ""))} if m else {}


def record(engine: str, tag: str, seconds: float, usage: dict, ok: bool = True) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "engine": engine,
           "tag": tag or "", "seconds": round(seconds, 1), "ok": ok, **usage}
    try:
        with _lock:
            log_file().parent.mkdir(parents=True, exist_ok=True)
            with open(log_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # accounting must never break an analysis


def summarize(rows=None) -> dict:
    """Per-tag-kind totals (kind = text before ':' in the tag)."""
    if rows is None:
        try:
            rows = [json.loads(l) for l in log_file().read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, ValueError):
            rows = []
    out: dict = {}
    for r in rows:
        k = (r.get("tag") or "other").split(":")[0]
        s = out.setdefault(k, {"calls": 0, "seconds": 0.0, "cost_usd": 0.0, "tokens": 0})
        s["calls"] += 1
        s["seconds"] += r.get("seconds", 0) or 0
        s["cost_usd"] += r.get("cost_usd") or 0
        s["tokens"] += (r.get("total_tokens") or
                        sum(r.get(x, 0) or 0 for x in ("input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens")))
    return out
