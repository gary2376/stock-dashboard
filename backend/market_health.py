"""大盤方向健康度 — the "market" axis of the 5-dimension analysis, and a precondition for
reading any single-stock technical setup (is the broad market on your side? 50MA/200MA are the
market-regime indicators used here). TW tickers are checked against 加權指數 (^TWII), everything else
against S&P 500 (^GSPC) — the same two indices this project's cards already surface in passing
(data_provider doesn't track them per-ticker today; this module is the first thing that does).

Cached to disk (data/market_cache/{tw,us}.json) with a 1h TTL — this is background context for
every ticker's analysis, not a per-ticker quote, so it doesn't need price_cache's per-ticker
freshness and shouldn't trigger its own yfinance hit on every request.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yfinance as yf

from atomic import write_text_atomic

from config import DATA_DIR  # noqa: E402

CACHE_DIR = DATA_DIR / "market_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

INDEX_FOR = {"tw": "^TWII", "us": "^GSPC"}
# Spelled out in desc/reason text — left to guess from a bare "^TWII" the model once wrote
# "香港加權指數" for the Taiwan index.
INDEX_NAME = {"^TWII": "台灣加權指數", "^GSPC": "S&P 500"}
# context-only side indices (not scored): what the headlines call a "chip crash" shows up in
# these long before it moves a broad index's 50MA
SIDE_INDICES = {"us": [("^IXIC", "那斯達克"), ("^SOX", "費城半導體指數")], "tw": [("^SOX", "費城半導體指數")]}
TTL_SECONDS = 3600


def market_of(ticker: str) -> str:
    return "tw" if ticker.endswith(".TW") or ticker.endswith(".TWO") else "us"


def _chg_5d(symbol: str):
    try:
        c = [float(x) for x in yf.Ticker(symbol).history(period="1mo")["Close"].dropna().tolist()]
        return round((c[-1] / c[-6] - 1) * 100, 1) if len(c) >= 6 else None
    except Exception:
        return None


def _fetch(market: str) -> dict:
    symbol = INDEX_FOR[market]
    df = yf.Ticker(symbol).history(period="2y", auto_adjust=False)
    closes = [float(x) for x in df["Close"].dropna().tolist()]
    if len(closes) < 230:
        return {"symbol": symbol, "healthy": None, "desc": "指數資料不足，無法判斷"}
    price = closes[-1]
    s50 = sum(closes[-50:]) / 50
    s200 = sum(closes[-200:]) / 200
    s50_20 = sum(closes[-70:-20]) / 50
    s200_20 = sum(closes[-220:-20]) / 200
    ma50_rising, ma200_rising = s50 >= s50_20, s200 >= s200_20
    healthy = price > s50 > s200 and ma50_rising and ma200_rising
    # recent shock context — a healthy-by-MA index can still be mid-crash/rebound (MAs lag), and
    # the score/reason below needs to say so instead of reading as calm
    chg_5d = (price / closes[-6] - 1) * 100
    drawdown = (price / max(closes[-252:]) - 1) * 100
    side = {label: _chg_5d(sym) for sym, label in SIDE_INDICES[market]}
    side_txt = "；".join(f"{k}近5日{v:+.1f}%" for k, v in side.items() if v is not None)
    label = f"{INDEX_NAME[symbol]}（{symbol}）"
    desc = (
        f"{label}健康：站上50MA({s50:.0f})/200MA({s200:.0f})，兩線上揚 — 大勢在多方"
        if healthy else
        f"{label}轉弱：跌破均線，或均線走平／下彎 — 大勢不在多方這邊，宜保守"
    ) + f"；近5日{chg_5d:+.1f}%，距一年高點{drawdown:+.1f}%" + (f"；參考：{side_txt}" if side_txt else "")
    return {"symbol": symbol, "side_5d": side, "price": round(price, 2), "ma50": round(s50, 2),
            "ma200": round(s200, 2), "ma50_rising": ma50_rising, "ma200_rising": ma200_rising,
            "chg_5d_pct": round(chg_5d, 1), "drawdown_pct": round(drawdown, 1),
            "healthy": healthy, "desc": desc}


def market_health(ticker: str) -> dict:
    market = market_of(ticker)
    cache_file = CACHE_DIR / f"{market}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            # "chg_5d_pct" check: a cache written before that field existed is the wrong shape
            if time.time() - cached.get("_fetched_at", 0) < TTL_SECONDS and "side_5d" in cached["data"]:
                return cached["data"]
        except (json.JSONDecodeError, KeyError):
            pass
    try:
        data = _fetch(market)
    except Exception:
        return {"symbol": INDEX_FOR[market], "healthy": None, "desc": "大盤資料抓取失敗"}
    write_text_atomic(cache_file, json.dumps({"_fetched_at": time.time(), "data": data}, ensure_ascii=False))
    return data


def market_score(health: dict) -> int:
    """Tiered, each tier explained by market_reason() below:
    healthy (price>50MA>200MA, both rising) 80, or 65 if it just took a sharp hit (5日<=-4% or
    距一年高點<=-10%); short-term broken but still above a rising 200MA 45; otherwise 25."""
    if health.get("healthy") is None:
        return 50
    shocked = health.get("chg_5d_pct", 0) <= -4 or health.get("drawdown_pct", 0) <= -10
    if health["healthy"]:
        return 65 if shocked else 80
    if health.get("price", 0) > health.get("ma200", 1e18) and health.get("ma200_rising"):
        return 45
    return 25


def market_reason(health: dict) -> str:
    """Plain-language 「為什麼是N分」 for the market axis, derived from the same fields as the score."""
    score = market_score(health)
    if health.get("healthy") is None:
        return f"{score}分：{health.get('desc')}，無法判斷，給中性分。"
    sym, p = health["symbol"], health["price"]
    side = health.get("side_5d") or {}
    side_txt = "；".join(f"{k}近5日{v:+.1f}%" for k, v in side.items() if v is not None)
    facts = (f"{INDEX_NAME.get(sym, sym)}（{sym}）收{p}，50MA={health['ma50']}（{'上揚' if health['ma50_rising'] else '走平/下彎'}），"
             f"200MA={health['ma200']}（{'上揚' if health['ma200_rising'] else '走平/下彎'}）；"
             f"近5日{health['chg_5d_pct']:+.1f}%，距一年高點{health['drawdown_pct']:+.1f}%"
             + (f"；{side_txt}（僅供參考，不計入分數）。" if side_txt else "。"))
    if score == 80:
        why = "價格站上50MA、50MA又在200MA之上且兩線上揚，近期也沒有劇烈回檔——大勢在多方。"
    elif score == 65:
        why = ("均線結構仍健康，但近期出現劇烈回檔（5日跌幅達4%或距一年高點回落10%以上），"
               "均線是落後指標，所以從80分扣到65分。")
    elif score == 45:
        why = "指數已跌破50MA或均線轉弱，但仍在上揚的200MA之上——中長期未破、短期轉弱，給偏中性分。"
    else:
        why = "價格與均線結構未達健康標準（跌破200MA，或200MA走平/下彎）——大勢不在多方，宜保守。"
    return f"{score}分：{facts}{why}"
