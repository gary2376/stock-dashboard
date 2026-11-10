from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from urllib.parse import unquote_plus

import pytest

import news_feed


def _rss(items):
    rows = []
    for title, days_ago, source in items:
        published = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))
        source_xml = f"<source>{escape(source)}</source>" if source else ""
        rows.append(
            f"<item><title>{escape(title)}</title>{source_xml}"
            f"<pubDate>{published}</pubDate><link>https://example.test/{days_ago}</link></item>"
        )
    return "<rss><channel>" + "".join(rows) + "</channel></rss>"


class _Response:
    ok = True

    def __init__(self, text):
        self.text = text


def test_parse_rss_normalizes_publisher_date_and_bad_xml():
    xml = _rss([
        ("Alcoa expands smelter - Reuters", 1, ""),
        ("Alcoa reports results - Bloomberg", 2, "Bloomberg"),
    ])

    items = news_feed._parse_rss(xml)

    assert [(i["title"], i["publisher"]) for i in items] == [
        ("Alcoa expands smelter", "Reuters"),
        ("Alcoa reports results", "Bloomberg"),
    ]
    assert all(i["date"] and i["_dt"].tzinfo == timezone.utc for i in items)
    assert news_feed._parse_rss("<not-valid") == []


def test_merge_filters_junk_stale_and_irrelevant_short_ticker_results():
    rel = news_feed._relevance("AA", "Alcoa Corporation")
    parsed = news_feed._parse_rss(_rss([
        ("Class AA soccer final ends in upset", 0, "Sports Desk"),
        ("Alcoa stock price, news, quote and history", 0, "Quote Site"),
        ("Alcoa opens a lower-carbon aluminum line", 1, "Reuters"),
        ("NYSE:AA rallies after guidance", 2, "Market Wire"),
        ("Alcoa old archive item", news_feed.MAX_AGE_DAYS + 1, "Archive"),
    ]))

    merged = news_feed._merge(parsed, limit=10, relevant=rel)

    assert [i["title"] for i in merged] == [
        "Alcoa opens a lower-carbon aluminum line",
        "NYSE:AA rallies after guidance",
    ]


def test_relevance_accepts_company_name_and_formal_short_ticker_contexts():
    rel = news_feed._relevance("AA", "Alcoa Corporation")

    assert rel("Alcoa signs a renewable power agreement")
    assert rel("Alcoa Corporation raises production guidance")
    assert rel("Why Alcoa (AA) shares rose today")
    assert rel("NYSE:AA announces quarterly results")
    assert not rel("Class AA soccer playoffs begin")
    assert not rel("AA battery prices fall at retailers")


@pytest.mark.xfail(
    strict=True,
    reason="short-ticker relevance only accepts exchange/parenthesized symbols, not the formal 'AA Inc' form",
)
def test_relevance_accepts_short_ticker_followed_by_corporate_suffix():
    # Suspected matcher gap: this is a corporate-name context, not the generic two-letter label.
    assert news_feed._relevance("AA", "Alcoa Corporation")("AA Inc announces a new aluminum alloy")


def test_relevance_accepts_exchange_prefix_with_space_after_colon():
    assert news_feed._relevance("AA", "Alcoa Corporation")("NYSE: AA announces quarterly results")


def test_keyword_buckets_cover_earnings_reactions_and_corporate_events():
    earnings = [
        "Alcoa earnings beat estimates",
        "Alcoa shares plunge after quarterly results",
        "Alcoa raises its outlook and guidance",
    ]
    events = [
        "Alcoa announces convertible offering",
        "Alcoa acquisition faces probe",
        "Alcoa board authorizes buyback",
    ]

    assert all(news_feed.REACTION.search(title) for title in earnings)
    assert all(news_feed.EVENT.search(title) for title in events)
    assert not news_feed.REACTION.search("Alcoa opens a new research center")
    assert not news_feed.EVENT.search("Alcoa reports quarterly earnings")


def test_ticker_news_uses_bucket_limits_and_returns_all_buckets_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(news_feed, "CACHE_DIR", tmp_path)
    latest = [(f"Alcoa latest story {i}", 12 + i, "Latest") for i in range(7)]
    earnings = [(f"Alcoa earnings beat story {i}", i, "Earnings") for i in range(6)]
    events = [(f"Alcoa acquisition story {i}", 6 + i, "Events") for i in range(5)]
    feeds = {
        "latest": _rss(latest),
        "earnings": _rss(earnings),
        "events": _rss(events),
        "yahoo": _rss([]),
    }

    def fake_get(url, **_kwargs):
        decoded = unquote_plus(url)
        if "feeds.finance.yahoo.com" in decoded:
            return _Response(feeds["yahoo"])
        if "earnings results" in decoded:
            return _Response(feeds["earnings"])
        if "offering OR acquisition" in decoded:
            return _Response(feeds["events"])
        return _Response(feeds["latest"])

    monkeypatch.setattr(news_feed.requests, "get", fake_get)

    items = news_feed.ticker_news("AA", "Alcoa Corporation")

    titles = [i["title"] for i in items]
    assert len(items) == 12
    assert sum("latest story" in t for t in titles) == 5
    assert sum("earnings beat story" in t for t in titles) == 4
    assert sum("acquisition story" in t for t in titles) == 3
    assert [i["date"] for i in items] == sorted((i["date"] for i in items), reverse=True)


def test_ticker_news_reads_fresh_cache_without_refetching(tmp_path, monkeypatch):
    monkeypatch.setattr(news_feed, "CACHE_DIR", tmp_path)
    calls = []
    feed = _rss([("Alcoa earnings beat estimates", 0, "Wire")])

    def fake_get(url, **_kwargs):
        calls.append(url)
        return _Response(feed)

    monkeypatch.setattr(news_feed.requests, "get", fake_get)

    first = news_feed.ticker_news("AA", "Alcoa Corporation")
    first_call_count = len(calls)
    second = news_feed.ticker_news("AA", "Alcoa Corporation")

    assert first and second == first
    assert first_call_count > 0
    assert len(calls) == first_call_count
    assert (tmp_path / "news_AA.json").is_file()
