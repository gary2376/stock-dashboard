"""Characterization fixture for backend/score_guards.py (tests/golden/guards.json), built from SYNTHETIC inputs.

Inputs are drawn from a fixed seed so the file is reproducible and contains no real market data: fundamentals,
momentum and market-health values are chosen to hit every guard (loss-making, negative FCF, heavy debt,
EPS miss, crash, cyclical sector/industry, tech sell-off) and the cases that fire nothing. Expected outputs are
whatever apply_guards returns today, so the file freezes current behaviour; regenerate only to change it.
Usage: python tests/make_golden.py   (from the repo root)"""
import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import score_guards  # noqa: E402

SECTORS = ["Technology", "Basic Materials", "Energy", "Industrials", "Financial Services", "Healthcare", "Consumer Cyclical"]
INDUSTRIES = ["Software", "Aluminum", "Steel", "Oil & Gas E&P", "Banks", "Semiconductors", "Copper", "Medical Devices", "Specialty Chemicals", "Retail"]
REASONS = {"fundamentals": "營收與毛利率穩定，現金流尚可。", "valuation": "本益比落在同業區間。", "news": "近期標題偏中性。",
           "technical": "15分：趨勢過濾未通過。", "market": "大盤均線位置中性。"}


def make_case(i: int, rng: random.Random) -> dict:
    cash = rng.choice([None, 0, 5e8, 2e9, 8e9])
    debt = rng.choice([None, 0, 1e9, 6e9, 2e10])
    fund = {
        "name": f"CASE{i:02d}", "sector": rng.choice(SECTORS), "industry": rng.choice(INDUSTRIES),
        "profit_margins": rng.choice([None, -0.2, -0.01, 0.0, 0.08, 0.25]),
        "free_cashflow": rng.choice([None, -3e8, 0, 2e8, 9e9]),
        "total_cash": cash, "total_debt": debt,
        "trailing_pe": rng.choice([None, 12.5, 34.0]), "market_cap": rng.choice([5e8, 5e10, 2e12]),
        "earnings_history": rng.choice([[], [{"surprise_pct": -15.0}], [{"surprise_pct": -10.0}], [{"surprise_pct": 4.2}], [{"surprise_pct": None}]]),
    }
    dossier = rng.choice([None, {"momentum": {"ret_20d_pct": rng.choice([-55.0, -40.0, -12.0, 3.0, None])}}, {"momentum": {}}])
    health = {"side_5d": rng.choice([{}, {"費城半導體指數": -12.0}, {"費城半導體指數": -8.0}, {"費城半導體指數": 2.5}])}
    scores = {k: rng.choice([20, 38, 47, 58, 66, 74, 88]) for k in ("technical", "fundamentals", "valuation", "news", "market")}
    return {"ticker": f"CASE{i:02d}", "fund": fund, "dossier": dossier, "health": health,
            "pct_from_high": rng.choice([None, -75.0, -60.0, -30.0, -2.0]),
            "data": {"scores": scores, "score_reasons": dict(REASONS)}}


def main() -> None:
    rng = random.Random(20261113)
    cases = []
    for i in range(1, 61):
        case = make_case(i, rng)
        data = copy.deepcopy(case["data"])
        fired = score_guards.apply_guards(data, case["fund"], case["dossier"], case["pct_from_high"], case["health"])
        case["expected"] = {"scores": data["scores"], "score_reasons": data["score_reasons"], "caps": fired}
        cases.append(case)
    out = ROOT / "tests" / "golden" / "guards.json"
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases -> {out}; caps fired in {sum(1 for c in cases if c['expected']['caps'])}")


if __name__ == "__main__":
    main()
