"""Tests for the AI Trade Journal behavioral coaching (Phase 2).

Two surfaces:
  * detect_patterns() — deterministic, evidence-backed pattern detection (no LLM).
  * coach()           — wraps detection + LLM coaching (router mocked).
  * GET /journal/coaching — endpoint gating, caching, honest 'not enough data'.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.journal.coaching import (
    MIN_TRADES_FOR_COACHING,
    coach,
    detect_patterns,
)


def _closed(outcome, pnl, symbol="BTCUSDT", conviction=None):
    """Build a minimal closed journal-entry dict as list_entries returns."""
    return {"symbol": symbol, "outcome": outcome, "pnl": pnl,
            "conviction": conviction, "status": "closed"}


# --- deterministic detector --------------------------------------------------

def test_basic_stats_and_win_rate():
    trades = [_closed("win", 100), _closed("win", 200), _closed("loss", -150)]
    prof = detect_patterns(trades)
    s = prof["stats"]
    assert s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate"] == round(2 / 3, 3)
    assert s["avg_win"] == 150.0        # (100+200)/2
    assert s["avg_loss"] == 150.0       # abs(-150)
    assert s["total_pnl"] == 150.0


def test_flag_losers_bigger_than_winners():
    # Small wins, big losses -> payoff ratio < 1.
    trades = [_closed("win", 50), _closed("win", 60), _closed("loss", -300)]
    prof = detect_patterns(trades)
    patterns = {f["pattern"] for f in prof["flags"]}
    assert "losers_bigger_than_winners" in patterns
    assert prof["stats"]["payoff_ratio"] is not None and prof["stats"]["payoff_ratio"] < 1.0


def test_flag_profitable_hit_rate_but_negative_pnl():
    # 60% win rate but net negative because the losses are huge.
    trades = [_closed("win", 20), _closed("win", 20), _closed("win", 20),
              _closed("loss", -200), _closed("loss", -200)]
    prof = detect_patterns(trades)
    patterns = {f["pattern"] for f in prof["flags"]}
    assert "profitable_hit_rate_unprofitable_pnl" in patterns
    assert prof["stats"]["total_pnl"] < 0
    assert prof["stats"]["win_rate"] >= 0.5


def test_flag_extended_losing_streak():
    trades = [_closed("loss", -10), _closed("loss", -10), _closed("loss", -10),
              _closed("loss", -10), _closed("win", 5)]
    prof = detect_patterns(trades)
    patterns = {f["pattern"] for f in prof["flags"]}
    assert "extended_losing_streak" in patterns
    assert prof["stats"]["longest_loss_streak"] == 4


def test_flag_symbol_concentration():
    trades = [_closed("win", 10, "BTCUSDT")] * 4 + [_closed("loss", -10, "ETHUSDT")]
    prof = detect_patterns(trades)
    patterns = {f["pattern"] for f in prof["flags"]}
    assert "symbol_concentration" in patterns
    assert prof["stats"]["top_symbol"] == "BTCUSDT"


def test_flag_conviction_miscalibrated():
    # High-conviction ideas (>=70) do WORSE than the overall book.
    trades = [
        _closed("loss", -50, conviction=85),
        _closed("loss", -50, conviction=80),
        _closed("loss", -50, conviction=90),
        _closed("win", 100, conviction=40),
        _closed("win", 100, conviction=30),
        _closed("win", 100, conviction=20),
    ]
    prof = detect_patterns(trades)
    patterns = {f["pattern"] for f in prof["flags"]}
    assert "conviction_miscalibrated" in patterns
    assert prof["stats"]["high_conviction_win_rate"] == 0.0


def test_clean_record_has_no_flags():
    # Good payoff, positive pnl, no streaks -> no negative patterns.
    trades = [_closed("win", 300, "BTCUSDT"), _closed("win", 300, "ETHUSDT"),
              _closed("loss", -50, "SOLUSDT"), _closed("win", 200, "BNBUSDT"),
              _closed("loss", -40, "XRPUSDT")]
    prof = detect_patterns(trades)
    assert prof["flags"] == []


def test_ignores_non_numeric_pnl():
    trades = [_closed("win", None), _closed("win", "bad"), _closed("loss", -100)]
    prof = detect_patterns(trades)   # must not raise
    assert prof["stats"]["losses"] == 1


# --- coach() (LLM layer mocked) ----------------------------------------------

class _FakeRouter:
    def __init__(self, payload='{"headline":"h","focus_areas":[],"encouragement":"e"}'):
        self.payload = payload
        self.prompts = []

        class _CL:
            total_usd = 0.0123
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.prompts.append((task, prompt, system))
        return self.payload


def test_coach_not_enough_data_makes_no_llm_call():
    trades = [_closed("win", 100), _closed("loss", -50)]   # only 2 decided
    r = _FakeRouter()
    out = coach(trades, router=r)
    assert out["enough_data"] is False
    assert out["coaching"] is None
    assert out["cost_usd"] == 0.0
    assert r.prompts == []                       # LLM never called
    assert out["min_trades"] == MIN_TRADES_FOR_COACHING


def test_coach_calls_llm_when_enough_data():
    trades = [_closed("win", 50), _closed("win", 60), _closed("loss", -300),
              _closed("loss", -200), _closed("win", 40)]
    from backend.ai.router import TaskClass
    r = _FakeRouter()
    out = coach(trades, router=r)
    assert out["enough_data"] is True
    assert out["coaching"]["headline"] == "h"
    assert out["cost_usd"] == 0.0123
    # Routed to the cheap tier, and the detected patterns were passed in the prompt.
    task, prompt, system = r.prompts[0]
    assert task == TaskClass.SIGNAL_SUMMARY
    assert "losers_bigger_than_winners" in prompt


def test_coach_falls_back_on_unparseable_llm_output():
    trades = [_closed("loss", -10), _closed("loss", -10), _closed("loss", -10),
              _closed("loss", -10), _closed("win", 5)]
    r = _FakeRouter(payload="not json at all")
    out = coach(trades, router=r)
    assert out["enough_data"] is True
    assert out["coaching"]["generated"] == "rule-based-fallback"
    # Fallback still surfaces the detected patterns as focus areas.
    labels = {fa["pattern"] for fa in out["coaching"]["focus_areas"]}
    assert "extended_losing_streak" in labels


# --- endpoint ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    """TestClient with a fixed authed user.

    Uses the shared throwaway SQLite DB from conftest's autouse `_clean_db`
    fixture (journal + users tables already created fresh per test).
    """
    from backend.api.main import app, coaching_cache
    from backend.api.auth import current_user_id
    coaching_cache.clear()

    state = {"user": "coach-user"}
    app.dependency_overrides[current_user_id] = lambda: state["user"]
    client = TestClient(app)
    client.set_api_user = lambda u: state.__setitem__("user", u)  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def _seed_closed(api, n_wins, n_losses, win_pnl=50, loss_pnl=-200):
    for _ in range(n_wins):
        r = api.post("/journal", json={"symbol": "BTCUSDT"})
        api.patch(f"/journal/{r.json()['id']}",
                  json={"status": "closed", "outcome": "win", "pnl": win_pnl})
    for _ in range(n_losses):
        r = api.post("/journal", json={"symbol": "BTCUSDT"})
        api.patch(f"/journal/{r.json()['id']}",
                  json={"status": "closed", "outcome": "loss", "pnl": loss_pnl})


def test_coaching_endpoint_not_enough_data(api):
    _seed_closed(api, 1, 1)
    r = api.get("/journal/coaching")
    assert r.status_code == 200
    body = r.json()
    assert body["enough_data"] is False
    assert body["coaching"] is None


def test_coaching_endpoint_full_path_mocked(api, monkeypatch):
    _seed_closed(api, 2, 3)   # 5 decided -> LLM path
    # Mock the router so no live key/network is needed.
    from backend.journal import coaching as coaching_mod
    monkeypatch.setattr(coaching_mod, "AIRouter", lambda: _FakeRouter())

    r = api.get("/journal/coaching")
    assert r.status_code == 200
    body = r.json()
    assert body["enough_data"] is True
    assert body["coaching"]["headline"] == "h"
    assert body["cached"] is False

    # Second call is served from cache (same closed count) — no re-bill.
    r2 = api.get("/journal/coaching")
    assert r2.json()["cached"] is True


def test_coaching_endpoint_gated_for_free_tier(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "free")
    r = api.get("/journal/coaching")
    assert r.status_code == 402
    assert r.json()["upgrade"] is True
