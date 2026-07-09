"""Debate Engine — deterministic vote tally + premium Judge synthesis.

Flow: build_market_context -> run_panel (agents.py) -> tally_votes (deterministic,
auditable) -> Judge (Opus, premium tier) writes the final verdict with a confidence
that is DOWN-WEIGHTED by how much the panel disagrees.

The tally is pure code, so the "vote" is not something an LLM invented — it's an
auditable count. The Judge explains and contextualizes it; it never fabricates votes.
When the panel is split, confidence is low and we SAY the panel is divided — the
disagreement is shown as signal, not hidden.
"""
from __future__ import annotations

import json
import statistics

from backend.ai.router import AIRouter, TaskClass
from backend.signals.agents import run_panel
from backend.signals.context import MarketContext, build_market_context

DEBATE_DISCLAIMER = (
    "This is a panel of AI models debating one asset — assistive analysis, NOT "
    "financial advice and NOT a guarantee. A divided panel means low confidence: "
    "treat consensus as one input, not a signal to act. Trading involves substantial "
    "risk of loss."
)

# Contrarian is a stress-test, not a directional vote — weight it low in the tally
# so a strong devil's-advocate doesn't by itself flip the consensus.
_CONTRARIAN_WEIGHT = 0.4
_DIRECTIONAL_WEIGHT = 1.0

_LEAN_SIGN = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def tally_votes(cards: list[dict]) -> dict:
    """Deterministic consensus from agent cards. Pure/testable.

    Returns direction (bullish/bearish/neutral), a raw consensus score in [-1, 1],
    a 0-100 agreement level, the vote counts, and a confidence 0-100 that already
    accounts for both average conviction and panel agreement.
    """
    usable = [c for c in cards if c.get("ok", True)]
    if not usable:
        return {"direction": "neutral", "score": 0.0, "agreement": 0,
                "confidence": 0, "counts": {"bullish": 0, "bearish": 0, "neutral": 0},
                "avg_conviction": 0, "divided": True}

    weighted_sum = 0.0
    weight_total = 0.0
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    convictions: list[float] = []
    signed_positions: list[float] = []  # per-agent signed lean*conviction, for spread

    for c in usable:
        w = _CONTRARIAN_WEIGHT if c.get("agent") == "contrarian" else _DIRECTIONAL_WEIGHT
        sign = _LEAN_SIGN.get(c["lean"], 0.0)
        conv = c["conviction"] / 100.0
        weighted_sum += sign * conv * w
        weight_total += w
        counts[c["lean"]] = counts.get(c["lean"], 0) + 1
        convictions.append(c["conviction"])
        signed_positions.append(sign * conv)

    score = round(weighted_sum / weight_total, 3) if weight_total else 0.0

    if score > 0.15:
        direction = "bullish"
    elif score < -0.15:
        direction = "bearish"
    else:
        direction = "neutral"

    # Agreement: how tightly the agents cluster directionally. Low spread of the
    # signed positions => high agreement. stdev of signed positions in [0, ~1].
    if len(signed_positions) > 1:
        spread = statistics.pstdev(signed_positions)
    else:
        spread = 0.0
    agreement = max(0, min(100, round((1.0 - min(spread, 1.0)) * 100)))

    # A panel is "divided" if bulls and bears both have real representation.
    directional_bulls = counts["bullish"]
    directional_bears = counts["bearish"]
    divided = directional_bulls > 0 and directional_bears > 0 and abs(score) < 0.4

    avg_conviction = round(sum(convictions) / len(convictions)) if convictions else 0

    # Confidence blends strength of the signal (|score|), average conviction, and
    # agreement — a strong-but-split panel is NOT confident.
    confidence = round(min(100.0, (abs(score) * 100 * 0.5)
                                  + (avg_conviction * 0.25)
                                  + (agreement * 0.25)))
    if divided:
        confidence = min(confidence, 45)  # cap: a split panel can't be high-confidence

    return {
        "direction": direction,
        "score": score,
        "agreement": agreement,
        "confidence": confidence,
        "counts": counts,
        "avg_conviction": avg_conviction,
        "divided": divided,
    }


JUDGE_SYSTEM_PROMPT = """You are the head of an AI research desk. Five analysts each
gave a view on one asset, grounded in the same market data, plus a deterministic vote
tally computed from their cards.

Your job: synthesize a FINAL, honest verdict. You explain and contextualize the panel —
you do NOT invent votes or numbers. The deterministic tally is authoritative for the
direction and confidence; your synthesis must be consistent with it.

RULES:
- If the panel is divided (tally says divided/low confidence), SAY the desk is split,
  explain the core disagreement, and keep the tone appropriately uncertain.
- Cite which analysts agreed and which dissented, and WHY (their key evidence).
- Never guarantee outcomes. Never give financial advice. This is assistive analysis.
- State the concrete condition(s) that would change the desk's mind.
- No self-correction or hedging mid-sentence. Finished claims only.

Respond ONLY with valid JSON in exactly this shape:
{
  "synthesis": "<3-5 sentences: the desk's honest read, consistent with the tally>",
  "dissent": "<who disagreed and the strongest counter-point, or 'none — panel aligned'>",
  "what_would_change_our_mind": "<specific price level / condition / data shift>"
}"""


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def _rule_based_judgement(tally: dict) -> dict:
    d = tally["direction"]
    if tally["divided"]:
        synth = (f"The desk is split: {tally['counts']['bullish']} bullish vs "
                 f"{tally['counts']['bearish']} bearish (agreement {tally['agreement']}%). "
                 f"Consensus is weak — treat as low confidence.")
    else:
        synth = (f"The desk leans {d} (score {tally['score']}, agreement "
                 f"{tally['agreement']}%, avg conviction {tally['avg_conviction']}).")
    return {"synthesis": synth, "dissent": "see agent cards",
            "what_would_change_our_mind": "", "generated": "rule-based-fallback"}


def debate(symbol: str = "BTCUSDT", interval: str = "1h",
           include_kronos: bool = True,
           ctx: MarketContext | None = None,
           router: AIRouter | None = None) -> dict:
    """Run the full multi-agent debate for a symbol. Returns a structured verdict.

    Panel (cheap tier) -> deterministic tally -> Judge (premium tier). The Judge's
    confidence comes from the tally, so a split panel is honestly low-confidence.
    """
    router = router or AIRouter()
    if ctx is None:
        ctx = build_market_context(symbol, interval, include_kronos=include_kronos)

    cards = run_panel(ctx, router=router)
    tally = tally_votes(cards)

    prompt = (
        f"Asset: {ctx.symbol} ({ctx.interval}).\n\n"
        f"ANALYST CARDS:\n{json.dumps([{k: c[k] for k in ('name','lean','conviction','rationale','key_evidence')} for c in cards], indent=2)}\n\n"
        f"DETERMINISTIC VOTE TALLY (authoritative for direction + confidence):\n"
        f"{json.dumps(tally, indent=2)}\n\n"
        "Write the desk's final verdict per your rules. Respond with JSON only."
    )
    raw = router.complete(TaskClass.AGENT_CONSENSUS, prompt,
                          system=JUDGE_SYSTEM_PROMPT, max_tokens=700)
    try:
        judgement = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        judgement = _rule_based_judgement(tally)

    return {
        "symbol": ctx.symbol,
        "interval": ctx.interval,
        "consensus": {
            "lean": tally["direction"],
            "confidence": tally["confidence"],
            "agreement": tally["agreement"],
            "divided": tally["divided"],
            "score": tally["score"],
            "vote_counts": tally["counts"],
            "avg_conviction": tally["avg_conviction"],
        },
        "agents": cards,
        "synthesis": judgement.get("synthesis", ""),
        "dissent": judgement.get("dissent", ""),
        "what_would_change_our_mind": judgement.get("what_would_change_our_mind", ""),
        "disclaimer": DEBATE_DISCLAIMER,
        "cost_usd": round(router.cost_log.total_usd, 5),
    }
