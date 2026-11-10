import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import strategy_check  # noqa: E402


def run(tmp_path, monkeypatch, capsys, content):
    """Run main() --dry-run with stubbed data; returns the captured output."""
    if content is not None:
        (tmp_path / "holdings.json").write_text(content, encoding="utf-8")
    sc = strategy_check
    monkeypatch.setattr(sc, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sc.strategy, "load_notes", lambda: ([strategy_note()], []))
    monkeypatch.setattr(sc.dp, "ohlcv_lists", lambda t: None)
    monkeypatch.setattr(sc.dp, "fundamentals", lambda t: {})
    monkeypatch.setattr(sc.meta_screen, "meta_gate", lambda t: {})
    monkeypatch.setattr(sc.meta_screen, "technical_dossier", lambda t: {})
    monkeypatch.setattr(sc.meta_screen, "technical_reason", lambda m, d: "")
    monkeypatch.setattr(sc.market_health, "market_health", lambda t: {})
    monkeypatch.setattr(sys, "argv", ["x", "tt", "--dry-run"])
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
