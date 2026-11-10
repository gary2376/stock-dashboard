import json

import pytest

import analysis_profile as ap
import generate_analysis as ga


def _ohlcv(n=260):
    closes = [100 + (i % 40) for i in range(n)]
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    return closes, closes, closes, closes, [1] * n, dates


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ga, "DATA_DIR", tmp_path)
    return tmp_path


def test_not_held_when_ticker_missing_or_no_file(data_dir):
    assert "HOLD.held=False" in ga._position_text("AAA", _ohlcv(), 120)
    (data_dir / "holdings.json").write_text(json.dumps({"BBB": [{"date": "2025-03-03", "price": 10, "shares": 1}]}))
    assert "HOLD.held=False" in ga._position_text("AAA", _ohlcv(), 120)


def test_held_block_has_derived_facts_but_no_raw_lots(data_dir):
    o = _ohlcv()
    d1, d2 = o[5][230], o[5][250]
    (data_dir / "holdings.json").write_text(json.dumps({"AAA": [
        {"id": "secret-id", "date": d1, "price": 130, "shares": 7},
        {"id": "x", "date": d2, "price": 110, "shares": 3}]}))
    text = ga._position_text("AAA", o, 120)
    assert "HOLD.held=True" in text and "HOLD.avg_down=True" in text
    assert "secret-id" not in text and "shares\": 7" not in text


def test_unreadable_holdings_file_is_unavailable_not_not_held(data_dir):
    (data_dir / "holdings.json").write_text("{broken")
    text = ga._position_text("AAA", _ohlcv(), 120)
    assert "無法讀取" in text and "HOLD.held" not in text


def test_profile_can_turn_position_context_off(data_dir, tmp_path, monkeypatch):
    (data_dir / "holdings.json").write_text(json.dumps({"AAA": [{"date": "2025-03-03", "price": 10}]}))
    p = tmp_path / "prof"
    p.mkdir()
    (p / "profile.json").write_text(json.dumps({"include_position": 0}))
    monkeypatch.setenv("STOCK_DASHBOARD_PROFILE", str(p))
    ap._load.cache_clear()
    try:
        text = ga._position_text("AAA", _ohlcv(), 120)
    finally:
        ap._load.cache_clear()
    assert "關閉" in text and "HOLD" not in text


def test_schema_and_prompt_say_position_does_not_touch_scores():
    assert "position_note" in ga.SCHEMA_HINT and "must NOT change any score" in ga.SCHEMA_HINT
    assert "position_note" not in ga.REQUIRED_KEYS  # optional: old/failed runs stay valid
