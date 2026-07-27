"""Bull/bear researcher rounds + risk verdict — TradingAgents-style adversarial stage.

Optional add-on to the debate engine: after the specialist panel runs, two RESEARCHERS
(a bull advocate and a bear advocate) argue the strongest case for each side over N
rounds, then a deterministic RISK VERDICT (approve / caution / reject) is computed
from the tally + panel breadth. Off by default; the classic single-round debate path
is unchanged.

The bull/bear cases and the risk verdict are DETERMINISTIC from the agent cards (no
extra LLM calls) so they stay honest and free, matching the existing tally design.
"""
import pytest

from backend.signals import debate as debate_mod


def _card(agent, lean, conviction, evidence=None, ok=True):
    return {"agent": agent, "name": agent.title(), "lean": lean, "conviction": conviction,
            "rationale": f"{agent} rationale", "key_evidence": evidence or [], "ok": ok}


# --- bull/bear researcher cases (deterministic) --------------------------------

def test_bull_case_collects_bullish_evidence():
    cards = [
        _card("trend", "bullish", 80, ["price above EMA stack"]),
        _card("momentum", "bullish", 70, ["MACD rising"]),
        _card("positioning", "bearish", 60, ["funding crowded long"]),
    ]
    case = debate_mod.build_researcher_case(cards, "bullish")
    assert case["side"] == "bullish"
    assert case["supporters"] == 2
    assert "price above EMA stack" in case["points"]
    assert "MACD rising" in case["points"]
    assert case["avg_conviction"] == 75


def test_bear_case_collects_bearish_evidence():
    cards = [
        _card("trend", "bullish", 80, ["price above EMA stack"]),
        _card("positioning", "bearish", 60, ["funding crowded long"]),
    ]
    case = debate_mod.build_researcher_case(cards, "bearish")
    assert case["supporters"] == 1
    assert "funding crowded long" in case["points"]


def test_researcher_case_empty_side():
    cards = [_card("trend", "bullish", 80)]
    case = debate_mod.build_researcher_case(cards, "bearish")
    assert case["supporters"] == 0
    assert case["points"] == []


def test_run_researcher_rounds_produces_both_cases():
    cards = [
        _card("trend", "bullish", 80, ["structure up"]),
        _card("momentum", "bearish", 60, ["momentum fading"]),
        _card("positioning", "bearish", 55, ["crowded long"]),
    ]
    rnds = debate_mod.run_researcher_rounds(cards, rounds=1)
    assert "bull" in rnds and "bear" in rnds
    assert rnds["bull"]["supporters"] == 1
    assert rnds["bear"]["supporters"] == 2


# --- risk verdict (deterministic) ----------------------------------------------

def test_risk_verdict_approve_on_aligned_confident_panel():
    tally = {"direction": "bullish", "confidence": 80, "agreement": 85,
             "divided": False, "score": 0.6}
    v = debate_mod.risk_verdict(tally)
    assert v["verdict"] == "APPROVE"
    assert v["reasons"]


def test_risk_verdict_reject_on_divided_panel():
    tally = {"direction": "neutral", "confidence": 30, "agreement": 25,
             "divided": True, "score": 0.05}
    v = debate_mod.risk_verdict(tally)
    assert v["verdict"] == "REJECT"


def test_risk_verdict_caution_on_middle():
    tally = {"direction": "bullish", "confidence": 55, "agreement": 60,
             "divided": False, "score": 0.3}
    v = debate_mod.risk_verdict(tally)
    assert v["verdict"] == "CAUTION"


def test_risk_verdict_low_confidence_not_approve():
    tally = {"direction": "bullish", "confidence": 40, "agreement": 50,
             "divided": False, "score": 0.2}
    v = debate_mod.risk_verdict(tally)
    assert v["verdict"] in ("CAUTION", "REJECT")


# --- integration: debate() with researchers enabled ----------------------------

def test_debate_with_researchers_adds_rounds_and_verdict():
    cards = [
        _card("trend", "bullish", 80, ["structure up"]),
        _card("momentum", "bullish", 70, ["macd rising"]),
        _card("positioning", "bullish", 65, ["oi rising"]),
        _card("volatility", "neutral", 40, []),
        _card("contrarian", "bearish", 50, ["crowded"]),
    ]
    out = debate_mod.debate_from_cards(cards, symbol="BTCUSDT", interval="1h",
                                       router=_StubRouter(), researchers=True)
    assert "researchers" in out
    assert "risk" in out
    assert out["risk"]["verdict"] in ("APPROVE", "CAUTION", "REJECT")


def test_debate_without_researchers_omits_them():
    cards = [_card("trend", "bullish", 80, ["structure up"])]
    out = debate_mod.debate_from_cards(cards, symbol="BTCUSDT", interval="1h",
                                       router=_StubRouter(), researchers=False)
    assert "researchers" not in out
    assert "risk" not in out


class _StubRouter:
    """Judge call returns minimal valid JSON; tracks cost."""

    def __init__(self):
        class _CL: total_usd = 0.0
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        return '{"synthesis":"desk read","dissent":"none","what_would_change_our_mind":"x"}'
