"""yfinance-backed data layer: quotes, history, trend-sigma, range-position bar, fundamentals.

Price history + fundamentals are cached to disk under data/price_cache/{TICKER}.json. A ticker
is served straight from that file once it has one; scripts/refresh_prices.py (meant to run daily
via cron/launchd) is what keeps it fresh. A ticker with no cache file yet (just added) falls back
to a live yfinance fetch here, which also writes the cache so it doesn't live-fetch on every view.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from atomic import write_text_atomic

from config import DATA_DIR  # noqa: E402

CACHE_DIR = DATA_DIR / "price_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# tickers are used directly as filenames (data/price_cache/{TICKER}.json,
# data/analysis/{TICKER}.json) — reject anything that isn't a plain symbol so a
# crafted ticker string can't escape those directories (e.g. "../../etc/passwd")
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")


def is_valid_ticker(ticker: str) -> bool:
    return bool(TICKER_RE.fullmatch(ticker))


def _finite(v):
    """yfinance occasionally returns NaN/inf for edge-case fundamentals (e.g. near-zero
    earnings blowing up a ratio) — FastAPI's strict JSON encoder rejects those outright."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v

# TWSE/TPEx open data: official Chinese company names, keyed by bare stock code.
# yfinance only ever returns the English name for .TW/.TWO tickers.
_TW_NAME_SOURCES = [
    ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "公司代號", "公司簡稱", "公司名稱"),
    ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "SecuritiesCompanyCode", "CompanyAbbreviation", "CompanyName"),
]
_TW_NAME_TTL = 24 * 3600
_tw_names: dict = {}
_tw_names_loaded_at = 0.0


def _load_tw_names():
    global _tw_names, _tw_names_loaded_at
    now = time.time()
    if _tw_names and now - _tw_names_loaded_at < _TW_NAME_TTL:
        return
    names = {}
    try:
        for url, code_key, short_key, full_key in _TW_NAME_SOURCES:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            for row in resp.json():
                code = row.get(code_key)
                if code:
                    names[code] = {"short": row.get(short_key), "full": row.get(full_key)}
    except Exception:
        pass  # keep whatever we already had; fundamentals() falls back to the English name
    if names:
        _tw_names = names
        _tw_names_loaded_at = now


def tw_chinese_name(ticker: str):
    if not (ticker.endswith(".TW") or ticker.endswith(".TWO")):
        return None
    _load_tw_names()
    entry = _tw_names.get(ticker.split(".")[0])
    return entry["short"] if entry else None

# row counts are approximate trading days; slicing is tail(n) off one cached 5y fetch
RANGES = {
    "1m": 22,
    "3m": 66,
    "6m": 130,
    "1y": 252,
    "3y": 756,
}

def _cache_path(ticker: str) -> Path:
    if not is_valid_ticker(ticker):
        raise ValueError(f"invalid ticker: {ticker!r}")
    return CACHE_DIR / f"{ticker}.json"


def _read_cache(ticker: str):
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_cache(ticker: str, df=None, fundamentals_dict=None):
    """Merges into the existing cache file rather than overwriting — history and fundamentals
    are written by different callers at different times (first live fetch, or the refresh script)."""
    existing = _read_cache(ticker) or {}
    if df is not None and not df.empty:
        # yfinance sometimes returns an incomplete last bar (NaN close) for today's still-forming
        # candle, especially right around market open on non-US exchanges — drop it rather than
        # cache a NaN, which later fails strict JSON encoding (allow_nan=False) on every response.
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        existing["candles"] = [
            {
                "time": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                # volume needs float() first — yfinance gives numpy int64/float64, and a bare
                # NaN (occasionally present for thin tickers) must become 0, not a json.dumps crash.
                "volume": int(row["Volume"]) if "Volume" in row and math.isfinite(float(row["Volume"])) else 0,
            }
            for idx, row in df.iterrows()
        ]
    if fundamentals_dict is not None:
        existing["fundamentals"] = fundamentals_dict
    existing["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    write_text_atomic(_cache_path(ticker), json.dumps(existing, ensure_ascii=False))


def _candles_to_df(candles_list):
    # defensive: filter out any NaN rows that made it into a cache file written before the
    # dropna fix in _write_cache (e.g. an incomplete today's bar from a prior fetch)
    candles_list = [c for c in candles_list if all(
        isinstance(c[k], (int, float)) and not math.isnan(c[k]) for k in ("open", "high", "low", "close")
    )]
    df = pd.DataFrame(candles_list)
    df.index = pd.to_datetime([c["time"] for c in candles_list])
    # "volume" (added after this cache format already existed) may be absent on rows cached
    # before that change — only rename it when present, same reasoning as the dropna filter above.
    rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close"}
    if "volume" in df.columns:
        rename["volume"] = "Volume"
    return df.rename(columns=rename)


def _history_full(ticker: str):
    """Disk-cached 5y history; every range below slices off this, satisfying the >=3y requirement.
    Reads the on-disk cache (kept warm by scripts/refresh_prices.py); live-fetches and writes
    the cache only the first time a brand-new ticker has no cache file yet."""
    cached = _read_cache(ticker)
    if cached and cached.get("candles"):
        return _candles_to_df(cached["candles"])
    df = yf.Ticker(ticker).history(period="5y", auto_adjust=False)
    if not df.empty:
        _write_cache(ticker, df=df)
    return df


def _slice(ticker: str, range_key: str):
    df = _history_full(ticker)
    n = RANGES.get(range_key, RANGES["1y"])
    return df.tail(n)


def trend_sigma(ticker: str, range_key: str = "1y") -> dict:
    """Deviation of current price from a log-linear trend fit over the given range, in std-devs of residuals."""
    df = _slice(ticker, range_key)
    closes = df["Close"].dropna()
    if len(closes) < 20:
        return {"sigma": 0.0, "label": "資料不足", "desc": "資料不足"}
    y = [float(v) for v in closes.apply(math.log).to_numpy()]
    x = list(range(len(y)))
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var = sum((xi - mean_x) ** 2 for xi in x)
    slope = cov / var if var else 0.0
    intercept = mean_y - slope * mean_x
    resid = [yi - (slope * xi + intercept) for xi, yi in zip(x, y)]
    std = (sum(r * r for r in resid) / n) ** 0.5 if n else 0.0
    last_resid = resid[-1]
    sigma = last_resid / std if std else 0.0
    sigma = max(-3.0, min(3.0, sigma))
    label = "低位階" if sigma <= -1 else ("高位階" if sigma >= 1 else "中位階")
    desc = "價格便宜｜低於長期趨勢" if sigma <= -1 else (
        "價格偏貴｜高於長期趨勢" if sigma >= 1 else "價格合理｜在長期趨勢附近")
    return {"sigma": round(sigma, 2), "label": label, "desc": desc}


def range_position(ticker: str, range_key: str) -> dict:
    df = _slice(ticker, range_key)
    if df.empty:
        return {"pct": 50.0, "low": None, "high": None, "current": None}
    low = float(df["Low"].min())
    high = float(df["High"].max())
    current = float(df["Close"].iloc[-1])
    pct = 50.0 if high == low else (current - low) / (high - low) * 100
    pct = max(0.0, min(100.0, pct))
    return {"pct": round(pct, 1), "low": round(low, 2), "high": round(high, 2), "current": round(current, 2)}


def quote(ticker: str) -> dict:
    df = _slice(ticker, "1y")
    if df.empty:
        return {"price": None, "pct_from_high": None, "prev_close": None}
    current = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else current
    high_1y = float(df["High"].max())
    pct_from_high = (current - high_1y) / high_1y * 100
    return {
        "price": round(current, 2),
        "prev_close": round(prev_close, 2),
        "day_change_pct": round((current - prev_close) / prev_close * 100, 2) if prev_close else 0.0,
        "pct_from_high": round(pct_from_high, 1),
        "high_1y": round(high_1y, 2),
    }


_EARNINGS_WARNED: list = []  # warn once per process, not once per ticker


def earnings_history(ticker: str):
    """Last four reported quarters (EPS estimate vs actual, surprise %) and the next scheduled date,
    from yfinance's earnings-dates table. Structured numbers beat headline-scraping: it is what tells
    the analysis whether a quarter was a beat or a miss (the model otherwise has to say "unknown").
    Returns ([], None) on any failure — a missing table must not break the refresh."""
    try:
        table = yf.Ticker(ticker).get_earnings_dates(limit=8)
    except Exception as e:
        if not _EARNINGS_WARNED:
            _EARNINGS_WARNED.append(1)
            print(f"[earnings_history] 抓取失敗，之後的失敗不再提示（{type(e).__name__}: {e}）；"
                  "常見原因：缺 lxml 或 yfinance 版本太舊，請重裝 requirements.txt")
        return [], None
    if table is None or table.empty:
        return [], None
    today = datetime.now().strftime("%Y-%m-%d")
    past, future = [], []
    for idx, row in table.iterrows():
        d = idx.strftime("%Y-%m-%d")
        reported = row.get("Reported EPS")
        if reported is None or (isinstance(reported, float) and not math.isfinite(reported)):
            if d >= today:
                future.append(d)
            continue
        est, sur = row.get("EPS Estimate"), row.get("Surprise(%)")
        past.append({"date": d, "eps_estimate": _finite(est), "eps_actual": _finite(float(reported)),
                     "surprise_pct": _finite(sur)})
    past.sort(key=lambda r: r["date"], reverse=True)
    return past[:4], (min(future) if future else None)


def fetch_fundamentals_live(ticker: str) -> dict:
    """Actually hits yfinance (+ the TWSE/TPEx name lookup). Called by fundamentals() below for a
    brand-new ticker with no cache yet, and directly by scripts/refresh_prices.py for the daily refresh."""
    # yfinance's get_info() occasionally hiccups right after a fresh ticker is first queried
    # (rate limiting) — one quick retry avoids falling back to the bare ticker code as the name.
    info = {}
    for attempt in range(2):
        try:
            info = yf.Ticker(ticker).get_info()
            if info.get("shortName") or info.get("longName"):
                break
        except Exception:
            pass
        if attempt == 0:
            time.sleep(0.8)
    name = tw_chinese_name(ticker) or info.get("shortName") or info.get("longName") or ticker
    history, next_date = earnings_history(ticker)
    earnings_date = next_date
    if earnings_date is None:  # the info timestamp can be stale (already-reported) — last resort only
        ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        if ts:
            try:
                earnings_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                earnings_date = None
    return {
        "name": name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "trailing_pe": _finite(info.get("trailingPE")),
        "forward_pe": _finite(info.get("forwardPE")),
        "revenue_growth_yoy": _finite(info.get("revenueGrowth")),
        "earnings_growth_yoy": _finite(info.get("earningsGrowth")),
        "gross_margins": _finite(info.get("grossMargins")),
        "trailing_eps": _finite(info.get("trailingEps")),
        "market_cap": _finite(info.get("marketCap")),
        # balance-sheet / profitability / risk fields — without these the "財務體質" section had
        # nothing to cite and kept answering "資料缺失"
        "total_cash": _finite(info.get("totalCash")),
        "total_debt": _finite(info.get("totalDebt")),
        "debt_to_equity": _finite(info.get("debtToEquity")),
        "free_cashflow": _finite(info.get("freeCashflow")),
        "operating_margins": _finite(info.get("operatingMargins")),
        "profit_margins": _finite(info.get("profitMargins")),
        "return_on_equity": _finite(info.get("returnOnEquity")),
        "beta": _finite(info.get("beta")),
        # valuation: sell-side analyst targets, free via yfinance's own info dict — no extra API
        "target_mean_price": _finite(info.get("targetMeanPrice")),
        "target_high_price": _finite(info.get("targetHighPrice")),
        "target_low_price": _finite(info.get("targetLowPrice")),
        "num_analyst_opinions": _finite(info.get("numberOfAnalystOpinions")),
        "recommendation_key": info.get("recommendationKey"),
        # next known binary event — the same kind of event that moves a stock around earnings
        "next_earnings_date": earnings_date,
        "earnings_history": history,
    }


def fundamentals(ticker: str) -> dict:
    cached = _read_cache(ticker)
    if cached and cached.get("fundamentals"):
        return cached["fundamentals"]
    try:
        result = fetch_fundamentals_live(ticker)
        if result.get("name") != ticker:
            _write_cache(ticker, fundamentals_dict=result)
        return result
    except Exception:
        return {"name": ticker}


def ohlcv_lists(ticker: str):
    """Full cached history (up to 5y) as parallel o/h/l/c/v/dates lists — the shape
    backend/meta_screen.py's META-screen logic expects (it was written against raw
    Yahoo chart-API arrays and kept in that shape rather than reshaped to pandas, so this is
    the adapter)."""
    df = _history_full(ticker)
    if df.empty:
        return None
    dates = [idx.strftime("%Y-%m-%d") for idx in df.index]
    o = [float(x) for x in df["Open"].tolist()]
    h = [float(x) for x in df["High"].tolist()]
    l = [float(x) for x in df["Low"].tolist()]
    c = [float(x) for x in df["Close"].tolist()]
    v = [float(x) if "Volume" in df.columns and math.isfinite(x) else 0.0
         for x in (df["Volume"].tolist() if "Volume" in df.columns else [0.0] * len(df))]
    return o, h, l, c, v, dates


def candles(ticker: str, range_key: str) -> list[dict]:
    df = _slice(ticker, range_key)
    out = []
    for idx, row in df.iterrows():
        out.append({
            "time": idx.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            # older caches may lack volume (or hold NaN): omit rather than send a bogus 0
            **({"volume": int(row["Volume"])} if "Volume" in df.columns and math.isfinite(float(row["Volume"])) else {}),
        })
    return out


def ticker_exists(ticker: str) -> bool:
    try:
        df = yf.Ticker(ticker).history(period="5d")
        return not df.empty
    except Exception:
        return False


def resolve_ticker(raw: str):
    """Accept a bare ticker (AVGO) or a bare Taiwan stock code (2330) and find
    the symbol yfinance actually has data for, trying .TW then .TWO."""
    candidates = [raw]
    if raw.isdigit():
        candidates += [f"{raw}.TW", f"{raw}.TWO"]
    for c in candidates:
        if ticker_exists(c):
            return c
    return None
