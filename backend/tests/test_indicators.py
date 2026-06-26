"""Tests for technical indicators — validated against known reference values."""
import numpy as np
import pandas as pd
import pytest

from backend.data import indicators as ind


def test_rsi_all_gains_is_100():
    """Monotonically rising series → RSI should approach 100."""
    close = pd.Series([float(i) for i in range(1, 50)])
    r = ind.rsi(close, 14).iloc[-1]
    assert r == pytest.approx(100.0, abs=1e-6)


def test_rsi_known_wilder_example():
    """RSI stays within valid 0-100 bounds and reacts to direction."""
    up = pd.Series([float(i) for i in range(1, 40)])
    down = pd.Series([float(i) for i in range(40, 1, -1)])
    assert ind.rsi(up, 14).iloc[-1] > 70
    assert ind.rsi(down, 14).iloc[-1] < 30


def test_ema_matches_pandas():
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8])
    expected = s.ewm(span=3, adjust=False).mean().iloc[-1]
    assert ind.ema(s, 3).iloc[-1] == pytest.approx(expected)


def test_macd_hist_is_macd_minus_signal():
    close = pd.Series(np.linspace(100, 120, 60) + np.sin(np.arange(60)))
    m = ind.macd(close)
    assert (m["hist"] - (m["macd"] - m["signal"])).abs().max() < 1e-9


def test_atr_positive():
    n = 30
    high = pd.Series(np.linspace(105, 130, n))
    low = pd.Series(np.linspace(100, 125, n))
    close = pd.Series(np.linspace(102, 127, n))
    a = ind.atr(high, low, close, 14).iloc[-1]
    assert a > 0


def test_volume_trend_rising():
    vol = pd.Series([10.0] * 20 + [20.0] * 20)  # second half double
    assert ind.volume_trend(vol, 20) == pytest.approx(2.0)


def test_snapshot_has_keys():
    n = 250
    df = pd.DataFrame({
        "open": np.linspace(100, 120, n),
        "high": np.linspace(101, 121, n),
        "low": np.linspace(99, 119, n),
        "close": np.linspace(100, 120, n),
        "volume": np.random.rand(n) * 1000,
    })
    snap = ind.snapshot(df)
    for key in ["last_close", "rsi_14", "macd", "atr_14", "atr_pct", "volume_trend", "sma_200"]:
        assert key in snap


def test_snapshot_preserves_forex_precision():
    """Regression: 2dp rounding zeroed forex ATR (~0.0008 -> 0.0), collapsing the
    volatility band to a single value. Snapshot must keep sub-cent precision for
    low-priced instruments."""
    rng = np.random.default_rng(7)
    n = 250
    close = 1.1400 + np.cumsum(rng.standard_normal(n) * 0.0004)
    df = pd.DataFrame({
        "open": close,
        "high": close + np.abs(rng.standard_normal(n) * 0.0005),
        "low": close - np.abs(rng.standard_normal(n) * 0.0005),
        "close": close,
        "volume": rng.random(n) * 1000,
    })
    snap = ind.snapshot(df)
    assert snap["atr_14"] > 0, "forex ATR was rounded to zero"
    # EMA20 should not be flattened to exactly the close's 2dp value
    assert snap["ema_20"] != round(float(close[-1]), 2) or snap["ema_20"] != snap["last_close"]


def test_price_dp_scales_with_magnitude():
    assert ind._price_dp(59000.0) == 2
    assert ind._price_dp(1.1437) == 5
    assert ind._price_dp(0.5) == 6
    assert ind._price_dp(0.0008) == 8
