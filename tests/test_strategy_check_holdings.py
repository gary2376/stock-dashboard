import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import strategy_check  # noqa: E402


def run(tmp_path, monkeypatch, capsys, content, llm=None):
    """Run main() --dry-run with stubbed data; returns the captured output."""
    if content is not None:
        (tmp_path / "holdings.json").write_text(content, encoding="utf-8")
    sc = strategy_check
    monkeypatch.setattr(sc.runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sc.strategy, "load_notes", lambda d=None: ([strategy_note()], []))
    monkeypatch.setattr(sc.runner.dp, "ohlcv_lists", lambda t: None)
    monkeypatch.setattr(sc.runner.dp, "fundamentals", lambda t: {})
    monkeypatch.setattr(sc.runner.meta_screen, "meta_gate", lambda t: {})
    monkeypatch.setattr(sc.runner.meta_screen, "technical_dossier", lambda t: {})
    monkeypatch.setattr(sc.runner.meta_screen, "technical_reason", lambda m, d: "")
    monkeypatch.setattr(sc.runner.market_health, "market_health", lambda t: {})
    if llm is not None:
        monkeypatch.setattr(sc.runner.ga, "run_llm", lambda prompt, engine, tag="": llm)
        monkeypatch.setattr(sc.runner.ga, "pick_engine", lambda prefer=None: "claude")
    monkeypatch.setattr(sys, "argv", ["x", "tt"] + ([] if llm is not None else ["--dry-run"]))
    sc.main()
    return capsys.readouterr().out


def strategy_note():
    return sc_note("# R\n\nDo not average down on a losing position at any time.\n")


def sc_note(t):
    return strategy_check.strategy.Note("n.md", t)


OMIT = "position facts omitted"
GOOD = {"date": "2025-01-02", "price": 10}


@pytest.mark.parametrize("content,expect_held,expect_omit,expect_warn", [
    (None, "HOLD.held = False", False, None),                                   # missing file
    ("{}", "HOLD.held = False", False, None),                                   # missing ticker
    ('{"TT": []}', "HOLD.held = False", False, None),                           # empty lots
    ("not json", None, True, None),                                             # malformed
    ("[1]", None, True, None),                                                  # bad top level
    (json.dumps({"TT": [{"date": "x", "price": 1}]}), None, True, None),        # all lots invalid
    (json.dumps({"TT": [GOOD, {"date": "x"}]}), "HOLD.held = True", False, "1 of 2 lots ignored"),
])
def test_holdings_loading(tmp_path, monkeypatch, capsys, content, expect_held, expect_omit, expect_warn):
    out = run(tmp_path, monkeypatch, capsys, content)
    assert (OMIT in out) == expect_omit
    if expect_held:
        assert expect_held in out
    else:
        assert "HOLD.held =" not in out
    if expect_warn:
        assert expect_warn in out


def test_scenario_advice_section_printed(tmp_path, monkeypatch, capsys):
    q = "Do not average down on a losing position at any time."
    rows = [{"rule": "不攤平", "source": "S1", "quote": q, "status": "conditional", "fact_refs": ["HOLD.held"],
             "if_held": "已持有就不要加碼", "if_not_held": "沒持有就等回測"}]
    out = run(tmp_path, monkeypatch, capsys, None, llm=json.dumps(rows))
    assert "情境建議（模型建議，未經程式驗證）" in out
    assert "如果已持有: 已持有就不要加碼" in out and "如果沒持有: 沒持有就等回測" in out
    assert "不攤平 | conditional" in out and "this does NOT prove" in out


def test_cli_sanitises_and_hides_not_held_line_for_holders(tmp_path, monkeypatch, capsys):
    q = "Do not average down on a losing position at any time."
    rows = [{"rule": "不\x1b[31m攤平\x1b[0m\n偽造", "source": "S1", "quote": q, "status": "fail",
             "fact_refs": ["HOLD.avg_down"], "if_held": "已持有就不要加碼\x1b[2J", "if_not_held": "沒持有就等"}]
    held = json.dumps({"TT": [GOOD, {"date": "2025-01-03", "price": 5}]})
    out = run(tmp_path, monkeypatch, capsys, held, llm=json.dumps(rows))
    assert "\x1b" not in out
    assert "不攤平 偽造 | fail" in out
    assert "如果已持有: 已持有就不要加碼" in out and "如果沒持有" not in out
