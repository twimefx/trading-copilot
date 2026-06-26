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


def test_forex_range_does_not_collapse_to_identical_bounds():
    # Regression: forex (~1.14) rounded to 2dp produced low == high == 1.14,
    # rendering a useless "1.14 – 1.14" band. Precision must scale with price.
    ctx = _ctx(indicators={"last_close": 1.1437, "atr_14": 0.0008})
    r = _compute_range(ctx)
    assert r["source"] == "ATR estimate"
    assert r["low"] != r["high"], "forex band collapsed to a single value"
    assert r["low"] < 1.1437 < r["high"]


def test_price_decimals_scale_with_magnitude():
    from backend.signals.copilot import _price_decimals
    assert _price_decimals(59000.0) == 2   # BTC
    assert _price_decimals(2034.5) == 2     # gold
    assert _price_decimals(1.1437) == 4     # forex major
    assert _price_decimals(0.5) == 5        # sub-dollar alt
    assert _price_decimals(0.0009) == 6     # micro-priced


def test_jpy_and_gold_bands_are_distinct():
    for close, atr in [(157.23, 0.18), (2034.5, 6.2)]:
        r = _compute_range(_ctx(indicators={"last_close": close, "atr_14": atr}))
        assert r["low"] != r["high"]
        assert r["low"] < close < r["high"]
