import json

import pytest

import strategy_runner as runner

Q = "Do not average down on a losing position at any time."


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "strategy").mkdir()
    (tmp_path / "strategy" / "n.md").write_text(f"# Rules\n\n{Q}\n", encoding="utf-8")
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runner.dp, "ohlcv_lists", lambda t: None)
    monkeypatch.setattr(runner.dp, "fundamentals", lambda t: {})
    monkeypatch.setattr(runner.meta_screen, "meta_gate", lambda t: {})
    monkeypatch.setattr(runner.meta_screen, "technical_dossier", lambda t: {})
    monkeypatch.setattr(runner.meta_screen, "technical_reason", lambda m, d: "")
    monkeypatch.setattr(runner.market_health, "market_health", lambda t: {})
    monkeypatch.setattr(runner.ga, "pick_engine", lambda prefer=None: "claude")
    return tmp_path


def llm(monkeypatch, rows):
    seen = {}
    monkeypatch.setattr(runner.ga, "run_llm", lambda prompt, engine, tag="": seen.update(p=prompt, e=engine) or json.dumps(rows))
    return seen


ROW = {"rule": "不攤平", "source": "S1", "quote": Q, "status": "conditional", "fact_refs": ["HOLD.held"],
       "if_held": "已持有不加碼", "if_not_held": "沒持有就等"}


def test_run_check_result_shape(env, monkeypatch):
    seen = llm(monkeypatch, [ROW])
    res = runner.run_check("TT")
    assert set(res) == {"ticker", "generated_at", "engine", "rows", "rows_proposed", "warnings", "notes",
                        "holdings_lots", "facts", "disclaimer", "entry_lots_checked", "pins"}
    assert res["ticker"] == "TT" and res["engine"] == "claude" and seen["e"] == "claude"
    assert res["generated_at"].endswith("Z") and res["holdings_lots"] == 0
    assert res["notes"] == [{"name": "n.md", "size": len(f"# Rules\n\n{Q}\n".encode())}]
    assert res["facts"]["HOLD.held"] == "False" and all(isinstance(v, str) for v in res["facts"].values())
    r = res["rows"][0]
    assert r["status"] == "conditional" and r["if_held"] and r["advice_verified"] is False and r["heading"] == "Rules"
    assert "quote/fact-ref verified by code" in res["disclaimer"]
    json.dumps(res)


def test_engine_override_and_holdings_included(env, monkeypatch):
    (env / "holdings.json").write_text(json.dumps({"TT": [{"date": "2025-01-02", "price": 10, "shares": 2}]}))
    seen = llm(monkeypatch, [])
    res = runner.run_check("TT", engine="codex")
    assert res["engine"] == "codex" and seen["e"] == "codex"
    assert res["holdings_lots"] == 1 and res["facts"]["HOLD.held"] == "True"
    assert "HOLD.avg_cost = 10.0 price" in seen["p"]


def test_value_errors(env, monkeypatch):
    llm(monkeypatch, [])
    with pytest.raises(ValueError, match="no strategy notes"):
        runner.run_check("TT", notes_dir=env / "empty")
    monkeypatch.setattr(runner.strategy, "rank", lambda q, c: [])
    with pytest.raises(ValueError, match="no relevant notes matched"):
        runner.run_check("TT")


@pytest.mark.parametrize("content,lots,warn", [
    (None, [], False), ("{}", [], False), ('{"TT": []}', [], False),
    ("nope", None, True), ("[1]", None, True), ('{"TT": [{"date": "x", "price": 1}]}', None, True),
    ('{"TT": "s"}', None, True),
])
def test_load_holdings_lots(env, content, lots, warn):
    if content is not None:
        (env / "holdings.json").write_text(content)
    got, w = runner.load_holdings_lots("TT")
    assert got == lots and bool(w) == warn


def test_load_holdings_partial_warning(env):
    good = {"date": "2025-01-02", "price": 10}
    (env / "holdings.json").write_text(json.dumps({"TT": [good, {"date": "x"}]}))
    got, w = runner.load_holdings_lots("TT")
    assert len(got) == 1 and w == ["1 of 2 lots ignored as malformed"]


def test_save_and_load_result_atomic(env):
    assert runner.load_result("TT") is None
    runner.save_result("TT", {"a": 1})
    assert runner.load_result("TT") == {"a": 1}
    assert [p.name for p in (env / "strategy_checks").iterdir()] == ["TT.json"]
    (env / "strategy_checks" / "TT.json").write_text("[1]")
    assert runner.load_result("TT") is None


@pytest.mark.parametrize("t", ["../x", "a/b", "", "..", "x y"])
def test_invalid_ticker_rejected_by_result_store(env, t):
    with pytest.raises(ValueError):
        runner.save_result(t, {"a": 1})
    assert runner.load_result(t) is None
    assert not (env.parent / "x.json").exists()
