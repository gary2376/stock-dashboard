from __future__ import annotations

from datetime import date, timedelta

import pytest

import meta_screen


def _ohlcv(closes):
    n = len(closes)
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]
    opens = [price - 0.1 for price in closes]
    highs = [price + 1.0 for price in closes]
    lows = [price - 1.0 for price in closes]
    volumes = [1_000.0] * n
    return opens, highs, lows, closes, volumes, dates


def test_analyze_core_steady_uptrend_passes_stage2():
    series = _ohlcv([100.0 + i * 0.5 for i in range(230)])

    result = meta_screen.analyze_core("UP", *series)

    assert result is not None
    assert result["stage2"] is True
    assert result["price"] == series[3][-1]


def test_analyze_core_downtrend_fails_stage2_and_scores_15():
    series = _ohlcv([300.0 - i * 0.5 for i in range(230)])

    result = meta_screen.analyze_core("DOWN", *series)

    assert result == {"t": "DOWN", "price": series[3][-1], "stage2": False}
    assert meta_screen.technical_score(result) == 15


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, 50),
        ({"stage2": True, "edge_count": 0}, 30),
        ({"stage2": True, "edge_count": 1}, 40),
        ({"stage2": True, "edge_count": 2}, 50),
        ({"stage2": True, "edge_count": 3, "abnormal": True}, 55),
        ({"stage2": True, "edge_count": 3, "abnormal": False}, 65),
    ],
)
def test_technical_score_tiers(result, expected):
    assert meta_screen.technical_score(result) == expected


def test_technical_score_final_pass_is_in_top_band():
    assert meta_screen.technical_score({"stage2": True, "edge_count": 3, "final_pass": True}) >= 90
    assert meta_screen.technical_score({"stage2": True, "edge_count": 4, "final_pass": True}) >= 90


def test_technical_score_final_pass_top_band_is_90_to_92():
    assert meta_screen.technical_score({"stage2": True, "edge_count": 3, "final_pass": True}) == 90
    assert meta_screen.technical_score({"stage2": True, "edge_count": 4, "final_pass": True}) == 92


def test_rsi14_rising_and_falling_series():
    assert meta_screen.rsi14(list(range(15))) == 100.0
    assert meta_screen.rsi14(list(range(14, -1, -1))) == 0.0
    assert meta_screen.rsi14([1.0] * 14) is None


def test_rsi14_constant_series_is_neutral():
    assert meta_screen.rsi14([10.0] * 15) == 50.0


def test_resample_weekly_uses_last_close_of_each_iso_week():
    dates = ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-06"]
    closes = [10, 20, 30, 40, 50]

    assert meta_screen._resample_weekly(closes, dates) == [10, 40, 50]
