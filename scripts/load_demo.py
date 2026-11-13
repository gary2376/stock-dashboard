"""Build a fully synthetic demo dataset so a stranger can see the whole UI without an AI CLI or network.

    python scripts/load_demo.py [OUT_DIR]        (default <repo>/data_demo, git-ignored)
    STOCK_DASHBOARD_DATA=<OUT_DIR> STOCK_DASHBOARD_DEMO=1 uvicorn app:app   (scripts/run_demo.sh does this)

Everything is fictional and deterministic (fixed seeds and timestamps): prices are seeded random walks,
fundamentals/news/prose are hand-written. The numbers that CAN be computed come from the real code paths
(meta_screen, market_health._fetch on a synthetic index, score_guards, strategy.verify_rows, the pin template
compiler, strategy_batch.build_overview) so the demo is internally consistent. Nothing is copied from real data
and no network or LLM is used (model answers are canned). Re-running overwrites OUT_DIR; it refuses to touch
the real data/ dir or any non-empty directory that was not created by this script."""
from __future__ import annotations

import contextlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ".demo_dataset"
END = date(2026, 11, 6)  # last trading day of the demo data (a Friday)
N_DAYS = 1300
STAMP = "20261106T120000000000Z"  # fixed "now" for every generated_at / confirmed_at
NOTES_NAME = "sample_strategy.md"

# ticker -> (name, seed, start price, base volume, segments [(days, mu, sigma)] or 'range', role)
SERIES = {
    "DEMOA": ("示範公司 A", 5, 38.0, 2_400_000, [(960, 0.0012, 0.013), (85, -0.0030, 0.016), (255, 0.0016, 0.012)]),
    "DEMOB": ("示範公司 B", 22, 140.0, 1_800_000, [(500, 0.0006, 0.014), (800, -0.0010, 0.015)]),
    "DEMOC": ("示範公司 C", 33, 100.0, 3_100_000, "range"),
    "DEMOD.TW": ("示範公司 D", 70, 210.0, 9_000_000, [(700, 0.0008, 0.015), (300, 0.0002, 0.012), (300, 0.0010, 0.013)]),
    "DEMOE.TW": ("示範公司 E", 55, 55.0, 14_000_000, [(400, 0.0010, 0.017), (400, -0.0012, 0.018), (500, 0.0004, 0.014)]),
}
FUND = {  # fictional numbers; same keys as data_provider.fetch_fundamentals_live
    "DEMOA": dict(sector="Technology", industry="Software - Infrastructure", trailing_pe=34.2, forward_pe=29.8,
                  revenue_growth_yoy=0.21, earnings_growth_yoy=0.27, gross_margins=0.71, trailing_eps=2.84,
                  market_cap=182_000_000_000, total_cash=9_800_000_000, total_debt=3_100_000_000,
                  debt_to_equity=24.0, free_cashflow=5_200_000_000, operating_margins=0.28, profit_margins=0.23,
                  return_on_equity=0.26, beta=1.18, target_mean_price=118.0, target_high_price=140.0,
                  target_low_price=90.0, num_analyst_opinions=31, recommendation_key="buy",
                  next_earnings_date="2026-11-18", eps=[(1.9, 2.05), (2.1, 2.22), (2.3, 2.41), (2.5, 2.62)]),
    "DEMOB": dict(sector="Consumer Cyclical", industry="Specialty Retail", trailing_pe=None, forward_pe=41.0,
                  revenue_growth_yoy=-0.06, earnings_growth_yoy=-0.35, gross_margins=0.33, trailing_eps=-0.42,
                  market_cap=12_400_000_000, total_cash=600_000_000, total_debt=2_900_000_000,
                  debt_to_equity=148.0, free_cashflow=-310_000_000, operating_margins=-0.02, profit_margins=-0.04,
                  return_on_equity=-0.08, beta=1.45, target_mean_price=88.0, target_high_price=120.0,
                  target_low_price=60.0, num_analyst_opinions=14, recommendation_key="hold",
                  next_earnings_date="2026-11-26", eps=[(0.2, 0.12), (0.1, -0.05), (0.0, -0.22), (-0.1, -0.31)]),
    "DEMOC": dict(sector="Healthcare", industry="Medical Devices", trailing_pe=22.5, forward_pe=21.0,
                  revenue_growth_yoy=0.04, earnings_growth_yoy=0.02, gross_margins=0.58, trailing_eps=4.43,
                  market_cap=46_000_000_000, total_cash=4_100_000_000, total_debt=3_600_000_000,
                  debt_to_equity=45.0, free_cashflow=1_900_000_000, operating_margins=0.19, profit_margins=0.15,
                  return_on_equity=0.14, beta=0.82, target_mean_price=104.0, target_high_price=118.0,
                  target_low_price=88.0, num_analyst_opinions=22, recommendation_key="hold",
                  next_earnings_date="2026-11-12", eps=[(1.05, 1.08), (1.1, 1.09), (1.12, 1.15), (1.15, 1.16)]),
    "DEMOD.TW": dict(sector="Technology", industry="Semiconductors", trailing_pe=18.4, forward_pe=15.9,
                    revenue_growth_yoy=0.15, earnings_growth_yoy=0.19, gross_margins=0.46, trailing_eps=12.1,
                    market_cap=980_000_000_000, total_cash=210_000_000_000, total_debt=90_000_000_000,
                    debt_to_equity=31.0, free_cashflow=64_000_000_000, operating_margins=0.31, profit_margins=0.26,
                    return_on_equity=0.22, beta=1.05, target_mean_price=255.0, target_high_price=300.0,
                    target_low_price=205.0, num_analyst_opinions=18, recommendation_key="buy",
                    next_earnings_date="2026-11-27", eps=[(2.8, 2.95), (2.9, 3.02), (3.0, 3.1), (3.1, 3.27)]),
    "DEMOE.TW": dict(sector="Industrials", industry="Electronic Components", trailing_pe=13.2, forward_pe=12.4,
                    revenue_growth_yoy=0.03, earnings_growth_yoy=-0.04, gross_margins=0.22, trailing_eps=3.9,
                    market_cap=88_000_000_000, total_cash=9_000_000_000, total_debt=8_500_000_000,
                    debt_to_equity=62.0, free_cashflow=2_100_000_000, operating_margins=0.09, profit_margins=0.07,
                    return_on_equity=0.09, beta=0.95, target_mean_price=58.0, target_high_price=70.0,
                    target_low_price=46.0, num_analyst_opinions=9, recommendation_key="hold",
                    next_earnings_date="2026-12-03", eps=[(0.9, 0.86), (0.95, 0.93), (0.92, 0.96), (0.97, 0.94)]),
}
# fundamentals / valuation / news axes are hand-written (technical + market come from code)
HAND_SCORES = {"DEMOA": (78, 52, 64), "DEMOB": (48, 72, 33), "DEMOC": (60, 58, 50),
               "DEMOD.TW": (72, 61, 58), "DEMOE.TW": (54, 66, 47)}
VERDICT = {"DEMOA": "多方續抱", "DEMOB": "偏空觀望", "DEMOC": "區間觀望", "DEMOD.TW": "趨勢偏多", "DEMOE.TW": "反彈觀望"}
SAMPLE_NOTES = """# 示範交易策略 Demo Strategy

這是一份為示範模式撰寫的原創策略筆記，內容與任何真實建議無關。
This note is synthetic and only exists so the demo can show how strategy checks work.

## 趨勢濾網 Trend filter

只做第二階段（Stage2）的股票：股價必須站上50日均線，且50日均線高於200日線。
Both moving averages must be rising over the last twenty trading days before a new position is opened.
若股價跌破50日均線，就不要新開任何多單部位，已持有的部位也要重新檢視。
The trend filter is a hard gate: a stock that fails the Stage2 test is simply not on the buy list.

## 進場 Entry

等待量縮整理後的突破，突破當天成交量需要放大到二十日均量的一點三倍以上。
Enter only on a breakout from a tight base, and only when volume expands clearly above its recent average.
RSI 超過 70 的時候不要追價，等回檔整理後再重新評估是否進場。
Do not chase an extended stock; wait for a pullback and a fresh setup instead.
突破失敗時不要加碼攤平，虧損的部位不可以再加碼。

## 風險 Risk

單筆交易的停損不可以超過百分之七，這是不可談判的規則。
Every position needs a stop level written down before the order is placed.
報酬風險比至少要達到三比一，否則這筆交易不值得做。

| 項目 | 規則 |
| --- | --- |
| 停損 | 不超過7% |
| 報酬風險比 | 至少3比1 |
| 同時持股 | 最多八檔 |
| 單筆部位 | 不超過總資金的百分之十五 |

## 部位管理 Position management

賺錢的部位可以分批加碼，但每次加碼都要比前一次更小。
Add to winners in smaller and smaller pieces, never to losers.
賣出的理由只有兩個：停損被觸發，或是趨勢濾網不再成立。
If neither reason applies, do nothing; boredom is not a signal.

## 市場環境 Market regime

大盤在50日線之下時，新部位的規模減半，而且只做最強的標的。
When the index trades below its 50-day average, halve the size of any new position.
大盤跌破200日線時，暫停所有新的進場，只管理手上已有的部位。
Pause new entries completely when the index loses its 200-day average.

## 觀察清單 Watchlist

每週日整理一次觀察清單，刪除連續四週沒有出現進場條件的股票。
Keep the list short: a name that has not set up for four weeks is removed.
每檔股票都要寫下一句話的理由，寫不出來就不要放進清單。
One sentence of thesis per name; if it cannot be written, the name does not belong.

## 檢討 Review

每週日花十分鐘回顧本週的每一筆交易，寫下一個教訓。
Keep a short journal: what was the setup, what did I do, what would I change.
不要在財報公布前一週建立新部位，因為跳空風險太高。
"""
PIN_TEXTS = ["停損不超過7%", "RSI超過70不追", "股價在200日線之上", "不攤平", "財報前一週不要買"]


# ---------------------------------------------------------------- synthetic data (no repo imports)

def trading_days(n: int, end: date) -> list:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def closes_for(seed: int, start: float, segments, n: int) -> list:
    rng = random.Random(seed)
    price, out = start, []
    if segments == "range":
        for _ in range(n):  # mean-reverting around the start price
            price *= math.exp(0.035 * (math.log(start) - math.log(price)) + 0.014 * rng.gauss(0, 1))
            out.append(price)
        return out
    for days, mu, sigma in segments:
        for _ in range(days):
            price *= math.exp(mu + sigma * rng.gauss(0, 1))
            out.append(price)
    return out


def candles_for(seed: int, closes: list, dates: list, base_vol: int) -> list:
    rng = random.Random(seed + 1000)
    out, prev = [], closes[0]
    for c, d in zip(closes, dates):
        o = prev * (1 + 0.003 * rng.gauss(0, 1))
        hi = max(o, c) * (1 + abs(0.006 * rng.gauss(0, 1)))
        lo = min(o, c) * (1 - abs(0.006 * rng.gauss(0, 1)))
        vol = base_vol * math.exp(0.35 * rng.gauss(0, 1)) * (1 + 18 * abs(c / prev - 1))
        out.append({"time": d, "open": round(o, 2), "high": round(hi, 2), "low": round(lo, 2),
                    "close": round(c, 2), "volume": int(vol)})
        prev = c
    return out


def index_closes(seed: int, kind: str) -> list:
    rng = random.Random(seed)
    price, out = 4200.0, []
    plan = [(460, 0.0006, 0.008), (60, -0.0035, 0.01)] if kind == "weak" else [(520, 0.0011, 0.006)]
    for days, mu, sigma in plan:
        for _ in range(days):
            price *= math.exp(mu + sigma * rng.gauss(0, 1))
            out.append(price)
    return out


def write_json(path: Path, data, indent=2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


# ---------------------------------------------------------------- main

def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)  # handles symlinks, case-insensitive filesystems, '..' and relative paths
    except OSError:  # one of them does not exist yet
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _protected(out: Path) -> bool:
    """True when `out` is the data dir, inside it, or an ancestor of it or of the project."""
    real = Path(os.path.realpath(ROOT / "data"))
    root = Path(os.path.realpath(ROOT))
    mine = [out, *out.parents]
    if any(_same(a, real) for a in mine):  # out == data/ or somewhere inside it
        return True
    return any(_same(out, b) for p in (real, root) for b in (p, *p.parents))  # out == or contains data/ or the project


def main(argv) -> int:
    out = Path(os.path.realpath(Path(argv[1] if len(argv) > 1 else ROOT / "data_demo").expanduser()))
    if _protected(out):
        print(f"refusing: {out} is (or contains) the real data/ dir or the project", file=sys.stderr)
        return 2
    if out.exists():
        if not out.is_dir() or (not (out / MARKER).exists() and any(out.iterdir())):
            print(f"refusing: {out} is not empty and was not created by load_demo.py", file=sys.stderr)
            return 2
        shutil.rmtree(out)  # only reached for a directory this script created (marker present) or an empty one
    out.mkdir(parents=True)
    (out / MARKER).write_text("synthetic demo dataset; safe to delete\n")  # first thing: a crashed run stays re-runnable
    os.environ["STOCK_DASHBOARD_DATA"] = str(out)  # config reads it once at import
    os.environ["STOCK_DASHBOARD_DEMO"] = "1"
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "scripts"))
    build(out)
    print(f"demo dataset written to {out}")
    return 0


@contextlib.contextmanager
def canned_llm_window():
    """Demo mode off ONLY while the canned (stubbed) model answer is compiled; subprocess and network raise
    inside the window, and demo mode is restored in finally."""
    import config
    import requests

    def boom(*a, **k):
        raise RuntimeError("network/subprocess attempted while demo mode was off")
    saved = (config.DEMO, subprocess.run, subprocess.Popen, requests.get, requests.post, requests.Session.request)
    config.DEMO = False
    subprocess.run = subprocess.Popen = requests.get = requests.post = requests.Session.request = boom
    try:
        yield
    finally:
        config.DEMO = True
        (_, subprocess.run, subprocess.Popen, requests.get, requests.post, requests.Session.request) = saved
        assert config.DEMO is True


def build(out: Path) -> None:
    import pandas as pd

    import data_provider as dp
    import market_health
    import meta_screen
    import pins
    import score_guards
    import snapshots
    import strategy
    import strategy_batch
    import strategy_runner as runner
    from analysis_profile import profile

    snapshots.utc_stamp = lambda: STAMP  # every "now" in generated files is fixed (deterministic output)
    dates = trading_days(N_DAYS, END)
    tickers = [t for t in SERIES if dp.is_valid_ticker(t)]
    candles = {}
    for t in tickers:
        name, seed, start, vol, seg = SERIES[t]
        candles[t] = candles_for(seed, closes_for(seed, start, seg, N_DAYS), dates, vol)

    # ---- tickers / favorites / price caches (exact data_provider cache format)
    write_json(out / "tickers.json", tickers)
    write_json(out / "favorites.json", ["DEMOA", "DEMOD.TW"])
    for t in tickers:
        f = dict(FUND[t])
        eps = f.pop("eps")
        hist = [{"date": d, "eps_estimate": e, "eps_actual": a,
                 "surprise_pct": round((a - e) / abs(e) * 100, 1) if e else None}
                for d, (e, a) in zip(["2026-08-12", "2026-05-13", "2026-02-11", "2025-11-12"], reversed(eps))]
        fund = {"name": SERIES[t][0], "sector": f.pop("sector"), "industry": f.pop("industry"), **f,
                "earnings_history": hist}
        write_json(out / "price_cache" / f"{t}.json",
                   {"candles": candles[t], "fundamentals": fund, "fetched_at": "2026-11-06T18:00:00"}, indent=None)

    # ---- market cache from the REAL market_health._fetch on a synthetic index (no network: yf is stubbed)
    def frame(closes):
        return pd.DataFrame({"Close": closes})
    series = {"^GSPC": index_closes(2, "ok"), "^IXIC": index_closes(8, "ok"), "^SOX": index_closes(9, "ok"),
              "^TWII": index_closes(10, "weak")}

    class _Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period="2y", **kw):
            df = frame(series[self.symbol])
            return df.tail(22) if period == "1mo" else df
    real_yf = market_health.yf
    market_health.yf = types.SimpleNamespace(Ticker=_Ticker)
    try:
        for m in ("us", "tw"):
            write_json(out / "market_cache" / f"{m}.json",
                       {"_fetched_at": 1793000000.0, "data": market_health._fetch(m)}, indent=None)
    finally:
        market_health.yf = real_yf

    # ---- holdings (DEMOA: second lot cheaper = averaging down; DEMOB: one lot; DEMOC: none)
    ca = candles["DEMOA"]
    i1 = max(range(900, 1000), key=lambda i: ca[i]["close"])
    i2 = min(range(i1 + 20, i1 + 120), key=lambda i: ca[i]["close"])
    ib = 1300 - 260
    lots = {"DEMOA": [{"id": "demo-a-1", "date": ca[i1]["time"], "price": ca[i1]["close"], "shares": 40},
                      {"id": "demo-a-2", "date": ca[i2]["time"], "price": ca[i2]["close"], "shares": 60}],
            "DEMOB": [{"id": "demo-b-1", "date": candles["DEMOB"][ib]["time"],
                       "price": candles["DEMOB"][ib]["close"], "shares": 100}]}
    assert lots["DEMOA"][1]["price"] < lots["DEMOA"][0]["price"], "demo averaging-down lot must be cheaper"
    write_json(out / "holdings.json", lots)

    # ---- notes
    (out / "strategy").mkdir()
    (out / "strategy" / NOTES_NAME).write_text(SAMPLE_NOTES, encoding="utf-8")

    # ---- analysis (technical/market axes + blocks from the real functions)
    news_items = lambda t, nm: [  # noqa: E731
        {"title": f"示範新聞：{nm} 公布示範季度財報（虛構內容）", "publisher": "示範通訊社", "date": "2026-11-04", "link": None},
        {"title": f"示範新聞：{nm} 宣布示範產品計畫（虛構內容）", "publisher": "示範財經", "date": "2026-10-29", "link": None},
        {"title": f"示範新聞：分析師調整 {nm} 示範目標價（虛構內容）", "publisher": "示範週報", "date": "2026-10-22", "link": None}]
    macro = [{"title": "示範新聞：示範央行維持利率不變（虛構內容）", "publisher": "示範通訊社", "date": "2026-11-05", "link": None},
             {"title": "示範新聞：示範景氣指標小幅回升（虛構內容）", "publisher": "示範財經", "date": "2026-11-03", "link": None}]
    analyses = {}
    for t in tickers:
        analyses[t] = make_analysis(t, dates, candles[t], lots.get(t), news_items(t, SERIES[t][0]), macro,
                                    dp, meta_screen, market_health, score_guards, profile)
        write_json(out / "analysis" / f"{t}.json", analyses[t])

    # ---- analysis history (4 earlier snapshots per ticker + the latest) and their run files
    rng = random.Random(99)
    run_ids = []
    for k, (stamp, mdate) in enumerate([("20260807T120000000000Z", "2026-08-07"), ("20260904T120000000000Z", "2026-09-04"),
                                        ("20261002T120000000000Z", "2026-10-02"), (STAMP, END.isoformat())]):
        for t in tickers:
            d = json.loads(json.dumps(analyses[t]))
            if k < 3:
                d["scores"] = {a: max(5, min(95, v + rng.randint(-9, 9))) for a, v in d["scores"].items()}
            px = next(c["close"] for c in candles[t] if c["time"] == mdate)
            snapshots.save_snapshot(snapshots.snapshot_of(d, generated_at=stamp, market_date=mdate, engine="demo",
                                                          price=px, profile="demo", run_id=stamp))
        snapshots.save_run(stamp, tickers, tickers, [], [], False)
        run_ids.append(stamp)

    # ---- llm usage (illustrative numbers only)
    rows = []
    for i, (kind, t) in enumerate([("analysis", "DEMOA"), ("analysis", "DEMOB"), ("analysis", "DEMOC"),
                                   ("strategy", "DEMOA"), ("strategy", "DEMOB"), ("pin_compile", "pin"), ("chat", "q")]):
        rows.append({"ts": f"2026-11-0{i + 1}T09:{10 + i}:00+00:00", "engine": "demo", "tag": f"demo:{kind}:{t}",
                     "seconds": round(20 + 7.5 * i, 1), "ok": True, "input_tokens": 3 + i,
                     "cache_creation_tokens": 2100 + 40 * i, "cache_read_tokens": 600 + 90 * i,
                     "output_tokens": 900 + 130 * i, "cost_usd": round(0.021 + 0.004 * i, 4)})
    (out / "llm_usage.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    # ---- pins: the REAL template compiler; the one that needs an event calendar is "unsupported"
    pins.secrets = types.SimpleNamespace(token_hex=lambda n, _c=iter(range(1, 99)): f"{0xde0000000000 + next(_c):012x}")
    pins.ga.pick_engine = lambda prefer=None: "demo"
    pins.ga.run_llm = lambda prompt, engine, tag="": json.dumps({"status": "unsupported", "reason": "needs_event_calendar"})
    stub = pins.ga.run_llm
    with canned_llm_window():  # the pin compiler refuses the LLM path in demo mode; open a guarded window for the stub
        assert pins.ga.run_llm is stub
        for text in PIN_TEXTS:
            pid = pins.create(text)["id"]
            c = pins.compile_and_store(pid)["compiled"]
            if c["state"] == "draft":
                pins.confirm(pid, pins.load()["revision"])
    assert [p["compiled"]["state"] for p in pins.load()["pins"]] == ["confirmed"] * 4 + ["unsupported"]

    # ---- strategy checks: the REAL pipeline (prepare -> verify_rows -> pins) with a canned model answer
    results = {}
    for t in tickers:
        prep = runner.prepare(t, notes_dir=out / "strategy")
        rows = canned_rows(prep, t)
        runner.ga.run_llm = lambda prompt, engine, tag="", _r=rows: json.dumps(_r, ensure_ascii=False)
        res = runner.run_check(t, engine="demo", notes_dir=out / "strategy")
        res["generated_at"] = STAMP
        assert res["rows_proposed"] == len(res["rows"]), (t, res["warnings"])
        runner.save_result(t, res)
        results[t] = res
    pin_cols = [p for p in pins.enabled_pins() if p["scope"] in ("now", "both")]
    write_json(out / "strategy_checks" / "_batch.json", {
        "status": "done", "progress": {"done": len(tickers), "total": len(tickers), "current": None},
        "overview": strategy_batch.build_overview(results, {}, pin_cols), "errors": {},
        "started_at": "2026-11-06T12:00:00+00:00", "finished_at": "2026-11-06T12:04:30+00:00"}, indent=None)
    (out / "backups").mkdir(exist_ok=True)


def make_analysis(t, dates, candles, lots, news, macro, dp, meta_screen, market_health, score_guards, profile):
    name = SERIES[t][0]
    meta, dossier = meta_screen.meta_gate(t), meta_screen.technical_dossier(t)
    health = market_health.market_health(t)
    fund = dp.fundamentals(t)
    q = dp.quote(t)
    price, d, w = q["price"], dossier["daily"], dossier["weekly"]
    mom = dossier["momentum"]
    stage2 = bool(meta and meta.get("stage2"))
    fa, va, nw = HAND_SCORES[t]
    held = lots is not None
    trade = (f"日線：收{price}，MA10={d['ma10']}／MA20={d['ma20']}／MA50={d['ma50']}／MA200={d['ma200']}，"
             f"{'站上並多頭排列' if stage2 else '未形成Stage2多頭排列'} -> 週線：{w['trend']} -> 季線：63MA={dossier['quarterly']['ma63']} -> "
             f"動能：RSI14={mom['rsi14']}，近5日{mom['ret_5d_pct']:+.1f}%、近20日{mom['ret_20d_pct']:+.1f}% -> "
             f"操作訊號：{'續抱，跌破50日線再重新評估' if stage2 else '觀望，等站回50日線並放量再說'}（示範內容，非投資建議）")
    own = {"DEMOA": "你有兩筆買進，第二筆價格比第一筆低（向下攤平），目前整體成本與損益請看持倉面板。這段只是示範說明，不影響分數。",
           "DEMOB": "你有一筆買進，目前處於帳面虧損；示範筆記的規則是跌破50日線不加碼。這段只是示範說明，不影響分數。"}.get(t, "")
    data = {
        "why_moved": f"示範：{name}近期走勢由示範資料產生，主要反映合成的價格趨勢。",
        "profit_bullets": [f"{name}示範獲利項目一（虛構）", f"{name}示範獲利項目二（虛構）"],
        "thesis": f"{name}（虛構公司）在示範資料中呈現{'穩定上升趨勢，營收與獲利同步成長' if stage2 else '趨勢尚未轉強，基本面與價格互相拉扯'}；這份分析是為展示介面而手寫的示範內容。",
        "moat": f"{name}的護城河為示範文字：虛構的品牌與轉換成本，無法對應任何真實公司。",
        "financial_health": (f"示範：淨利率{fund.get('profit_margins')}、自由現金流{fund.get('free_cashflow')}、"
                             f"負債權益比{fund.get('debt_to_equity')}（皆為虛構數字）。"),
        "competitive_landscape": "示範：虛構的產業有三到四家主要競爭者，彼此以價格與通路競爭。",
        "catalysts": ["示範：下一次財報公布（虛構日期）", "示範：新產品發表（虛構事件）"],
        "valuation_view": f"示範：本益比{fund.get('trailing_pe') or '無（虧損）'}、預估本益比{fund.get('forward_pe')}，與虛構同業比較屬於{'偏高' if (fund.get('forward_pe') or 0) > 25 else '合理'}區間。",
        "news_summary": "示範：以下新聞皆為虛構，只用來展示新聞區塊的排版。",
        "risks": ["示範：需求放緩（虛構風險）", "示範：利率環境變化（虛構風險）"],
        "verdict": VERDICT[t],
        "trade_signal": trade,
        "world_context": "示範：總體環境為虛構的溫和成長，央行利率維持不變，沒有任何真實世界局勢對應。",
        "bear_case": f"示範反方：若{name}跌破50日線且量增，趨勢濾網失效，示範策略會建議降低部位。",
        "reality_check": "示範：這是合成資料，價格走勢由亂數產生，任何看起來像訊號的地方都只是巧合。",
        "position_note": own,
        "scores": {"fundamentals": fa, "valuation": va, "news": nw},
        "score_reasons": {
            "fundamentals": f"{fa}分：示範評分——營收年增{fund.get('revenue_growth_yoy')}、淨利率{fund.get('profit_margins')}（虛構）。",
            "valuation": f"{va}分：示範評分——預估本益比{fund.get('forward_pe')}（虛構）。",
            "news": f"{nw}分：示範評分——示範新聞偏{'正面' if nw >= 55 else '中性偏弱'}（虛構）。"},
        "warnings": [], "profile": "demo", "ticker": t, "as_of": END.isoformat(), "generated_at": STAMP,
        "run_id": STAMP, "market_date": END.isoformat(), "schema_version": 1,
    }
    data["scores"]["technical"] = meta_screen.technical_score(meta)
    data["scores"]["market"] = market_health.market_score(health)
    data["score_reasons"]["technical"] = meta_screen.technical_reason(meta, dossier)
    data["score_reasons"]["market"] = market_health.market_reason(health)
    data["score_caps"] = score_guards.apply_guards(data, fund, dossier, q.get("pct_from_high"), health)
    data.update(fundamentals=fund, meta_gate=meta, technical_dossier=dossier, market_health=health,
                news=news, macro=macro)
    return data


def canned_rows(prep, ticker):
    """What a model could answer, built from the registry so the verdicts match the facts. Quotes are verbatim
    from the sample notes; strategy.verify_rows (real) then checks every quote, offset and fact reference."""
    import fact_catalog as fc
    reg = prep.registry

    def num(k):
        v = fc.parse_fact_value(reg[k]) if k in reg else None
        return v[1] if v and v[0] == "number" else None

    def src(quote):
        return next(c.id for c in prep.selected if quote in c.text)
    Q = {"trend": "只做第二階段（Stage2）的股票：股價必須站上50日均線，且50日均線高於200日線。",
         "stop": "單筆交易的停損不可以超過百分之七，這是不可談判的規則。",
         "rr": "報酬風險比至少要達到三比一，否則這筆交易不值得做。",
         "rsi": "RSI 超過 70 的時候不要追價，等回檔整理後再重新評估是否進場。",
         "avg": "突破失敗時不要加碼攤平，虧損的部位不可以再加碼。",
         "vol": "等待量縮整理後的突破，突破當天成交量需要放大到二十日均量的一點三倍以上。"}

    def row(rule, key, status, refs, scope="now", lot=None, **kw):
        return {"rule": rule, "source": src(Q[key]), "quote": Q[key], "status": status, "fact_refs": refs,
                "scope": scope, "lot": lot, **kw}

    def judge(ok):
        return "unknown" if ok is None else ("pass" if ok else "fail")
    now = [row("只做 Stage2 趨勢股", "trend", judge(reg.get("TECH.stage2") == "True" if "TECH.stage2" in reg else None),
               [k for k in ("TECH.stage2", "DAILY.ma50_rising", "DAILY.ma200_rising") if k in reg])]
    stop = num("TECH.stop_pct")
    now.append(row("停損幅度不超過 7%", "stop", judge(None if stop is None else stop <= 7), ["TECH.stop_pct"] if stop is not None else []))
    rr = num("TECH.rr")
    now.append(row("報酬風險比至少 3", "rr", judge(None if rr is None else rr >= 3), ["TECH.rr"] if rr is not None else []))
    rsi = num("TECH.rsi14")
    now.append(row("RSI 過熱不追價", "rsi", judge(None if rsi is None else rsi <= 70), ["TECH.rsi14"] if rsi is not None else []))
    if reg.get("HOLD.held") == "True":
        now.append(row("不攤平", "avg", judge(reg.get("HOLD.avg_down") == "False"), ["HOLD.avg_down", "HOLD.avg_down_count"]))
    else:
        now.append(row("不攤平", "avg", "conditional", ["HOLD.held"],
                       if_held="已持有：虧損時不要再加碼，先確認趨勢濾網是否還成立。",
                       if_not_held="尚未持有：等突破成功再進場，進場後就不再向下加碼。"))
    entry = []
    for n in sorted(int(k[3:-5]) for k in reg if k.startswith("BUY") and k.endswith(".date")):
        b = lambda s: f"BUY{n}.{s}"  # noqa: E731
        entry.append(row("買入當時是否為 Stage2", "trend", judge(reg.get(b("stage2")) == "True" if b("stage2") in reg else None),
                         [k for k in (b("stage2"), b("price_vs_ma50_pct")) if k in reg], "entry", n))
        v = num(b("vol_vs_20d"))
        entry.append(row("買入當時是否放量突破", "vol", judge(None if v is None else v >= 1.3), [b("vol_vs_20d")] if v is not None else [], "entry", n))
        s = num(b("stop_pct"))
        entry.append(row("買入當時停損是否不超過 7%", "stop", judge(None if s is None else s <= 7), [b("stop_pct")] if s is not None else [], "entry", n))
    return now + entry


if __name__ == "__main__":
    sys.exit(main(sys.argv))
