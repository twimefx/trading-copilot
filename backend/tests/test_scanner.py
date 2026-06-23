"""Tests for the scan engine scoring (no network — tests pure scoring logic)."""
from backend.signals.scanner import _score_from_indicators


def test_strong_bullish():
    ind = {"last_close": 110, "ema_20": 100, "ema_50": 95, "sma_200": 90,
           "macd_hist": 5.0, "rsi_14": 55}
    lean, conv, _ = _score_from_indicators(ind)
    assert lean == "bullish"
    assert conv >= 60


def test_strong_bearish():
    ind = {"last_close": 90, "ema_20": 100, "ema_50": 105, "sma_200": 110,
           "macd_hist": -5.0, "rsi_14": 45}
    lean, conv, _ = _score_from_indicators(ind)
    assert lean == "bearish"
    assert conv >= 60


def test_neutral_mixed():
    ind = {"last_close": 100, "ema_20": 100, "ema_50": 100, "sma_200": 100,
           "macd_hist": 0.0, "rsi_14": 50}
    lean, conv, _ = _score_from_indicators(ind)
    assert lean == "neutral"


def test_overbought_caps_bullish():
    ind = {"last_close": 110, "ema_20": 100, "ema_50": 95, "sma_200": 90,
           "macd_hist": 5.0, "rsi_14": 80}
    lean, conv, reasons = _score_from_indicators(ind)
    assert any("overbought" in r for r in reasons)
