"""Backtest the DETERMINISTIC technical axis on the cached price history — no waiting for snapshots.

    python scripts/backtest_technical.py [--horizon 20] [--step 5] [--min-bars 250]

At each past bar i it runs meta_screen.analyze_core on the series truncated at i (point-in-time, no
lookahead), maps it to technical_score, and compares with the forward `horizon`-bar return. What it
CAN'T test: the LLM-judged axes (fundamentals/valuation/news) — historical news and fundamentals
aren't in the cache and the model's own knowledge would leak the future — and the market axis
(needs index history). Read the caveats it prints: overlapping windows, a watchlist picked with
hindsight, one bull-market regime.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import meta_screen  # noqa: E402
from config import DATA_DIR  # noqa: E402


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def collect(horizon: int, step: int, min_bars: int):
    rows = []  # (ticker, date, score, stage2, fwd_return_pct)
    for f in sorted((DATA_DIR / "price_cache").glob("*.json")):
        try:
            c = json.loads(f.read_text())["candles"]
        except (OSError, ValueError, KeyError):
            continue
        o, h, l, cl = ([x[k] for x in c] for k in ("open", "high", "low", "close"))
        v = [x.get("volume") or 0.0 for x in c]
        d = [x["time"] for x in c]
        for i in range(min_bars, len(c) - horizon, step):
            res = meta_screen.analyze_core(f.stem, o[: i + 1], h[: i + 1], l[: i + 1], cl[: i + 1], v[: i + 1], d[: i + 1])
            if res is None:
                continue
            rows.append((f.stem, d[i], meta_screen.technical_score(res), bool(res.get("stage2")),
                         (cl[i + horizon] / cl[i] - 1) * 100))
    return rows


def summarize(label, rets):
    if not rets:
        return f"{label:<22} n=0"
    return (f"{label:<22} n={len(rets):<5} mean={statistics.fmean(rets):+6.2f}%  median={statistics.median(rets):+6.2f}%  "
            f"up={sum(r > 0 for r in rets) / len(rets) * 100:4.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--min-bars", type=int, default=250)
    a = ap.parse_args()
    rows = collect(a.horizon, a.step, a.min_bars)
    if not rows:
        print("no data (need data/price_cache/*.json)")
        return 1
    tickers = len({r[0] for r in rows})
    print(f"{len(rows)} point-in-time samples, {tickers} tickers, forward {a.horizon} bars, every {a.step} bars\n")
    print(summarize("ALL (baseline)", [r[4] for r in rows]))
    print()
    for label, lo, hi in (("score 15 (no Stage2)", 15, 15), ("score 30-50", 30, 50), ("score 55-65", 55, 65), ("score 90+", 90, 100)):
        print(summarize(label, [r[4] for r in rows if lo <= r[2] <= hi]))
    print()
    print(summarize("Stage2 passes", [r[4] for r in rows if r[3]]))
    print(summarize("Stage2 fails", [r[4] for r in rows if not r[3]]))
    rho = spearman([r[2] for r in rows], [r[4] for r in rows])
    print(f"\nSpearman(score, forward return) = {rho:+.3f}" if rho is not None else "\nSpearman n/a")
    # excess return vs the OTHER tickers of the same market on the same date (leave-one-out): removes the
    # market drift a one-regime bull run lifts everything with; TW and US calendars are kept apart
    def market(t: str) -> str:
        return "tw" if t.endswith((".TW", ".TWO")) else "us"

    groups: dict = {}
    for r in rows:
        groups.setdefault((market(r[0]), r[1]), []).append(r[4])
    ex = []
    for r in rows:
        g = groups[(market(r[0]), r[1])]
        if len(g) >= 6:
            ex.append((r, r[4] - (sum(g) - r[4]) / (len(g) - 1)))
    if ex:
        print(f"\nExcess vs the other same-market tickers on the same date ({len(ex)} samples with >=6 tickers that day):")
        for label, lo, hi in (("score 15 (no Stage2)", 15, 15), ("score 30-50", 30, 50), ("score 55-65", 55, 65), ("score 90+", 90, 100)):
            e = [x for r, x in ex if lo <= r[2] <= hi]
            print(summarize(label, e))
        rho2 = spearman([r[2] for r, _ in ex], [x for _, x in ex])
        print(f"Spearman(score, excess return) = {rho2:+.3f}" if rho2 is not None else "Spearman n/a")
    print("""
Caveats (read before believing anything above):
- Windows overlap (step < horizon) and tickers move together, so the effective sample is far smaller than n.
- The watchlist was chosen with hindsight (survivorship / selection bias); the cache is ~5 years of one regime.
- Signal and entry use the same close (an IC, not an executable return); raw closes ignore dividends.
- Only the rule-based technical axis is tested; LLM-judged axes can't be backtested without lookahead.
- A significant-looking number here is a hint, not proof; a flat result means 'no evidence', not 'broken'.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
