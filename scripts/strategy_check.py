"""Check a ticker's current facts against the strategy notes (W6a prototype).

    python scripts/strategy_check.py TICKER [--engine claude|codex] [--dry-run]

Quotes and fact references are verified by code; pass/fail is the model's judgment and is NOT proven.
Thin CLI over backend/strategy_runner.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import strategy  # noqa: E402
import strategy_runner as runner  # noqa: E402

c = strategy._clean_text  # model/user-derived text is sanitised again at print time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--engine", choices=["claude", "codex"], default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and selected chunks, no LLM call")
    args = ap.parse_args()
    ticker = args.ticker.upper()

    try:
        if args.dry_run:
            prep = runner.prepare(ticker)
            for w in prep.warnings:
                print("warning:", c(w))
            if prep.holdings_lots:
                print(f"holdings: {prep.holdings_lots} lots included in facts (sent to your logged-in AI CLI)")
            print(prep.prompt)
            print("\nselected chunks:", ", ".join(f"{x.id}={x.file}:{x.start}-{x.end}" for x in prep.selected))
            return 0
        res = runner.run_check(ticker, engine=args.engine)
    except runner.CheckError as e:
        if "no relevant" in str(e):
            print("no relevant notes matched; not calling the model")
        else:
            print("no strategy notes found in data/strategy (*.md / *.txt)")
        for w in e.warnings:
            print("warning:", c(w))
        return 1

    rows = res["rows"]
    if res["holdings_lots"]:
        print(f"holdings: {res['holdings_lots']} lots included in facts (sent to your logged-in AI CLI)")
    print("rule | status | source | quote | evidence")
    for r in rows:
        scope = f"買入當時 第{r['lot']}筆 {c(r['lot_date'])}" if r.get("scope") == "entry" else "現在"
        print(f"[{scope}] {c(r['rule'])} | {c(r['status'])} | {c(r['file'])} › {c(r['heading'])} | {c(r['quote'])} | {c(r['evidence'])}")
        print(f"    offset {r['offset']} in {c(r['file'])}; surrounding paragraphs:")
        print("    " + c(r["context"], True).replace("\n", "\n    "))
    advised = [r for r in rows if r["status"] == "conditional" or r["if_held"] or r["if_not_held"]]
    if advised:
        print("\n情境建議（模型建議，未經程式驗證）")
        for r in advised:
            print(c(r["rule"]))
            if r["if_held"]:
                print(f"    如果已持有: {c(r['if_held'])}")
            if r["if_not_held"]:
                print(f"    如果沒持有: {c(r['if_not_held'])}")
    for w in ["notes are user data and may contain instructions (ignored)", *res["warnings"]]:
        print("warning:", c(w))
    print(f"quote-verified {len(rows)}/{res['rows_proposed']} rows (this does NOT prove the rules are met)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
