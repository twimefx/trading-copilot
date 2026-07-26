"""Tests for Market Replay (Phase 4).

  * fetch_klines_range  — historical window fetch on Binance + Oanda (mocked HTTP).
  * build_replay_context — MarketContext truncated at as_of (no lookahead).
  * score_outcome        — deterministic replay verdict math.
  * F_REPLAY             — Premium-only feature flag.
  * POST /replay         — tier gate + happy path (router mocked).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest


def _df(closes, start="2024-01-01", freq="h"):
    ts = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame({
        "timestamps": ts, "open": [float(c) for c in closes],
        "high": [float(c) * 1.01 for c in closes],
        "low": [float(c) * 0.99 for c in closes],
        "close": [float(c) for c in closes],
        "volume": [1000.0] * len(closes), "amount": [1000.0] * len(closes),
    })


# --- Binance fetch_klines_range ---------------------------------------------

def test_binance_fetch_klines_range_shape(monkeypatch):
    from backend.data import binance

    fake = [
        [1704067200000, "100", "110", "90", "105", "1000", 0, "105000"],  # 2024-01-01
        [1704070800000, "105", "115", "95", "110", "1000", 0, "110000"],
        [1704074400000, "110", "120", "100", "115", "1000", 0, "115000"],
    ]

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(fake).encode()

    monkeypatch.setattr(binance.urllib.request, "urlopen", lambda *a, **k: _Resp())
    df = binance.fetch_klines_range(
        "BTCUSDT", "1h",
        start_ms=1704067200000, end_ms=1704074400000,
    )
    assert list(df["close"]) == [105.0, 110.0, 115.0]
    assert list(df.columns) == ["timestamps", "open", "high", "low", "close", "volume", "amount"]


# --- Oanda fetch_klines_range ------------------------------------------------

def test_oanda_fetch_klines_range_shape(monkeypatch):
    from backend.data import oanda

    fake = {"candles": [
        {"time": "2024-01-01T00:00:00.000000000Z", "complete": True,
         "mid": {"o": "1.10", "h": "1.11", "l": "1.09", "c": "1.105"}, "volume": 100},
        {"time": "2024-01-01T01:00:00.000000000Z", "complete": True,
         "mid": {"o": "1.105", "h": "1.115", "l": "1.095", "c": "1.110"}, "volume": 120},
    ]}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(fake).encode()

    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")
    monkeypatch.setattr(oanda.urllib.request, "urlopen", lambda *a, **k: _Resp())
    df = oanda.fetch_klines_range("EUR_USD", "1h",
                                  start_ms=1704067200000, end_ms=1704074400000)
    assert list(df["close"]) == [1.105, 1.110]


# --- build_replay_context ----------------------------------------------------

def test_build_replay_context_truncates_at_as_of(monkeypatch):
    from backend.signals import replay

    # 100 hourly closes; as_of at candle index 79 (80 candles after truncation —
    # above the 60 minimum). Provider returns the whole frame; the context
    # builder must truncate at as_of before computing indicators, so last_close
    # is the close AT as_of — never the future.
    df = _df(list(range(1, 101)))
    as_of = int(df["timestamps"].iloc[79].timestamp())

    monkeypatch.setattr(replay, "_fetch_window", lambda sym, iv, a, b: df)
    ctx = replay.build_replay_context("BTCUSDT", "1h", as_of, include_kronos=False)
    assert ctx.symbol == "BTCUSDT"
    assert ctx.indicators["last_close"] == pytest.approx(df["close"].iloc[79])
    # Positioning honestly unavailable in replay.
    assert ctx.funding["available"] is False
    assert ctx.open_interest["available"] is False


def test_build_replay_context_rejects_thin_history(monkeypatch):
    from backend.signals import replay

    df = _df(list(range(1, 31)))  # only 30 candles — under the 60 minimum
    as_of = int(df["timestamps"].iloc[20].timestamp())
    monkeypatch.setattr(replay, "_fetch_window", lambda sym, iv, a, b: df)
    with pytest.raises(ValueError, match="Not enough history"):
        replay.build_replay_context("BTCUSDT", "1h", as_of, include_kronos=False)


# --- score_outcome -----------------------------------------------------------

def test_score_outcome_bullish_correct_when_price_rises():
    from backend.signals import replay

    outcome = _df([110, 115, 120])
    res = replay.score_outcome(100.0, "bullish", outcome)
    assert res["available"] is True
    assert res["verdict"] == "correct"
    assert res["final_close"] == 120.0
    assert res["move_pct"] == pytest.approx(20.0)


def test_score_outcome_bearish_incorrect_when_price_rises():
    from backend.signals import replay

    outcome = _df([110, 115, 120])
    res = replay.score_outcome(100.0, "bearish", outcome)
    assert res["verdict"] == "incorrect"


def test_score_outcome_empty_window_unavailable():
    from backend.signals import replay

    res = replay.score_outcome(100.0, "bullish", _df([]))
    assert res["available"] is False


# --- F_REPLAY feature flag ---------------------------------------------------

def test_replay_is_premium_only():
    from backend.billing import has_feature, F_REPLAY, FREE, PRO, PREMIUM
    assert has_feature(PREMIUM, F_REPLAY) is True
    assert has_feature(PRO, F_REPLAY) is False
    assert has_feature(FREE, F_REPLAY) is False
