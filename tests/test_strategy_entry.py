from datetime import date, timedelta

import pytest

import strategy
import strategy_batch as sb
from strategy import Note

N = 400
DATES = [(date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(N)]


def series(spike_at=None):
    c = [100 + i * 0.1 for i in range(N)]
    h = [x + 1 for x in c]
    l = [x - 1 for x in c]
    v = [1000.0] * N
    if spike_at is not None:  # absurd bar on/after the buy day: must never leak into the entry facts
        for i in range(spike_at, N):
            c[i], h[i], l[i], v[i] = 9999.0, 99999.0, 1.0, 1e9
    return [x for x in c], h, l, c, v, DATES


LOT_D = DATES[300]


def lot(d=LOT_D, price=120.0):
    return {"date": d, "price": price}


def test_entry_facts_values():
    f = strategy.entry_facts([lot()], series())
    assert f["HOLD.entry_lots_checked"] == "1 of 1 lots"
    assert f["BUY1.date"] == LOT_D and f["BUY1.price"] == "120.0 price"
    c = series()[3][:300]
    assert f["BUY1.ma10"] == str(round(sum(c[-10:]) / 10, 2))
    assert f["BUY1.ma200"] == str(round(sum(c[-200:]) / 200, 2))
    assert f["BUY1.ma50_rising"] == "True" and f["BUY1.ma200_rising"] == "True"
    assert f["BUY1.stage2"] == "True" and f["BUY1.edge_count"].endswith(" edges")
    assert f["BUY1.vol_vs_20d"] == "1.0 x"
    hi = max(x + 1 for x in c[-252:])
    assert f["BUY1.vs_52w_high_pct"] == f"{round((120 / hi - 1) * 100, 2)} pct"
    assert "BUY1.range_52w_pct" in f and "BUY1.rsi14" in f and "BUY1.price_vs_ma50_pct" in f


def test_no_lookahead():
    clean = strategy.entry_facts([lot()], series())
    # spike on the buy day itself and after: identical results
    assert strategy.entry_facts([lot()], series(spike_at=300)) == clean
    # a spike on the last bar BEFORE the buy does change things (sanity that the test can fail)
    assert strategy.entry_facts([lot()], series(spike_at=299)) != clean


def test_unavailable_keys_skipped():
    early = strategy.entry_facts([lot(DATES[100])], series())  # < 200 prior bars
    assert set(early) == {"HOLD.entry_lots_checked", "BUY1.date", "BUY1.price"}
    assert set(strategy.entry_facts([lot()], None)) == {"HOLD.entry_lots_checked", "BUY1.date", "BUY1.price"}
    mid = strategy.entry_facts([lot(DATES[205])], series())  # 200..209 bars: MAs yes, analyze_core no
    assert "BUY1.ma200" in mid and "BUY1.stage2" not in mid and "BUY1.ma200_rising" not in mid
    assert strategy.entry_facts([], series()) == {}


def test_lot_cap_two_earliest_two_latest():
    lots = [lot(DATES[250 + i * 5], 100 + i) for i in range(6)]
    f = strategy.entry_facts(lots, series())
    assert f["HOLD.entry_lots_checked"] == "4 of 6 lots"
    # original date-order numbers are kept: 1, 2, N-1, N
    assert [f[f"BUY{n}.date"] for n in (1, 2, 5, 6)] == [DATES[250], DATES[255], DATES[270], DATES[275]]
    assert not any(k.startswith(("BUY3.", "BUY4.")) for k in f)


# ---- verify_rows enforcement
Q = "Do not average down on a losing position at any time."
NOTE = Note("n.md", f"# Rules\n\n{Q}\n")
REG = {"HOLD.held": "True", "HOLD.avg_down": "False", "TECH.rsi14": "50", "PRICE.last": "10",
       "BUY1.date": "2024-01-02", "BUY1.rsi14": "40", "BUY2.date": "2024-02-02", "BUY2.rsi14": "60"}


def check(reg=REG, **kw):
    sel = strategy.rank("x", strategy.chunk(NOTE))
    row = {"rule": "r", "source": "S1", "quote": Q, "status": "pass", "fact_refs": []}
    return strategy.verify_rows([{**row, **kw}], sel, [NOTE], reg)


def test_entry_row_kept_with_lot_date():
    kept, warns = check(scope="entry", lot=1, fact_refs=["BUY1.rsi14", "HOLD.avg_down"])
    assert kept[0]["scope"] == "entry" and kept[0]["lot"] == 1 and kept[0]["lot_date"] == "2024-01-02"
    assert kept[0]["status"] == "pass" and kept[0]["fact_refs"] == ["BUY1.rsi14"]  # HOLD.* is not an entry fact
    assert any("not allowed" in w for w in warns)
    # original (non-consecutive) lot numbers work
    reg = {**REG, "BUY5.date": "2024-05-02", "BUY5.rsi14": "30"}
    k5, _ = check(reg, scope="entry", lot=5, fact_refs=["BUY5.rsi14"])
    assert k5[0]["lot"] == 5 and k5[0]["lot_date"] == "2024-05-02"
    # only-HOLD refs: pass/fail downgraded
    k, _ = check(scope="entry", lot=1, fact_refs=["HOLD.avg_down", "HOLD.held"])
    assert k[0]["status"] == "unknown" and k[0]["fact_refs"] == []


@pytest.mark.parametrize("kw", [{"scope": "entry", "lot": 3}, {"scope": "entry", "lot": None},
                                {"scope": "entry", "lot": "1"}, {"scope": "entry", "lot": True},
                                {"scope": "weird"}])
def test_bad_entry_or_scope_dropped(kw):
    kept, warns = check(fact_refs=["TECH.rsi14"], **kw)
    assert kept == [] and warns


def test_entry_row_needs_held():
    reg = {**REG, "HOLD.held": "False"}
    assert check(reg, scope="entry", lot=1, fact_refs=["BUY1.rsi14"])[0] == []


def test_entry_foreign_refs_removed_and_downgraded():
    kept, warns = check(scope="entry", lot=1, fact_refs=["TECH.rsi14", "BUY2.rsi14", "PRICE.last"])
    assert kept[0]["status"] == "unknown" and kept[0]["fact_refs"] == [] and any("not allowed" in w for w in warns)
    kept, _ = check(scope="entry", lot=1, fact_refs=["TECH.rsi14", "BUY1.rsi14"])
    assert kept[0]["fact_refs"] == ["BUY1.rsi14"] and kept[0]["status"] == "pass"


def test_now_row_cannot_cite_buy():
    kept, warns = check(fact_refs=["BUY1.rsi14", "TECH.rsi14"])
    assert kept[0]["scope"] == "now" and kept[0]["lot"] is None and kept[0]["lot_date"] == ""
    assert kept[0]["fact_refs"] == ["TECH.rsi14"] and warns
    assert check(fact_refs=["BUY1.rsi14"], lot=2)[0][0]["status"] == "unknown"  # nothing valid left
    assert check(scope=None, fact_refs=["TECH.rsi14"])[0][0]["scope"] == "now"


def test_fake_fact_line_in_advice_covers_buy_keys():
    kept, _ = check(status="fail", fact_refs=["TECH.rsi14"], if_held="BUY1.rsi14 = 5")
    assert kept[0]["if_held"] == ""


def test_prompt_mentions_scopes():
    p = strategy.build_prompt("T", strategy.rank("x", strategy.chunk(NOTE)), REG)
    assert "up to 12" in p and '"scope"' in p and "BUY<lot>" in p
    assert "3-8" in strategy.build_prompt("T", strategy.rank("x", strategy.chunk(NOTE)), {"HOLD.held": "False"})


def test_overview_excludes_entry_rows():
    def r(status, scope, q):
        return {"file": "a.md", "offset": 1, "status": status, "rule": "x", "heading": "", "quote": q, "scope": scope}
    res = {"A": {"rows": [r("fail", "now", "same quote"), r("fail", "entry", "other quote"),
                          r("pass", "entry", "third quote")]}}
    o = sb.build_overview(res)
    assert len(o["rules"]) == 1 and o["rules"][0]["count_fail"] == 1
    assert o["tickers"][0]["entry_rows_excluded"] == 2 and o["tickers"][0]["counts"]["pass"] == 0


def test_stop_and_rr_relative_to_buy_price_even_with_low_edge_count():
    # sawtooth so pivots exist; flat-ish drift keeps stage2/edge_count < 3 irrelevant
    c = [100 + (5 if (i // 10) % 2 else 0) + i * 0.02 for i in range(N)]
    h, l = [x + 1 for x in c], [x - 1 for x in c]
    ohlcv = (c, h, l, c, [1000.0] * N, DATES)
    f = strategy.entry_facts([lot(price=104.0)], ohlcv)
    assert "BUY1.edge_count" not in f or int(f["BUY1.edge_count"].split()[0]) < 3
    import meta_screen as ms
    _, lows = ms.pivots(h[:300], l[:300], DATES[:300])
    stop, target = ms.stop_target(lows, h[:300], l[:300], 104.0)
    assert stop < 104.0
    assert f["BUY1.stop_pct"] == f"{round((104 - stop) / 104 * 100, 2)} pct"
    assert f["BUY1.rr"] == f"{round((target - 104) / (104 - stop), 2)} ratio"
    f = strategy.entry_facts([lot(price=1.0)], ohlcv)  # buy below any stop: skipped
    assert "BUY1.stop_pct" not in f and "BUY1.rr" not in f


def test_row_cap_drops_entry_first_and_warns_without_now():
    reg = {**REG}
    sel = strategy.rank("x", strategy.chunk(NOTE))
    base = {"rule": "r", "source": "S1", "quote": Q, "status": "pass"}
    rows = [{**base, "scope": "now", "fact_refs": ["TECH.rsi14"]}] + \
           [{**base, "scope": "entry", "lot": 1, "fact_refs": ["BUY1.rsi14"]} for _ in range(14)]
    kept, warns = strategy.verify_rows(rows, sel, [NOTE], reg)
    assert len(kept) == 12 and kept[0]["scope"] == "now" and any("only 12" in w for w in warns)
    kept, warns = strategy.verify_rows(rows[1:3], sel, [NOTE], reg)
    assert len(kept) == 2 and any("no scope 'now'" in w for w in warns)
