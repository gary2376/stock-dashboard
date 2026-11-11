"""Append-only log of every LLM CLI call (tokens, cost, seconds) so the user can see what the
analysis / strategy checks / chat actually cost. One JSON object per line in data/llm_usage.jsonl.
Token counts come from the CLI itself: claude reports them in its JSON output; codex prints a
'tokens used' total on stderr (no input/output split, no dollar cost)."""
from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timezone

from config import DATA_DIR

_lock = threading.Lock()


def log_file():
    return DATA_DIR / "llm_usage.jsonl"


def parse_envelope(stdout: str):
    """The claude JSON envelope (dict with 'result'), tolerating warning lines around it; else None."""
    s = stdout or ""
    for cand in (s, s[s.find("{"):s.rfind("}") + 1]):
        try:
            d = json.loads(cand)
        except ValueError:
            continue
        if isinstance(d, dict) and "result" in d:
            return d
    return None


def parse_claude(stdout: str):
    """-> (text, usage dict). Falls back to (stdout, {}) if the JSON envelope isn't there."""
    d = parse_envelope(stdout)
    if d is None:
        return stdout, {}
    u = d.get("usage") if isinstance(d.get("usage"), dict) else {}
    return str(d.get("result", "")), {
        "input_tokens": u.get("input_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cost_usd": d.get("total_cost_usd"),
    }


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


_NUM = ("seconds", "input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens", "total_tokens", "cost_usd")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def sanitize(r) -> dict:
    """Allowlisted, type-coerced copy of a log row (never echoes unknown fields such as prompts/paths)."""
    out = {"ts": str(r.get("ts", ""))[:40], "engine": str(r.get("engine", ""))[:20],
           "tag": str(r.get("tag") or "")[:60], "ok": bool(r.get("ok", True))}
    for k in _NUM:
        n = _num(r.get(k))
        out[k] = n if k == "cost_usd" else (n or 0)
    return out


def recent_rows(n: int = 2000, max_bytes: int = 2_000_000) -> list:
    """Last n lines (bounded read) of the log, each parsed independently; bad lines are skipped."""
    try:
        with open(log_file(), "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - max_bytes))
            raw = f.read().decode("utf-8", "replace").splitlines()
            if size > max_bytes:
                raw = raw[1:]  # first line is probably cut in half
    except OSError:
        return []
    rows = []
    for line in raw[-n:]:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(sanitize(d))
    return rows
