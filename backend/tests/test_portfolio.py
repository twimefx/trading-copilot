"""Tests for the Portfolio Copilot (Phase 2).

  * mark_positions() — direction-aware marking + unrealized P&L (pure, prices injected).
  * assess()         — deterministic exposure/concentration/bias + risk flags.
  * portfolio_copilot() — empty path (no LLM) + LLM path (router mocked).
  * GET /portfolio   — endpoint gating, caching, honest empty book.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.signals.portfolio import assess, mark_positions, portfolio_copilot


def _pos(symbol, direction, entry, size, stop=None, pid="x"):
    return {"id": pid, "symbol": symbol, "direction": direction,
            "entry_price": entry, "size": size, "stop_price": stop, "status": "open"}


# --- marking + unrealized P&L ------------------------------------------------

def test_mark_long_profit_and_notional():
    positions = mark_positions([_pos("BTCUSDT", "long", 60000, 0.5)],
                               {"BTCUSDT": 62000})
    p = positions[0]
    assert p["complete"] is True
    assert p["notional"] == round(0.5 * 62000, 2)
    assert p["unrealized_pnl"] == round((62000 - 60000) * 0.5, 2)   # +1000
    assert p["pct_move"] == round((62000 - 60000) / 60000 * 100, 2)


def test_mark_short_profit_is_direction_aware():
    # Short from 100, price drops to 90 -> profit.
    positions = mark_positions([_pos("ETHUSDT", "short", 100, 10)],
                               {"ETHUSDT": 90})
    p = positions[0]
    assert p["unrealized_pnl"] == round((100 - 90) * 10, 2)         # +100 (short gains)
    assert p["pct_move"] == 10.0


def test_mark_incomplete_position_excluded_from_pnl():
    # Missing size -> incomplete, no P&L, but still gets a notional-free record.
    positions = mark_positions([{"id": "a", "symbol": "SOLUSDT", "direction": "long",
                                 "entry_price": 150, "size": None}],
                               {"SOLUSDT": 160})
    p = positions[0]
    assert p["complete"] is False
    assert p["unrealized_pnl"] is None


def test_mark_missing_price_falls_back_to_entry_for_notional():
    positions = mark_positions([_pos("BTCUSDT", "long", 60000, 1.0)], {"BTCUSDT": None})
    p = positions[0]
    assert p["mark"] is None
    assert p["notional"] == 60000.0        # falls back to entry
    assert p["unrealized_pnl"] is None     # can't mark without a price


# --- deterministic assessment + flags ----------------------------------------

def test_exposure_and_net_bias():
    entries = [_pos("BTCUSDT", "long", 100, 10, pid="1"),   # 1000 long
               _pos("ETHUSDT", "long", 50, 10, pid="2")]    # 500 long
    a = assess(entries, prices={"BTCUSDT": 100, "ETHUSDT": 50})
    prof = a["profile"]
    assert prof["gross_exposure"] == 1500.0
    assert prof["long_exposure"] == 1500.0
    assert prof["short_exposure"] == 0.0
    assert prof["net_exposure"] == 1500.0
    assert prof["net_bias"] == "net_long"


def test_flag_single_name_concentration():
    entries = [_pos("BTCUSDT", "long", 100, 90, pid="1"),   # 9000
               _pos("ETHUSDT", "long", 100, 10, pid="2")]   # 1000
    a = assess(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100})
    risks = {f["risk"] for f in a["flags"]}
    assert "single_name_concentration" in risks
    assert a["profile"]["largest_position"] == "BTCUSDT"


def test_flag_one_directional_and_asset_class():
    # All crypto, all long -> both one_directional_book and asset_class_concentration.
    entries = [_pos("BTCUSDT", "long", 100, 5, pid="1"),
               _pos("ETHUSDT", "long", 100, 5, pid="2")]
    a = assess(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100})
    risks = {f["risk"] for f in a["flags"]}
    assert "one_directional_book" in risks
    assert "asset_class_concentration" in risks


def test_flag_stop_breached_still_open():
    # Long with stop at 95, price now 90 -> stop breached but still open.
    entries = [_pos("BTCUSDT", "long", 100, 1, stop=95, pid="1"),
               _pos("ETHUSDT", "short", 100, 1, stop=105, pid="2")]
    a = assess(entries, prices={"BTCUSDT": 90, "ETHUSDT": 100})
    risks = {f["risk"] for f in a["flags"]}
    assert "stop_breached_still_open" in risks


def test_flag_oversized_position():
    # One position 3x+ the average of the others.
    entries = [_pos("BTCUSDT", "long", 100, 100, pid="1"),   # 10000
               _pos("ETHUSDT", "long", 100, 10, pid="2"),    # 1000
               _pos("SOLUSDT", "long", 100, 10, pid="3")]    # 1000
    a = assess(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100, "SOLUSDT": 100})
    risks = {f["risk"] for f in a["flags"]}
    assert "oversized_position" in risks


def test_balanced_book_diversified_no_directional_flag():
    entries = [_pos("BTCUSDT", "long", 100, 5, pid="1"),     # 500 long
               _pos("ETHUSDT", "short", 100, 5, pid="2")]    # 500 short
    a = assess(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100})
    assert a["profile"]["net_bias"] == "balanced"
    assert "one_directional_book" not in {f["risk"] for f in a["flags"]}


# --- portfolio_copilot (LLM mocked) ------------------------------------------

class _FakeRouter:
    def __init__(self, payload='{"headline":"H","risks":[],"suggestions":[]}'):
        self.payload = payload
        self.prompts = []

        class _CL:
            total_usd = 0.0088
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.prompts.append((task, prompt, system))
        return self.payload


def test_copilot_empty_book_makes_no_llm_call():
    r = _FakeRouter()
    out = portfolio_copilot([], router=r)
    assert out["has_positions"] is False
    assert out["read"] is None
    assert out["cost_usd"] == 0.0
    assert r.prompts == []


def test_copilot_calls_llm_with_flags_in_prompt():
    from backend.ai.router import TaskClass
    entries = [_pos("BTCUSDT", "long", 100, 90, pid="1"),
               _pos("ETHUSDT", "long", 100, 10, pid="2")]
    r = _FakeRouter()
    out = portfolio_copilot(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100}, router=r)
    assert out["has_positions"] is True
    assert out["read"]["headline"] == "H"
    assert out["cost_usd"] == 0.0088
    task, prompt, system = r.prompts[0]
    assert task == TaskClass.SIGNAL_SUMMARY
    assert "single_name_concentration" in prompt


def test_copilot_falls_back_on_unparseable_output():
    entries = [_pos("BTCUSDT", "long", 100, 5, pid="1"),
               _pos("ETHUSDT", "long", 100, 5, pid="2")]
    r = _FakeRouter(payload="totally not json")
    out = portfolio_copilot(entries, prices={"BTCUSDT": 100, "ETHUSDT": 100}, router=r)
    assert out["read"]["generated"] == "rule-based-fallback"
    labels = {x["risk"] for x in out["read"]["risks"]}
    assert "one_directional_book" in labels


# --- endpoint ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    """TestClient with a fixed authed user; shared throwaway DB via conftest."""
    from backend.api.main import app, portfolio_cache
    from backend.api.auth import current_user_id
    portfolio_cache.clear()

    state = {"user": "port-user"}
    app.dependency_overrides[current_user_id] = lambda: state["user"]
    client = TestClient(app)
    client.set_api_user = lambda u: state.__setitem__("user", u)  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def _open_position(api, symbol="BTCUSDT", direction="long", entry=100, size=10):
    r = api.post("/journal", json={"symbol": symbol})
    eid = r.json()["id"]
    api.patch(f"/journal/{eid}", json={
        "status": "open", "direction": direction,
        "entry_price": entry, "size": size,
    })
    return eid


def test_portfolio_endpoint_empty_book(api):
    r = api.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["has_positions"] is False
    assert body["read"] is None


def test_portfolio_endpoint_full_path_mocked(api, monkeypatch):
    _open_position(api, "BTCUSDT", "long", 100, 90)
    _open_position(api, "ETHUSDT", "long", 100, 10)

    # Mock the router AND live prices so no network/keys are needed.
    from backend.signals import portfolio as pf
    monkeypatch.setattr(pf, "AIRouter", lambda: _FakeRouter())
    monkeypatch.setattr(pf, "live_price", lambda sym, interval="1h": 100.0)

    r = api.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["has_positions"] is True
    assert body["read"]["headline"] == "H"
    assert body["cached"] is False
    # concentration flag surfaced from real deterministic math
    assert "single_name_concentration" in {f["risk"] for f in body["flags"]}

    # Second call served from cache.
    r2 = api.get("/portfolio")
    assert r2.json()["cached"] is True


def test_portfolio_endpoint_gated_for_free_tier(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "free")
    r = api.get("/portfolio")
    assert r.status_code == 402
    assert r.json()["upgrade"] is True
