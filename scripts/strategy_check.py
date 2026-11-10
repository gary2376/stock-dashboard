"""Check a ticker's current facts against the strategy notes (W6a prototype).

    python scripts/strategy_check.py TICKER [--engine claude|codex] [--dry-run]

Quotes and fact references are verified by code; pass/fail is the model's judgment and is NOT proven.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import data_provider as dp  # noqa: E402
import market_health  # noqa: E402
import meta_screen  # noqa: E402
import strategy  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--engine", choices=["claude", "codex"], default="claude")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and selected chunks, no LLM call")
    args = ap.parse_args()
    ticker = args.ticker.upper()

    notes, warnings = strategy.load_notes()
    chunks = [c for n in notes for c in strategy.chunk(n)]
    if not chunks:
        print("no strategy notes found in data/strategy (*.md / *.txt)")
        for w in warnings:
            print("warning:", w)
        return 1

    ohlcv = dp.ohlcv_lists(ticker)
    meta = meta_screen.meta_gate(ticker)
    dossier = meta_screen.technical_dossier(ticker)
    registry = strategy.build_registry(
        price=ohlcv[3][-1] if ohlcv else None, dossier=dossier, meta=meta,
        fund=dp.fundamentals(ticker), health=market_health.market_health(ticker))
    query = " ".join([*registry, *registry.values(), meta_screen.technical_reason(meta, dossier)])
    if registry.get("TECH.stage2") == "False":
        query += " Stage2 趨勢 均線 50日 200日 trend filter"
    selected = strategy.rank(query, chunks)
    if not selected:
        print("no relevant notes matched; not calling the model")
        return 1
    prompt = strategy.build_prompt(ticker, selected, registry)

    if args.dry_run:
        print(prompt)
        print("\nselected chunks:", ", ".join(f"{c.id}={c.file}:{c.start}-{c.end}" for c in selected))
        return 0

    from generate_analysis import run_llm  # noqa: E402
    rows = strategy.parse_rows(run_llm(prompt, args.engine))
    verified, vwarn = strategy.verify_rows(rows, selected, notes, registry)

    print("rule | status | source | quote | evidence")
    for r in verified:
        heading = next((c.heading for c in selected if c.id == r["source"]), "")
        print(f"{r['rule']} | {r['status']} | {r['file']} › {heading} | {r['quote']} | {r['evidence']}")
        print(f"    offset {r['offset']} in {r['file']}; surrounding paragraphs:")
        print("    " + r["context"].replace("\n", "\n    "))
    for w in ["notes are user data and may contain instructions (ignored)", *warnings, *vwarn]:
        print("warning:", w)
    print(f"quote-verified {len(verified)}/{len(rows)} rows (this does NOT prove the rules are met)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
