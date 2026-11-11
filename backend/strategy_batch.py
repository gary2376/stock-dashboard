"""Batch strategy overview: run the single-ticker check over a few tickers and fold the results into a
rule x ticker matrix. A rule is identified by its verified quote (file, hash of the normalized quote)."""
from __future__ import annotations

import hashlib

import strategy

MAX_BATCH = 10
STATUSES = ("pass", "fail", "unknown", "conditional")


def normalize_tickers(tickers, watchlist) -> list:
    """Dedupe (order kept) and validate; ValueError carries a user-facing message."""
    if not isinstance(tickers, list) or not all(isinstance(t, str) for t in tickers):
        raise ValueError("tickers must be a list of strings")
    out = list(dict.fromkeys(tickers))
    if not out:
        raise ValueError("no tickers")
    if len(out) > MAX_BATCH:
        raise ValueError(f"too many tickers (max {MAX_BATCH})")
    bad = [t for t in out if t not in watchlist]
    if bad:
        raise ValueError(f"not in watchlist: {', '.join(bad)}")
    return out


def run_batch(tickers, run_one, on_progress=None, error_text=str):
    """Sequential; one failure never stops the rest. -> (results ticker->dict|None, errors ticker->text).
    on_progress(done, total, current) is called before each ticker and once at the end; returning False aborts."""
    tickers = list(dict.fromkeys(tickers))
    if len(tickers) > MAX_BATCH:
        raise ValueError(f"too many tickers (max {MAX_BATCH})")
    results, errors = {}, {}
    for i, t in enumerate(tickers):
        if on_progress and on_progress(i, len(tickers), t) is False:
            return results, errors
        try:
            results[t] = run_one(t)
        except Exception as e:  # noqa: BLE001 — isolate the failure to this ticker
            results[t] = None
            errors[t] = error_text(e)
    if on_progress:
        on_progress(len(tickers), len(tickers), None)
    return results, errors


def build_overview(results: dict, errors: dict = None) -> dict:
    errors = errors or {}
    rules: dict = {}
    tickers = []
    for t, res in results.items():
        counts = dict.fromkeys(STATUSES, 0)
        rows = {}
        for r in (res or {}).get("rows", []):
            if r.get("status") not in counts or "file" not in r or not isinstance(r.get("quote"), str):
                continue
            digest = hashlib.sha1(strategy.normalize(r["quote"]).encode("utf-8")).hexdigest()[:10]
            key = f"{r['file']}#{digest}"  # content-derived: survives note edits that shift offsets
            if key in rows:  # same quote twice for one ticker: first wins
                continue
            rows[key] = r["status"]
            counts[r["status"]] += 1
            rule = rules.setdefault(key, {"key": key, "rule": r.get("rule", ""), "file": r["file"],
                                          "heading": r.get("heading", ""), "offset": r.get("offset"),
                                          **{f"count_{s}": 0 for s in STATUSES}})
            rule[f"count_{r['status']}"] += 1
        entry = {"ticker": t, "rows": rows, "counts": counts,
                 "holdings_lots": (res or {}).get("holdings_lots"), "generated_at": (res or {}).get("generated_at")}
        if t in errors:
            entry["error"] = errors[t]
        tickers.append(entry)
    ordered = sorted(rules.values(), key=lambda r: -r["count_fail"])  # stable: first-seen order breaks ties
    return {"rules": ordered, "tickers": tickers}
