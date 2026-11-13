import json

import snapshots


def test_snapshot_keeps_scores_drops_bulk_and_is_utc():
    data = {"ticker": "AAA", "scores": {"technical": 50}, "score_reasons": {"technical": "x"},
            "score_caps": [], "verdict": "v", "trade_signal": "t", "news": [{"title": "big"}],
            "market_health": {"a": 1}}
    s = snapshots.snapshot_of(data, generated_at="20260101T000000000000Z", market_date="2025-12-31", engine="claude", price=12.3)
    assert s["schema_version"] == 1 and s["market_date"] == "2025-12-31" and s["price"] == 12.3
    assert "news" not in s and "strategy_check" not in s
    data["strategy_check"] = []
    assert "strategy_check" in snapshots.snapshot_of(data, generated_at="g", market_date=None, engine="e")


def test_save_snapshot_and_run_never_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "HISTORY_DIR", tmp_path)
    for g in ("20260101T000000000001Z", "20260101T000000000002Z"):
        snapshots.save_snapshot({"ticker": "AAA", "generated_at": g})
    assert len(list((tmp_path / "AAA").iterdir())) == 2
    snapshots.save_run("g1", ["AAA", "BBB"], ["AAA"], ["BBB"])
    run = json.loads((tmp_path / "_runs" / "g1.json").read_text())
    assert run["watchlist"] == ["AAA", "BBB"] and run["failed"] == ["BBB"]
