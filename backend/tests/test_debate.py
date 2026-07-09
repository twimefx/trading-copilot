"""Tests for the Multi-Agent Debate Engine (Phase 3).

  * tally_votes()  — deterministic consensus math (aligned, split, contrarian weight).
  * run_panel()    — panel orchestration with the router mocked (5 agents).
  * debate()       — full orchestrator (panel -> tally -> judge), router mocked.
  * POST /debate   — Premium gating + cache.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.signals.debate import debate, tally_votes


def _card(agent, lean, conviction, ok=True):
    return {"agent": agent, "name": agent.title(), "lean": lean,
            "conviction": conviction, "rationale": "r", "key_evidence": ["e"], "ok": ok}


# --- deterministic tally -----------------------------------------------------

def test_tally_aligned_bullish_high_confidence():
    cards = [_card("trend", "bullish", 80), _card("momentum", "bullish", 75),
             _card("positioning", "bullish", 70), _card("volatility", "neutral", 40),
             _card("contrarian", "bearish", 30)]
    t = tally_votes(cards)
    assert t["direction"] == "bullish"
    assert t["divided"] is False
    assert t["confidence"] >= 50
    assert t["counts"]["bullish"] == 3


def test_tally_split_panel_is_divided_and_low_confidence():
    cards = [_card("trend", "bullish", 85), _card("momentum", "bullish", 80),
             _card("positioning", "bearish", 85), _card("volatility", "bearish", 80),
             _card("contrarian", "neutral", 50)]
    t = tally_votes(cards)
    assert t["divided"] is True
    assert t["confidence"] <= 45          # split panel is capped
    assert t["counts"]["bullish"] == 2 and t["counts"]["bearish"] == 2


def test_tally_contrarian_weighted_low():
    # 3 mild-bull directional agents vs a max-conviction contrarian bear.
    # Contrarian is weighted 0.4, so it should NOT flip the consensus.
    cards = [_card("trend", "bullish", 60), _card("momentum", "bullish", 60),
             _card("positioning", "bullish", 60), _card("volatility", "bullish", 60),
             _card("contrarian", "bearish", 100)]
    t = tally_votes(cards)
    assert t["direction"] == "bullish"


def test_tally_all_unusable_returns_neutral_zero():
    cards = [_card("trend", "bullish", 80, ok=False)]
    t = tally_votes(cards)
    assert t["direction"] == "neutral"
    assert t["confidence"] == 0
    assert t["divided"] is True


def test_tally_neutral_when_balanced_low_score():
    cards = [_card("trend", "neutral", 50), _card("momentum", "neutral", 40),
             _card("positioning", "neutral", 30), _card("volatility", "neutral", 20),
             _card("contrarian", "neutral", 10)]
    t = tally_votes(cards)
    assert t["direction"] == "neutral"


# --- panel + debate orchestration (router mocked) ----------------------------

class _ScriptedRouter:
    """Returns a JSON card per agent (by matching the system prompt persona),
    and a judge verdict for the AGENT_CONSENSUS task."""
    def __init__(self, agent_leans: dict[str, str]):
        self.agent_leans = agent_leans
        self.calls = []

        class _CL:
            total_usd = 0.0
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        from backend.ai.router import TaskClass
        self.calls.append(task)
        if task == TaskClass.AGENT_CONSENSUS:
            return json.dumps({
                "synthesis": "The desk leans per the tally.",
                "dissent": "contrarian pushed back",
                "what_would_change_our_mind": "a break of the range",
            })
        # Identify which agent by a keyword in its system prompt.
        sys = system or ""
        if "trend and market-structure" in sys:
            lean = self.agent_leans.get("trend", "neutral")
        elif "momentum and exhaustion" in sys:
            lean = self.agent_leans.get("momentum", "neutral")
        elif "crowd-positioning" in sys:
            lean = self.agent_leans.get("positioning", "neutral")
        elif "volatility and range" in sys:
            lean = self.agent_leans.get("volatility", "neutral")
        elif "pure price-action" in sys:
            lean = self.agent_leans.get("price_action", "neutral")
        elif "classical chart-pattern" in sys:
            lean = self.agent_leans.get("chart_pattern", "neutral")
        elif "devil's advocate" in sys:
            lean = self.agent_leans.get("contrarian", "neutral")
        else:
            lean = "neutral"
        return json.dumps({"lean": lean, "conviction": 70,
                           "rationale": "grounded in data", "key_evidence": ["rsi_14"]})


def _ctx():
    from backend.signals.context import MarketContext
    return MarketContext(symbol="BTCUSDT", interval="1h", asset_class="crypto",
                         indicators={"last_close": 60000, "rsi_14": 55},
                         funding={"available": False}, open_interest={"available": False},
                         kronos_range={"available": False})


def test_run_panel_produces_all_cards():
    from backend.signals.agents import run_panel, AGENTS
    r = _ScriptedRouter({"trend": "bullish", "momentum": "bullish",
                         "positioning": "neutral", "volatility": "neutral",
                         "price_action": "bullish", "chart_pattern": "neutral",
                         "contrarian": "bearish"})
    cards = run_panel(_ctx(), router=r)
    assert len(cards) == len(AGENTS) + 1          # all specialists + contrarian
    assert cards[-1]["agent"] == "contrarian"     # contrarian runs last
    keys = {c["agent"] for c in cards}
    assert keys == {"trend", "momentum", "positioning", "volatility",
                    "price_action", "chart_pattern", "contrarian"}


def test_debate_end_to_end_bullish(monkeypatch):
    from backend.ai.router import TaskClass
    from backend.signals.agents import AGENTS
    r = _ScriptedRouter({"trend": "bullish", "momentum": "bullish",
                         "positioning": "bullish", "volatility": "neutral",
                         "price_action": "bullish", "chart_pattern": "bullish",
                         "contrarian": "bearish"})
    out = debate(ctx=_ctx(), router=r)
    assert out["consensus"]["lean"] == "bullish"
    assert len(out["agents"]) == len(AGENTS) + 1
    assert out["synthesis"]
    assert out["disclaimer"]
    # Judge routed to the premium consensus tier; agents to the cheap scan tier.
    assert TaskClass.AGENT_CONSENSUS in r.calls
    assert r.calls.count(TaskClass.MARKET_SCAN) == len(AGENTS) + 1


def test_debate_judge_fallback_on_bad_json(monkeypatch):
    r = _ScriptedRouter({"trend": "bearish", "momentum": "bearish",
                         "positioning": "bearish", "volatility": "bearish",
                         "contrarian": "bullish"})
    # Break only the judge output.
    orig = r.complete
    def broken(task, prompt, *, system=None, max_tokens=1024):
        from backend.ai.router import TaskClass
        if task == TaskClass.AGENT_CONSENSUS:
            return "not json"
        return orig(task, prompt, system=system, max_tokens=max_tokens)
    r.complete = broken  # type: ignore[assignment]
    out = debate(ctx=_ctx(), router=r)
    assert out["consensus"]["lean"] == "bearish"
    # Judge JSON was unparseable -> deterministic fallback synthesis (mentions the
    # leaning + agreement from the tally), not an empty/garbage string.
    assert "lean" in out["synthesis"].lower() or "split" in out["synthesis"].lower()
    assert out["synthesis"]  # non-empty


# --- endpoint ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    from backend.api.main import app, debate_cache
    from backend.api.auth import current_user_id
    debate_cache.clear()
    state = {"user": "debate-user"}
    app.dependency_overrides[current_user_id] = lambda: state["user"]
    client = TestClient(app)
    client.set_api_user = lambda u: state.__setitem__("user", u)  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_debate_endpoint_gated_below_premium(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "pro")   # pro lacks F_DEBATE
    r = api.post("/debate", json={"symbol": "BTCUSDT"})
    assert r.status_code == 402
    assert r.json()["upgrade"] is True


def test_debate_endpoint_full_path_mocked(api, monkeypatch):
    # Premium user + mocked debate() so no live LLM/network.
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "premium")

    import backend.signals.debate as dbt
    fake = {"symbol": "BTCUSDT", "interval": "1h",
            "consensus": {"lean": "bullish", "confidence": 66, "agreement": 80,
                          "divided": False, "score": 0.5,
                          "vote_counts": {"bullish": 3, "bearish": 1, "neutral": 1},
                          "avg_conviction": 68},
            "agents": [], "synthesis": "s", "dissent": "d",
            "what_would_change_our_mind": "x", "disclaimer": "nfa", "cost_usd": 0.02}
    monkeypatch.setattr(dbt, "debate", lambda *a, **k: fake)

    r = api.post("/debate", json={"symbol": "BTCUSDT"})
    assert r.status_code == 200
    body = r.json()
    assert body["consensus"]["lean"] == "bullish"
    assert body["cached"] is False

    r2 = api.post("/debate", json={"symbol": "BTCUSDT"})
    assert r2.json()["cached"] is True
