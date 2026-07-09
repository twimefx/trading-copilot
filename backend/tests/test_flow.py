"""Tests for the Institutional Flow Dashboard (Phase 3).

  * institutional_flow() deterministic interpretation with injected fetchers.
  * graceful degradation when derivatives data is unavailable (geo-block).
  * forex symbols -> not applicable, clean.
  * GET /flow endpoint gating + cache.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.signals.flow import institutional_flow


def _funding(rate, n=10):
    return {"available": True, "series": [{"time": i, "rate": rate} for i in range(n)]}


def _oi(vals):
    return {"available": True, "series": [{"time": i, "oi": v, "oi_value": v * 100}
                                          for i, v in enumerate(vals)]}


def _ls(ratio, n=10):
    long_pct = ratio / (1 + ratio)
    return {"available": True, "series": [
        {"time": i, "ratio": ratio, "long_pct": long_pct, "short_pct": 1 - long_pct}
        for i in range(n)]}


def _taker(ratio, n=10):
    return {"available": True, "series": [{"time": i, "ratio": ratio,
                                           "buy_vol": 100 * ratio, "sell_vol": 100}
                                          for i in range(n)]}


class _FakeRouter:
    def __init__(self, payload='{"headline":"H","key_points":[],"squeeze_watch":"none"}'):
        self.payload = payload
        self.prompts = []

        class _CL:
            total_usd = 0.006
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.prompts.append((task, prompt))
        return self.payload


# --- deterministic interpretation --------------------------------------------

def test_crowded_long_with_rising_oi_flags_squeeze():
    fetchers = {
        "funding": _funding(0.001),          # positive => crowded long
        "oi": _oi([100, 110, 120, 130]),      # rising
        "ls": _ls(1.8),                       # retail heavily long
        "taker": _taker(1.0),
    }
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=_FakeRouter())
    assert out["available"] is True
    assert out["funding"]["regime"] == "crowded_long"
    assert out["open_interest"]["trend"] == "rising"
    assert out["long_short"]["regime"] == "retail_heavily_long"
    # positioning summary should call out long-squeeze vulnerability
    assert out["positioning"]["squeeze_risk"] == "downside (long squeeze)"
    assert any("squeeze" in s.lower() for s in out["positioning"]["signals"])


def test_crowded_short_with_rising_oi_flags_short_squeeze():
    fetchers = {
        "funding": _funding(-0.001),
        "oi": _oi([100, 120, 140]),
        "ls": _ls(0.5),
        "taker": _taker(1.1),
    }
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=_FakeRouter())
    assert out["funding"]["regime"] == "crowded_short"
    assert out["long_short"]["regime"] == "retail_heavily_short"
    assert out["positioning"]["squeeze_risk"] == "upside (short squeeze)"


def test_taker_flow_direction():
    fetchers = {"funding": _funding(0.0), "oi": _oi([100, 100]),
                "ls": _ls(1.0), "taker": _taker(1.2)}
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=_FakeRouter())
    assert out["taker_flow"]["flow"] == "buyers_aggressive"


def test_oi_unwind_detected():
    fetchers = {"funding": _funding(0.0), "oi": _oi([200, 150, 100]),
                "ls": _ls(1.0), "taker": _taker(1.0)}
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=_FakeRouter())
    assert out["open_interest"]["trend"] == "falling"
    assert out["open_interest"]["change_pct"] < 0


def test_narrative_routes_to_cheap_tier_and_grounds():
    from backend.ai.router import TaskClass
    fetchers = {"funding": _funding(0.001), "oi": _oi([100, 130]),
                "ls": _ls(1.8), "taker": _taker(0.9)}
    r = _FakeRouter()
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=r)
    assert out["narrative"]["headline"] == "H"
    assert out["cost_usd"] == 0.006
    assert r.prompts and r.prompts[0][0] == TaskClass.SIGNAL_SUMMARY


def test_narrative_disabled_makes_no_llm_call():
    r = _FakeRouter()
    fetchers = {"funding": _funding(0.0), "oi": _oi([100, 100]),
                "ls": _ls(1.0), "taker": _taker(1.0)}
    out = institutional_flow("BTCUSDT", narrative=False, fetchers=fetchers, router=r)
    assert out["narrative"] is None
    assert out["cost_usd"] == 0.0
    assert r.prompts == []


# --- graceful degradation ----------------------------------------------------

def test_all_streams_unavailable_degrades_cleanly():
    unavailable = {"available": False, "series": []}
    fetchers = {"funding": unavailable, "oi": unavailable,
                "ls": unavailable, "taker": unavailable}
    r = _FakeRouter()
    out = institutional_flow("BTCUSDT", fetchers=fetchers, router=r)
    assert out["available"] is False
    assert out["narrative"] is None
    assert "unavailable" in out["message"].lower()
    assert r.prompts == []          # no LLM call when there's nothing to narrate


def test_forex_symbol_not_applicable():
    out = institutional_flow("EUR_USD", router=_FakeRouter())
    assert out["available"] is False
    assert "crypto-only" in out["message"]


# --- endpoint ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    from backend.api.main import app, flow_cache
    from backend.api.auth import current_user_id
    flow_cache.clear()
    state = {"user": "flow-user"}
    app.dependency_overrides[current_user_id] = lambda: state["user"]
    client = TestClient(app)
    client.set_api_user = lambda u: state.__setitem__("user", u)  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_flow_endpoint_gated_below_premium(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "pro")   # pro lacks F_FLOW
    r = api.get("/flow?symbol=BTCUSDT")
    assert r.status_code == 402
    assert r.json()["upgrade"] is True


def test_flow_endpoint_full_path_mocked(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "premium")

    import backend.signals.flow as flow_mod
    fake = {"symbol": "BTCUSDT", "period": "1h", "available": True,
            "funding": {"available": True, "regime": "crowded_long"},
            "open_interest": {"available": True, "trend": "rising"},
            "long_short": {"available": True, "regime": "retail_long"},
            "taker_flow": {"available": True, "flow": "balanced"},
            "positioning": {"signals": [], "squeeze_risk": None},
            "series": {}, "narrative": {"headline": "h"},
            "disclaimer": "nfa", "cost_usd": 0.006}
    monkeypatch.setattr(flow_mod, "institutional_flow", lambda *a, **k: fake)

    r = api.get("/flow?symbol=BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["cached"] is False

    r2 = api.get("/flow?symbol=BTCUSDT")
    assert r2.json()["cached"] is True
