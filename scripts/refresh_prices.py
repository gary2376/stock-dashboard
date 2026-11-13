"""Cron/launchd target: refresh the on-disk price cache (data/price_cache/{TICKER}.json) for
every watched ticker. Run daily — the slow/expensive codex write-up is a separate weekly job
(generate_analysis.py), this one is just cheap yfinance history + fundamentals.

Usage: python3 scripts/refresh_prices.py [TICKER ...]
No args = every ticker in data/tickers.json.
"""
import json
import sys
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import data_provider as dp  # noqa: E402
from config import DATA_DIR  # noqa: E402


def refresh(ticker: str):
    df = yf.Ticker(ticker).history(period="5y", auto_adjust=False)
    if df.empty:
        print(f"[{ticker}] no price data, skipped")
        return
    fund = dp.fetch_fundamentals_live(ticker)
    dp._write_cache(ticker, df=df, fundamentals_dict=fund)
    print(f"[{ticker}] refreshed: {len(df)} candles, name={fund.get('name')}")


if __name__ == "__main__":
    tickers = sys.argv[1:] or json.loads((DATA_DIR / "tickers.json").read_text())
    failures = []
    for t in tickers:
        if not dp.is_valid_ticker(t):
            print(f"[{t}] FAILED: invalid ticker")
            failures.append(t)
            continue
        try:
            refresh(t)
        except Exception as e:
            print(f"[{t}] FAILED: {e}")
            failures.append(t)
    if failures:
        print(f"{len(failures)}/{len(tickers)} 檔失敗：{', '.join(failures)}")
        sys.exit(1)
