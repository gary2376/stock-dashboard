import json
import re

import pytest

import snapshots


def test_utc_stamp_format():
    assert re.fullmatch(r"\d{8}T\d{12}Z", snapshots.utc_stamp())


def test_snapshot_keeps_scores_drops_bulk():
    data = {"ticker": "AAA", "scores": {"technical": 50}, "score_reasons": {"technical": "x"},
            "score_caps": [], "verdict": "v", "trade_signal": "t", "news": [{"title": "big"}],
            "market_health": {"a": 1}}
    s = snapshots.snapshot_of(data, generated_at="g", market_date="2025-12-31", engine="claude",
                              price=12.3, run_id="r1")
    assert s["schema_version"] == 1 and s["market_date"] == "2025-12-31" and s["price"] == 12.3 and s["run_id"] == "r1"
    assert "news" not in s and "strategy_check" not in s
    data["strategy_check"] = []
    assert "strategy_check" in snapshots.snapshot_of(data, generated_at="g", market_date=None, engine="e")


def test_same_name_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "HISTORY_DIR", tmp_path)
    snapshots.save_snapshot({"ticker": "AAA", "generated_at": "g1", "v": 1})
    with pytest.raises(FileExistsError):
        snapshots.save_snapshot({"ticker": "AAA", "generated_at": "g1", "v": 2})
    assert json.loads((tmp_path / "AAA" / "g1.json").read_text())["v"] == 1
    assert [f.name for f in (tmp_path / "AAA").iterdir()] == ["g1.json"]  # no temp leftovers


def test_run_manifest_records_requested_ok_failed_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "HISTORY_DIR", tmp_path)
    snapshots.save_run("r1", ["AAA", "BBB", "CCC"], ["AAA"], ["BBB"], ["CCC"], explicit=True)
    run = json.loads((tmp_path / "_runs" / "r1.json").read_text())
    assert run["requested"] == ["AAA", "BBB", "CCC"] and run["skipped"] == ["CCC"] and run["explicit_tickers"] is True
