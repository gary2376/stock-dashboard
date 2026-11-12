import random

import pytest

import pin_expr as pe


def ev(expr, reg, scope="now"):
    return pe.evaluate(pe.parse(expr, scope), reg.get)


def test_parse_render_roundtrip_and_facts():
    a = pe.parse("TECH.rsi14 <= 70 and (PRICE.last > DAILY.ma200 or TECH.stage2 == true)")
    assert pe.facts_of(a) == ["TECH.rsi14", "PRICE.last", "DAILY.ma200", "TECH.stage2"]
    assert pe.parse(pe.render(a)) == a
    assert pe.render(pe.parse("TECH.stop_pct <= 7")) == "TECH.stop_pct <= 7 pct"  # bare number adopts the unit
    assert pe.parse('WEEKLY.trend == "x"')["right"] == {"text": "x"}


def test_nfkc_normalisation():
    a = pe.parse("TECH.rsi14 ＞＝ ７０ ＡＮＤ TECH.stage2 ＝＝ true".replace("ＡＮＤ", "and"))
    assert pe.render(a) == "TECH.rsi14 >= 70 and TECH.stage2 == true"
    assert pe.render(pe.parse("TECH.rsi14 ≤ 70")) == "TECH.rsi14 <= 70"


@pytest.mark.parametrize("bad", [
    "", " ", "TECH.rsi14", "TECH.rsi14 <", "TECH.rsi14 > > 3", "NOPE.x > 1", "TECH.rsi14 > 70 pct",  # unit on unit-less
    "TECH.stop_pct > 5 price", "PRICE.last > TECH.stop_pct", "TECH.stage2 > true", "TECH.stage2 == 1",
    "TECH.rsi14 == true", 'TECH.rsi14 == "a"', "WEEKLY.trend > \"a\"", "PRICE.last > DAILY.ma200 pct",
    "BUY1.rsi14 > 5", "BUY{lot}.rsi14 > 5", "not TECH.stage2 == true", "abs(TECH.rsi14) > 1",
    "TECH.rsi14 + 1 > 2", "__import__('os')", "TECH.rsi14 > 70; drop", "TECH.rsi14 > 7 0", "(TECH.rsi14 > 7",
    "TECH.rsi14 > 7)", "TECH.rsi14 > 1e5", "TECH.rsi14 > 70 and", "and TECH.rsi14 > 70", "TECH.rsi14 = 70",
    "TECH.rsi14 > 70 or or TECH.rsi14 < 3", "TECH.rsi14 > 9999999999999999", "TECH.rsi14 > 70 x",
    "WEEKLY.trend == \"" + "a" * 41 + "\"", "TECH.rsi14 > ٧٠",  # arabic-indic digits are not ASCII digits
])
def test_rejected(bad):
    with pytest.raises(ValueError):
        pe.parse(bad)


def test_scope_rules():
    with pytest.raises(ValueError):
        pe.parse("BUY{lot}.rsi14 < 30", "now")
    with pytest.raises(ValueError):
        pe.parse("TECH.rsi14 < 30", "entry")
    with pytest.raises(ValueError):
        pe.parse("BUY{lot}.rsi14 < 30 and TECH.rsi14 < 30", "entry")
    with pytest.raises(ValueError):
        pe.parse("HOLD.avg_down == false", "entry")
    assert pe.facts_of(pe.parse("BUY{lot}.price > BUY{lot}.ma200", "entry")) == ["BUY{lot}.price", "BUY{lot}.ma200"]
    with pytest.raises(ValueError):
        pe.parse("BUY{lot}.price > BUY{lot}.rsi14", "entry")  # price vs none


def test_limits():
    cmp_ = "TECH.rsi14 > 1"
    assert pe.parse(" and ".join([cmp_] * 10))  # 11 nodes
    with pytest.raises(ValueError):
        pe.parse(" and ".join([cmp_] * 20))  # 21 nodes
    with pytest.raises(ValueError):
        pe.parse("TECH.rsi14 > 1 " + "and TECH.rsi14 > 1 " * 12)
    nested = "TECH.rsi14 > 1"
    for _ in range(4):
        nested = f"({nested} and TECH.rsi14 > 2)"
    with pytest.raises(ValueError):
        pe.parse(nested)  # depth 5
    with pytest.raises(ValueError):
        pe.parse("(" * 50 + "TECH.rsi14 > 1" + ")" * 50)
    assert pe.parse("((TECH.rsi14 > 1))")["type"] == "cmp"  # parens alone add no depth
    nine = " and ".join(f"FUND.{k} > 1" for k in ("trailing_pe", "forward_pe", "beta", "revenue_growth_yoy",
                                                  "gross_margins", "profit_margins", "return_on_equity",
                                                  "debt_to_equity", "free_cashflow"))
    with pytest.raises(ValueError):
        pe.parse(nine)  # 9 facts
    with pytest.raises(ValueError):
        pe.parse("TECH.rsi14 > 1 " + " " * 300)


def test_three_valued_truth_tables():
    reg = {"A": None}
    T, F, U = "PRICE.last > 5", "PRICE.last < 5", "DAILY.ma10 > 5"  # U: fact missing
    r = {"PRICE.last": "10"}
    cases = {f"{T} and {U}": "unknown", f"{F} and {U}": "fail", f"{T} or {U}": "pass", f"{F} or {U}": "unknown",
             f"{T} and {T}": "pass", f"{T} and {F}": "fail", f"{F} or {F}": "fail", f"{U}": "unknown",
             f"{U} and {U}": "unknown", f"{U} or {U}": "unknown", f"({F} or {U}) and {T}": "unknown",
             f"({T} or {U}) and {F}": "fail"}
    for expr, want in cases.items():
        assert ev(expr, r) == want, expr


def test_evaluate_values_and_missing():
    reg = {"TECH.rsi14": "72.4", "TECH.stop_pct": "6.1 pct", "TECH.stage2": "True", "PRICE.last": "10",
           "DAILY.ma200": "9.5", "WEEKLY.trend": "10週>40週(多方)", "DAILY.ma50": "garbage"}
    assert ev("TECH.rsi14 <= 70", reg) == "fail" and ev("TECH.stop_pct <= 7", reg) == "pass"
    assert ev("TECH.stage2 == true", reg) == "pass" and ev("TECH.stage2 != true", reg) == "fail"
    assert ev("PRICE.last > DAILY.ma200", reg) == "pass"
    assert ev('WEEKLY.trend == "10週>40週(多方)"', reg) == "pass"
    assert ev("DAILY.ma50 > 1", reg) == "unknown" and ev("DAILY.ma10 > 1", reg) == "unknown"  # unparseable / missing
    assert ev("TECH.stop_pct == 6.1", reg) == "pass"


def test_resolve_and_evidence():
    a = pe.parse("BUY{lot}.price > BUY{lot}.ma200", "entry")
    r = pe.resolve(a, 3)
    assert pe.facts_of(r) == ["BUY3.price", "BUY3.ma200"] and pe.facts_of(a)[0] == "BUY{lot}.price"
    reg = {"BUY3.price": "120.0 price", "BUY3.ma200": "100.5"}
    assert pe.evaluate(r, reg.get) == "pass"
    assert pe.evidence(r, reg.get) == "BUY3.price=120.0 price vs > BUY3.ma200=100.5"
    assert pe.evidence(pe.parse("TECH.rsi14 <= 70"), {"TECH.rsi14": "72.4"}.get) == "TECH.rsi14=72.4 vs <= 70"
    assert "(無資料)" in pe.evidence(pe.parse("TECH.rsi14 <= 70"), {}.get)


def test_fuzz_only_valueerror():
    rnd = random.Random(7)
    atoms = ["TECH.rsi14", "PRICE.last", "BUY{lot}.price", "and", "or", "(", ")", "<", ">=", "==", "70", "-3.5", "pct",
             "true", '"a"', "%", "＞", "\x00", "BUY1.x", "not", "'", ";", "٣", "ａ"]
    for _ in range(3000):
        s = " ".join(rnd.choice(atoms) for _ in range(rnd.randint(0, 25)))
        for scope in ("now", "entry"):
            try:
                a = pe.parse(s, scope)
            except ValueError:
                continue
            assert pe.parse(pe.render(a), scope) == a  # whatever parses must round-trip
    for s in ["A" * 5000, "(" * 5000, "TECH.rsi14 > " + "9" * 5000, "\u0000" * 300, "＞" * 100, "TECH.rsi14 > 1 " * 400]:
        with pytest.raises(ValueError):
            pe.parse(s)
    for s in ["'; DROP TABLE pins;--", "${jndi:ldap://x}", "TECH.rsi14 > 1 #", "__class__.__mro__", "1 if 1 else 2"]:
        with pytest.raises(ValueError):
            pe.parse(s)


def test_runtime_unit_enforcement():
    pct, price = "TECH.stop_pct <= 7", "DAILY.ma50 > 1"
    assert ev(pct, {"TECH.stop_pct": "6.1 pct"}) == "pass"
    assert ev(pct, {"TECH.stop_pct": "6.1 price"}) == "unknown"      # wrong unit: never compared
    assert ev(pct, {"TECH.stop_pct": "6.1"}) == "unknown"            # pct values must carry their suffix
    assert ev(price, {"DAILY.ma50": "9"}) == "pass" and ev(price, {"DAILY.ma50": "9 price"}) == "pass"
    assert ev(price, {"DAILY.ma50": "9 pct"}) == "unknown"
    assert ev("TECH.edge_count >= 3", {"TECH.edge_count": "3"}) == "pass"
    assert ev("TECH.rsi14 > 1", {"TECH.rsi14": "abc"}) == "unknown"
    assert ev("PRICE.last > DAILY.ma200", {"PRICE.last": "10", "DAILY.ma200": "9 pct"}) == "unknown"
    assert ev("TECH.stage2 == true", {"TECH.stage2": "1"}) == "unknown"
    assert ev("BUY{lot}.stop_pct < 5", {"BUY1.stop_pct": "1 ratio"}.get and {}, "entry") == "unknown"


def test_describe_is_deterministic_from_ast():
    d = pe.describe
    assert d(pe.parse("TECH.rsi14 <= 70")) == "RSI(14) 不高於 70 才算符合"
    assert d(pe.parse("TECH.stop_pct < 7 and TECH.stage2 == false")) == "以最近swing low估的停損幅度 低於 7% 且 Stage2 趨勢未成立才算符合"
    assert d(pe.parse("HOLD.avg_down == false")) == "沒有攤平才算符合"
    assert d(pe.parse("BUY{lot}.vol_vs_20d >= 2 x", "entry"), "entry").startswith("買入當時（每一筆）：")
    assert "(" in d(pe.parse("(TECH.rsi14 > 1 or TECH.rr > 1) and TECH.stage2 == true"))
