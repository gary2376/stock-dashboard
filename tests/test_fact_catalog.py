from datetime import date, timedelta

import fact_catalog as fc
import strategy

N = 400
DATES = [(date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(N)]
C = [100 + i * 0.1 for i in range(N)]
OHLCV = (C, [x + 1 for x in C], [x - 1 for x in C], C, [1000.0] * N, DATES)


def test_every_emitted_registry_key_is_catalogued():
    daily = {f"ma{n}": 10.0 + n for n in (10, 20, 50, 200)}
    daily.update(ma50_rising=True, ma200_rising=False)
    dossier = {"daily": daily, "weekly": {"trend": "10週>40週(多方)"},
               "momentum": {"ret_5d_pct": 1.0, "ret_20d_pct": -2.0, "rsi14": 55.0}}
    meta = {"stage2": True, "edge_count": 3, "abnormal": False, "final_pass": True, "risk": 5.0, "reward": 9.0, "rr": 1.8}
    fund = {k: 1.5 for k in ("revenue_growth_yoy", "earnings_growth_yoy", "gross_margins", "operating_margins",
                             "profit_margins", "return_on_equity", "trailing_pe", "forward_pe", "debt_to_equity",
                             "beta", "free_cashflow", "total_cash", "total_debt", "market_cap")}
    health = {"healthy": True, "chg_5d_pct": 1.0, "drawdown_pct": -3.0, "side_5d": {"SOX": 2.0}}
    reg = strategy.build_registry(price=100.0, dossier=dossier, meta=meta, fund=fund, health=health)
    lots = [{"date": DATES[250], "price": 110.0, "shares": 10}, {"date": DATES[300], "price": 100.0, "shares": 5}]
    reg.update(strategy.holding_facts(lots, OHLCV, 120.0))
    reg.update(strategy.entry_facts(lots, OHLCV))
    assert len(reg) > 80
    missing = [k for k in reg if fc.lookup(k) is None]
    assert missing == []
    # keys in the catalog agree with how the registry formats them (unit suffix)
    for k, v in reg.items():
        e = fc.lookup(k)
        pv = fc.parse_fact_value(v)
        assert pv[0] == e["type"] or (e["type"] == "text" and pv[0] == "text"), (k, v, e)
        assert fc.value_usable(k, pv), (k, v, e)
        if pv[0] == "number":
            if pv[2] != "none":
                assert pv[2] == e["unit"], (k, v, e)
            else:  # suffix-less values only where the catalog says price/none/edges
                assert e["unit"] in fc.SUFFIXLESS_OK, (k, v, e)


def test_catalog_shape_and_scopes():
    cat = {e["key"]: e for e in fc.catalog_list()}
    assert cat["BUY{lot}.rsi14"]["scopes"] == ["entry"] and cat["TECH.rsi14"]["scopes"] == ["now"]
    assert fc.lookup("BUY3.stop_pct")["unit"] == "pct" and fc.lookup("BUY{lot}.stop_pct")
    assert fc.lookup("MARKET.SOX_5d")["unit"] == "pct" and fc.lookup("MARKET.healthy")["type"] == "bool"
    assert fc.lookup("NOPE.x") is None and fc.lookup("BUY3.nope") is None and fc.lookup(5) is None
    assert all(e["description"] for e in cat.values())


def test_parse_fact_value():
    p = fc.parse_fact_value
    assert p("-6.3 pct") == ("number", -6.3, "pct")
    assert p("1257687351296 abs") == ("number", 1257687351296.0, "abs")
    assert p("46.2 price") == ("number", 46.2, "price") and p("48.2") == ("number", 48.2, "none")
    assert p("True") == ("bool", True) and p("False") == ("bool", False)
    assert p("10週<40週(空方)") == ("text", "10週<40週(空方)") and p("2024-01-02") == ("text", "2024-01-02")
    assert p("nan")[0] == "text" and p("inf pct")[0] == "text" and p("4 of 6 lots")[0] == "text"


def test_value_usable_rules():
    u = fc.value_usable
    assert u("TECH.stop_pct", ("number", 6.1, "pct")) and not u("TECH.stop_pct", ("number", 6.1, "price"))
    assert not u("TECH.stop_pct", ("number", 6.1, "none"))  # pct keys need their suffix
    assert u("DAILY.ma50", ("number", 9.0, "none")) and u("DAILY.ma50", ("number", 9.0, "price"))
    assert not u("DAILY.ma50", ("number", 9.0, "pct")) and u("TECH.rsi14", ("number", 5.0, "none"))
    assert u("TECH.edge_count", ("number", 3.0, "none")) and u("BUY2.edge_count", ("number", 3.0, "edges"))
    assert not u("TECH.rsi14", ("text", "x")) and not u("TECH.stage2", ("number", 1.0, "none"))
    assert not u("NOPE.x", ("number", 1.0, "none"))
