"""Tests for the Copilot range honesty guarantee.

The 24h range MUST be deterministic and source-accurate — never an LLM-invented
band mislabeled "Kronos". These tests lock that in.
"""
from backend.signals.copilot import _compute_range
from backend.signals.context import MarketContext


def _ctx(indicators=None, kronos_range=None):
    return MarketContext(
        symbol="BTCUSDT",
        interval="1h",
        indicators=indicators or {},
        kronos_range=kronos_range,
    )


def test_range_uses_real_kronos_when_present():
    ctx = _ctx(
        indicators={"last_close": 64000.0, "atr_14": 350.0},
        kronos_range={"low": 63000.0, "high": 65000.0},
    )
    r = _compute_range(ctx)
    assert r["source"] == "Kronos"
    assert r["low"] == 63000.0 and r["high"] == 65000.0


def test_range_falls_back_to_atr_estimate_when_no_kronos():
    ctx = _ctx(indicators={"last_close": 64000.0, "atr_14": 350.0})
    r = _compute_range(ctx)
    assert r["source"] == "ATR estimate"  # NOT "Kronos" — honesty guarantee
    assert r["low"] < 64000.0 < r["high"]


def test_range_unavailable_when_no_indicators():
    ctx = _ctx(indicators={})
    r = _compute_range(ctx)
    assert r["source"] == "unavailable"
    assert r["low"] is None and r["high"] is None


def test_kronos_dict_with_null_bounds_is_not_treated_as_kronos():
    # Graceful-degradation marker (Kronos unavailable in lean build) must not
    # be mistaken for a real forecast.
    ctx = _ctx(
        indicators={"last_close": 64000.0, "atr_14": 350.0},
        kronos_range={"available": False, "note": "Kronos unavailable: no torch"},
    )
    r = _compute_range(ctx)
    assert r["source"] == "ATR estimate"
