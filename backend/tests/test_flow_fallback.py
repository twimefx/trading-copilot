"""Tests for QA fixes: Bybit futures fallback + UnknownSymbolError handling."""
from __future__ import annotations

import pytest

from backend.data import binance, bybit
from backend.data.errors import UnknownSymbolError


# --- #2: bad symbol -> UnknownSymbolError (friendly 422, not raw 500) --------

class _HTTPErr(Exception):
    def __init__(self, code):
        self.code = code


def test_binance_klines_bad_symbol_raises_unknown(monkeypatch):
    import urllib.error

    def boom(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(binance.urllib.request, "urlopen", boom)
    with pytest.raises(UnknownSymbolError) as ei:
        binance.fetch_klines("NOTACOIN", "1h", 5)
    assert "NOTACOIN" in str(ei.value)


def test_binance_klines_non_400_reraises(monkeypatch):
    import urllib.error

    def boom(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr(binance.urllib.request, "urlopen", boom)
    # A 500 is NOT an unknown symbol — must propagate, not masquerade as UnknownSymbol.
    with pytest.raises(urllib.error.HTTPError):
        binance.fetch_klines("BTCUSDT", "1h", 5)


def test_oanda_klines_bad_instrument_raises_unknown(monkeypatch):
    import urllib.error
    from backend.data import oanda

    monkeypatch.setenv("OANDA_API_TOKEN", "dummy")

    def boom(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(oanda.urllib.request, "urlopen", boom)
    with pytest.raises(UnknownSymbolError):
        oanda.fetch_klines("EUR_ZZZ", "1h", 5)


# --- #1: Bybit fallback when Binance futures unavailable ---------------------

def _binance_futures_dead(monkeypatch):
    """Make every Binance futures HTTP call fail (simulate 451 geo-block)."""
    def boom(url):
        raise RuntimeError("451 geo-blocked")
    monkeypatch.setattr(binance, "_get_json", boom)


def test_funding_history_falls_back_to_bybit(monkeypatch):
    _binance_futures_dead(monkeypatch)
    monkeypatch.setattr(bybit, "fetch_funding_history",
                        lambda s, l=30: {"series": [{"time": 1, "rate": 0.0001}],
                                         "available": True, "source": "bybit"})
    out = binance.fetch_funding_history("BTCUSDT", 5)
    assert out["available"] is True
    assert out["source"] == "bybit"


def test_oi_history_falls_back_to_bybit(monkeypatch):
    _binance_futures_dead(monkeypatch)
    monkeypatch.setattr(bybit, "fetch_oi_history",
                        lambda s, p="1h", l=30: {"series": [{"time": 1, "oi": 5.0, "oi_value": 5.0}],
                                                 "available": True, "source": "bybit"})
    out = binance.fetch_oi_history("BTCUSDT", "1h", 5)
    assert out["available"] is True and out["source"] == "bybit"


def test_long_short_falls_back_to_bybit(monkeypatch):
    _binance_futures_dead(monkeypatch)
    monkeypatch.setattr(bybit, "fetch_long_short_ratio",
                        lambda s, p="1h", l=30: {"series": [{"time": 1, "ratio": 1.4,
                                                             "long_pct": 0.58, "short_pct": 0.42}],
                                                 "available": True, "source": "bybit"})
    out = binance.fetch_long_short_ratio("BTCUSDT", "1h", 5)
    assert out["available"] is True and out["source"] == "bybit"


def test_binance_result_wins_when_available(monkeypatch):
    # When Binance succeeds, we must NOT call Bybit (Binance stays primary).
    monkeypatch.setattr(binance, "_get_json",
                        lambda url: [{"fundingTime": 1, "fundingRate": "0.0001"}])
    called = {"bybit": False}

    def spy(*a, **k):
        called["bybit"] = True
        return {"series": [], "available": True, "source": "bybit"}

    monkeypatch.setattr(bybit, "fetch_funding_history", spy)
    out = binance.fetch_funding_history("BTCUSDT", 5)
    assert out["available"] is True
    assert out.get("source") != "bybit"      # came from Binance
    assert called["bybit"] is False           # Bybit never consulted


def test_fallback_disabled_by_env(monkeypatch):
    monkeypatch.setattr(binance, "_FUTURES_FALLBACK", False)
    _binance_futures_dead(monkeypatch)
    spy = {"called": False}
    monkeypatch.setattr(bybit, "fetch_funding_history",
                        lambda *a, **k: spy.__setitem__("called", True) or {"available": True})
    out = binance.fetch_funding_history("BTCUSDT", 5)
    assert out["available"] is False          # no fallback
    assert spy["called"] is False


def test_bybit_parses_real_shapes():
    # Guard the Bybit response parsing against shape drift (unit test, no network).
    # funding history newest-first -> oldest-first
    import backend.data.bybit as bb

    def fake(url):
        if "funding/history" in url:
            return {"retCode": 0, "result": {"list": [
                {"fundingRateTimestamp": "2000", "fundingRate": "0.0002"},
                {"fundingRateTimestamp": "1000", "fundingRate": "0.0001"}]}}
        return {"retCode": 0, "result": {"list": []}}

    import urllib.request
    orig = bb._get_json
    bb._get_json = fake
    try:
        out = bb.fetch_funding_history("BTCUSDT", 2)
        assert out["available"] is True
        assert out["series"][0]["time"] == 1000   # reversed to oldest-first
        assert out["series"][1]["time"] == 2000
    finally:
        bb._get_json = orig
