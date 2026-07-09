"""Tests for the Forex expansion — Oanda position book + forex flow path."""
from __future__ import annotations

import pytest

from backend.data.providers import asset_class, get_provider
from backend.signals.flow import institutional_flow, _interpret_position_book


# --- provider routing / detection --------------------------------------------

def test_forex_symbols_route_to_oanda():
    from backend.data import oanda, binance
    assert asset_class("EUR_USD") == "forex"
    assert asset_class("EURUSD") == "forex"
    assert asset_class("XAU_USD") == "forex"
    assert asset_class("BTCUSDT") == "crypto"
    assert get_provider("EUR_USD") is oanda
    assert get_provider("BTCUSDT") is binance


def test_forex_universe_present():
    from backend.data import oanda
    assert "EUR_USD" in oanda.FOREX_UNIVERSE
    assert "XAU_USD" in oanda.FOREX_UNIVERSE
    assert len(oanda.FOREX_UNIVERSE) >= 10


# --- position book interpretation --------------------------------------------

def test_interpret_position_book_heavily_long():
    pb = {"available": True, "long_share": 0.72, "ratio": 2.57,
          "long_pct": 72.0, "short_pct": 28.0,
          "longs_underwater_pct": 40.0, "shorts_underwater_pct": 5.0}
    out = _interpret_position_book(pb)
    assert out["available"] is True
    assert out["regime"] == "retail_heavily_long"
    assert out["long_pct"] == 72.0


def test_interpret_position_book_balanced():
    pb = {"available": True, "long_share": 0.50, "ratio": 1.0,
          "long_pct": 50.0, "short_pct": 50.0,
          "longs_underwater_pct": 10.0, "shorts_underwater_pct": 10.0}
    out = _interpret_position_book(pb)
    assert out["regime"] == "retail_balanced"


def test_interpret_position_book_unavailable():
    out = _interpret_position_book({"available": False, "note": "HTTP 401"})
    assert out["available"] is False


# --- forex flow path (fetchers injected, no network/LLM) ---------------------

class _FakeRouter:
    def __init__(self, payload='{"headline":"H","key_points":[],"squeeze_watch":"none"}'):
        self.payload = payload
        self.prompts = []

        class _CL:
            total_usd = 0.004
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.prompts.append((task, prompt))
        return self.payload


def test_forex_flow_uses_position_book():
    book = {"available": True, "long_share": 0.72, "ratio": 2.57,
            "long_pct": 72.0, "short_pct": 28.0,
            "longs_underwater_pct": 45.0, "shorts_underwater_pct": 5.0}
    r = _FakeRouter()
    out = institutional_flow("EUR_USD", fetchers={"book": book}, router=r)
    assert out["available"] is True
    assert out["asset_class"] == "forex"
    assert out["position_book"]["regime"] == "retail_heavily_long"
    # crowded-long + trapped longs -> squeeze-down signal surfaced
    assert out["positioning"]["squeeze_risk"] == "downside (crowded-long unwind)"
    assert any("underwater" in s for s in out["positioning"]["signals"])
    assert out["narrative"]["headline"] == "H"
    # forex path must NOT carry the crypto perp streams
    assert "funding" not in out


def test_forex_flow_degrades_when_book_unavailable():
    r = _FakeRouter()
    out = institutional_flow("GBP_USD",
                             fetchers={"book": {"available": False, "note": "HTTP 401"}},
                             router=r)
    assert out["available"] is False
    assert out["narrative"] is None
    assert "book-scoped" in out["message"]
    assert r.prompts == []          # no LLM call when there's nothing to narrate


def test_forex_flow_no_narrative_flag():
    book = {"available": True, "long_share": 0.4, "ratio": 0.67,
            "long_pct": 40.0, "short_pct": 60.0,
            "longs_underwater_pct": 10.0, "shorts_underwater_pct": 35.0}
    r = _FakeRouter()
    out = institutional_flow("USD_JPY", narrative=False, fetchers={"book": book}, router=r)
    assert out["available"] is True
    assert out["narrative"] is None
    assert out["cost_usd"] == 0.0
    assert r.prompts == []


def test_crypto_flow_still_works_after_forex_branch():
    # Regression: crypto path unchanged by the forex branch.
    def _f(rate, n=5): return {"available": True, "series": [{"time": i, "rate": rate} for i in range(n)]}
    def _oi(vals): return {"available": True, "series": [{"time": i, "oi": v, "oi_value": v} for i, v in enumerate(vals)]}
    def _ls(x, n=5): return {"available": True, "series": [{"time": i, "ratio": x, "long_pct": 0.6, "short_pct": 0.4} for i in range(n)]}
    def _tk(x, n=5): return {"available": True, "series": [{"time": i, "ratio": x, "buy_vol": 1, "sell_vol": 1} for i in range(n)]}
    r = _FakeRouter()
    out = institutional_flow("BTCUSDT",
        fetchers={"funding": _f(0.001), "oi": _oi([1, 2, 3]), "ls": _ls(1.8), "taker": _tk(1.0)},
        router=r)
    assert out["available"] is True
    assert out["funding"]["regime"] == "crowded_long"
    assert "position_book" not in out
