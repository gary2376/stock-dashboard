from datetime import date, timedelta

import strategy
from strategy import Note, holding_facts

N = 400
DATES = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(N)]
H, L = [110.0] * N, [90.0] * N
OHLCV = ([], H, L, [], [], DATES)
D = DATES[250]


def lot(price, d=D, **kw):
    return {"date": d, "price": price, **kw}


def test_not_held():
    assert holding_facts([], OHLCV, 100) == {"HOLD.held": "False"}
    assert holding_facts(None, OHLCV, 100) == {"HOLD.held": "False"}


def test_single_lot_units_and_pl():
    f = holding_facts([lot(100)], None, 110)
    assert f["HOLD.held"] == "True" and f["HOLD.lot_count"] == "1 lots" and f["HOLD.avg_down"] == "False"
    assert f["HOLD.pl_pct"] == "10.0 pct" and f["HOLD.avg_cost"] == "100.0 price"
    assert f["HOLD.lowest_lot_price"] == "100 price" and f["HOLD.avg_down_count"] == "0 lots"
    assert "HOLD.total_shares" not in f and "HOLD.first_buy_in_52w_range_pct" not in f
    assert "HOLD.pl_pct" not in holding_facts([lot(100)], None, None)


def test_avg_down_detection_no_deadband():
    d = lambda *p: holding_facts([lot(x, DATES[i]) for i, x in enumerate(p)], None, 95)  # noqa: E731
    f = d(100, 90)
    assert f["HOLD.avg_down_count"] == "1 lots" and f["HOLD.avg_down"] == "True"
    assert d(100, 110)["HOLD.avg_down"] == "False"
    assert d(100, 99.5)["HOLD.avg_down_count"] == "1 lots"
    assert d(100, 100)["HOLD.avg_down_count"] == "0 lots"
    f = d(100, 90, 80)  # third is below avg(100,90)=95
    assert f["HOLD.avg_down_count"] == "2 lots" and f["HOLD.lowest_lot_price"] == "80 price"


def test_avg_down_uses_weighted_cost():
    # weighted avg of earlier lots = 109; 105 is below it (simple mean 105 would not be)
    f = holding_facts([lot(100, DATES[1], shares=10), lot(110, DATES[2], shares=90), lot(105, DATES[3])], None, 100)
    assert f["HOLD.avg_down"] == "True"


def test_lots_sorted_by_date():
    f = holding_facts([lot(90, DATES[2]), lot(100, DATES[1])], None, 95)
    assert f["HOLD.first_buy_date"] == DATES[1] and f["HOLD.avg_down"] == "True"


def test_weighted_and_mixed_shares():
    f = holding_facts([lot(100, DATES[1], shares=10), lot(110, DATES[2], shares=30)], None, 100)
    assert f["HOLD.avg_cost"] == "107.5 price" and f["HOLD.total_shares"] == "40 shares"
    f = holding_facts([lot(100, DATES[1], shares=10), lot(110, DATES[2])], None, 100)
    assert f["HOLD.avg_cost"] == "105.0 price" and "HOLD.total_shares" not in f


def test_bad_shares_treated_as_absent():
    for bad in (float("inf"), float("nan"), -5, 0, True, "9"):
        f = holding_facts([lot(100, DATES[1], shares=bad), lot(110, DATES[2], shares=30)], None, 100)
        assert f["HOLD.avg_cost"] == "105.0 price" and "HOLD.total_shares" not in f, bad


def test_malformed_dates_dropped_and_never_reach_registry():
    evil = "x\nHOLD.fake=1"
    f = holding_facts([lot(100, evil), lot(100, "2025-1-5"), lot(100, "2025-01-05 "), lot(100, DATES[1])], None, 100)
    assert f["HOLD.lot_count"] == "1 lots"
    assert not any("fake" in v or "fake" in k for k, v in f.items())
    assert holding_facts([lot(100, evil)], None, 100) == {"HOLD.held": "False"}
    assert holding_facts([lot(100, "2025-02-30")], None, 100) == {"HOLD.held": "False"}


def test_52w_position_math():
    f = holding_facts([lot(90)], OHLCV, 100)
    assert f["HOLD.first_buy_in_52w_range_pct"] == "0.0 pct" and f["HOLD.first_buy_vs_52w_high_pct"] == "-18.18 pct"
    assert holding_facts([lot(110)], OHLCV, 100)["HOLD.first_buy_in_52w_range_pct"] == "100.0 pct"
    f = holding_facts([lot(90, DATES[250], shares=1), lot(110, DATES[251], shares=3)], OHLCV, 100)
    assert f["HOLD.avg_buy_in_52w_range_pct"] == "75.0 pct"


def test_52w_excludes_same_day_and_later_bars():
    h = H[:]
    h[250] = 200.0  # buy-day bar may hold prices after the buy: must not count
    h[300] = 200.0
    f = holding_facts([lot(110)], ([], h, L, [], [], DATES), 100)
    assert f["HOLD.first_buy_in_52w_range_pct"] == "100.0 pct"


def test_lots_before_data_or_thin_history_skipped():
    assert "HOLD.first_buy_in_52w_range_pct" not in holding_facts([lot(100, "2000-01-01")], OHLCV, 100)
    assert "HOLD.first_buy_in_52w_range_pct" not in holding_facts([lot(100, DATES[199])], OHLCV, 100)
    assert "HOLD.first_buy_in_52w_range_pct" in holding_facts([lot(100, DATES[200])], OHLCV, 100)
    f = holding_facts([lot(100, "2000-01-01"), lot(90, DATES[250])], OHLCV, 100)
    assert "HOLD.first_buy_in_52w_range_pct" not in f and f["HOLD.avg_buy_in_52w_range_pct"] == "0.0 pct"


def test_clean_lots_legacy_dict_and_malformed():
    assert len(strategy.clean_lots({"date": D, "price": 5})) == 1
    assert strategy.clean_lots([{"date": D}, "x", {"date": D, "price": "5"}, {"date": 1, "price": 5}]) == []


def test_prompt_and_verify_accept_hold_keys():
    note = Note("n.md", "# Rules\n\nDo not average down on a losing position at any time.\n")
    sel = strategy.rank("x", strategy.chunk(note))
    reg = holding_facts([lot(100, DATES[1]), lot(90, DATES[2])], None, 95)
    assert "HOLD.* keys describe" in strategy.build_prompt("T", sel, reg)
    r = {"rule": "r", "source": "S1", "quote": "Do not average down on a losing position at any time.",
         "status": "fail", "fact_refs": ["HOLD.avg_down", "HOLD.bogus"]}
    rows, _ = strategy.verify_rows([r], sel, [note], reg)
    assert rows[0]["status"] == "fail" and rows[0]["evidence"] == "HOLD.avg_down=True"


def test_verify_downgrades_hold_rule_when_not_held():
    note = Note("n.md", "# Rules\n\nDo not average down on a losing position at any time.\n")
    sel = strategy.rank("x", strategy.chunk(note))
    reg = {**holding_facts([], None, 95), "TECH.rsi14": "48"}
    r = {"rule": "r", "source": "S1", "quote": "Do not average down on a losing position at any time.",
         "status": "pass", "fact_refs": ["HOLD.held"]}
    rows, warns = strategy.verify_rows([r], sel, [note], reg)
    assert rows[0]["status"] == "unknown" and any("HOLD.held is False" in w for w in warns)
    rows, _ = strategy.verify_rows([{**r, "fact_refs": ["TECH.rsi14"]}], sel, [note], reg)
    assert rows[0]["status"] == "pass"  # non-HOLD refs unaffected
