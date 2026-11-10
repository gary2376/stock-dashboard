"""Dated news + world/macro headlines for the analysis prompt — free, keyless RSS (Google News,
Yahoo Finance). Replaces data_provider's yfinance `.news`, which needs a Yahoo cookie/crumb
handshake that times out on some networks and then silently returns [] for every ticker (the news
axis then sat at a neutral 50 for the whole watchlist).

Everything here fails soft: a dead feed returns [] so the analysis degrades to "no news found",
never an exception. Headlines are untrusted external text — callers must treat them as data, and
the frontend must escape them and only link http(s) URLs.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests

from atomic import write_text_atomic

from config import DATA_DIR  # noqa: E402

CACHE_DIR = DATA_DIR / "market_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (stock-dashboard personal use)"}
TTL_SECONDS = 3600
MAX_AGE_DAYS = 45

# Pages that look like news results but are quote / chart / "should you buy" boilerplate — they
# crowded real headlines out of the top-N (e.g. every AA result was a quote page or option chart).
JUNK = re.compile(
    r"(stock )?price,? news,? quote|quote (and|&) history|interactive stock chart|price forecast|"
    r"stock forecast|should you buy|stock (price )?today|trades at (EUR|USD)|\bput\b.*\bstock chart|"
    r"global (strategic|industry|business).{0,30}report|market report|research and markets|"
    r"xstock|coinbase|live charts|marketcap|"
    r"call transcript|\b10-[QK]\b|\b8-K\b|schedules .{0,40}(earnings|dividend)|"
    r"報價|歷史股價|即時行情|股價走勢圖",
    re.I,
)

# (locale params for Google News, queries). Market-wide + world context, per market.
MACRO_QUERIES = {
    "us": (("en-US", "US", "US:en"), [
        "Wall Street stocks Fed rates inflation",
        "tariffs trade war economy oil geopolitics",
        "AI spending chips data center stocks",
    ]),
    "tw": (("zh-TW", "TW", "TW:zh-Hant"), [
        "台股 大盤 外資 收盤",
        "美股 聯準會 關稅 通膨",
        "AI 伺服器 半導體 供應鏈",
    ]),
}

# titles about corporate events that move a stock outside the earnings cycle (dilution, M&A, ratings)
EVENT = re.compile(
    r"offering|dilut|convertible|acquisition|acquire|merger|downgrade|upgrade|lawsuit|probe|investigation|"
    r"buyback|repurchase|recall|guidance|增資|併購|收購|下調|調降|訴訟|庫藏股|減資|現增",
    re.I,
)

# titles that report an earnings result / the stock's reaction to it (the earnings query's filter)
REACTION = re.compile(
    r"earnings|results|beat|miss|guidance|outlook|quarter|plunge|sink|slump|tumble|crash|surge|soar|"
    r"jump|climb|rally|fall|drop|財報|法說|營收|毛利|獲利|EPS|展望|指引",
    re.I,
)

_CORP_SUFFIX = re.compile(
    r",?\s+(Inc\.?|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|Holdings?|Group|plc|N\.V\.|S\.A\.)\s*$",
    re.I,
)


def _clean_name(name: str) -> str:
    prev = None
    while prev != name:
        prev, name = name, _CORP_SUFFIX.sub("", name).strip(" ,")
    return name


def _parse_rss(xml_text: str, default_publisher: str | None = None) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        publisher = (item.findtext("source") or "").strip() or default_publisher
        # Google News appends " - Publisher" to the title; peel it off when <source> is missing
        if not publisher and " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        elif publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        date = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                date = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except (TypeError, ValueError):
                date = None
        out.append({
            "title": title.strip(),
            "publisher": publisher,
            "date": date.strftime("%Y-%m-%d") if date else None,
            "_dt": date,
            "link": (item.findtext("link") or "").strip() or None,
        })
    return out


def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=10)
        return r.text if r.ok else None
    except requests.RequestException:
        return None


def _google(query: str, locale: tuple[str, str, str], days: int = 14) -> list[dict]:
    hl, gl, ceid = locale
    url = (f"https://news.google.com/rss/search?q={quote_plus(query + f' when:{days}d')}"
           f"&hl={hl}&gl={gl}&ceid={ceid}")
    text = _get(url)
    return _parse_rss(text) if text else []


def _yahoo(ticker: str) -> list[dict]:
    text = _get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(ticker)}&region=US&lang=en-US")
    return _parse_rss(text, default_publisher="Yahoo Finance") if text else []


def _merge(*lists: list[dict], limit: int, relevant=None, by_date: bool = True) -> list[dict]:
    """Dedupe, drop junk/stale/irrelevant, newest first. `relevant(title)` is optional — used for
    company queries so a search for 'Alcoa stock' can't return headlines about other tickers."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    seen, merged = set(), []
    for item in (i for lst in lists for i in lst):
        key = re.sub(r"\W+", "", item["title"].lower())[:60]
        if (key in seen or JUNK.search(item["title"]) or (item["_dt"] and item["_dt"] < cutoff)
                or (relevant and not relevant(item["title"]))):
            continue
        seen.add(key)
        merged.append(item)
    if by_date:  # else keep the feed's own relevance order (earnings query: biggest stories first)
        merged.sort(key=lambda i: i["_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [{k: v for k, v in i.items() if k != "_dt"} for i in merged[:limit]]


def _cached(name: str, build) -> list[dict]:
    f = CACHE_DIR / f"{name}.json"
    try:
        c = json.loads(f.read_text())
        if time.time() - c["_fetched_at"] < TTL_SECONDS:
            return c["items"]
    except (OSError, ValueError, KeyError):
        pass
    items = build()
    if items:  # don't cache an empty result — a transient feed failure shouldn't stick for an hour
        write_text_atomic(f, json.dumps({"_fetched_at": time.time(), "items": items}, ensure_ascii=False))
    return items


def _relevance(ticker: str, name: str):
    """Title must mention the company (cleaned full name / Chinese name) or the ticker symbol itself."""
    if ticker.endswith((".TW", ".TWO")):
        code = ticker.split(".")[0]
        return lambda t: name in t or code in t
    full = _clean_name(name).lower()
    t = re.escape(ticker)
    # short tickers ('AA') collide with ordinary words/labels ("Class AA title") — they only count in
    # their formal contexts: (AA), NYSE:AA, NASDAQ:AA. Longer ones can stand alone.
    sym = re.compile(rf"\({t}\)|(?:NYSE|NASDAQ|NYSEARCA)(?::\s*|\s+){t}\b" if len(ticker) <= 3
                     else rf"(?<![A-Za-z]){t}(?![A-Za-z])")
    return lambda t: full in t.lower() or bool(sym.search(t))


def ticker_news(ticker: str, name: str, limit: int = 9) -> list[dict]:
    """Company-specific headlines (latest 5 + earnings reaction 4 + corporate events 3, deduped): the latest few, PLUS a dedicated earnings-reaction query over a
    longer window — the quarter's beat/miss and the stock's reaction is the single most useful fact
    about a company and usually lands 2-5 weeks before the analysis runs, outside a 14-day window."""
    def build():
        rel = _relevance(ticker, name)
        is_tw = ticker.endswith((".TW", ".TWO"))
        if is_tw:
            code = ticker.split(".")[0]
            loc = MACRO_QUERIES["tw"][0]
            latest = _merge(_google(f"{name} 股價", loc), _google(f"{name} {code}", loc),
                            limit=limit - 4, relevant=rel)
            earn = _merge(_google(f"{name} 財報 法說會 營收", loc, days=45), limit=4,
                          relevant=lambda t: rel(t) and REACTION.search(t))
        else:
            loc = MACRO_QUERIES["us"][0]
            latest = _merge(_google(f"{_clean_name(name)} stock", loc), _yahoo(ticker),
                            limit=limit - 4, relevant=rel)
            earn = _merge(_google(f"{_clean_name(name)} earnings results", loc, days=45), limit=4,
                          relevant=lambda t: rel(t) and REACTION.search(t))
        events = _merge(_google(f"{name if is_tw else _clean_name(name)} offering OR acquisition OR downgrade OR lawsuit"
                                if not is_tw else f"{name} 增資 OR 併購 OR 調降 OR 訴訟", loc, days=45), limit=3,
                        relevant=lambda t: rel(t) and EVENT.search(t))
        have = set()
        merged = []
        for i in latest + earn + events:
            k = re.sub(r"\W+", "", i["title"].lower())[:60]
            if k not in have:
                have.add(k)
                merged.append(i)
        return sorted(merged, key=lambda i: i.get("date") or "", reverse=True)
    return _cached(f"news_{re.sub(r'[^A-Za-z0-9]', '_', ticker)}", build)


def macro_headlines(market: str, limit: int = 10) -> list[dict]:
    """Market-wide / world headlines (rates, tariffs, geopolitics, AI capex) for 'tw' or 'us'."""
    locale, queries = MACRO_QUERIES[market]
    return _cached(f"macro_{market}",
                   lambda: _merge(*[_google(q, locale) for q in queries], limit=limit))
