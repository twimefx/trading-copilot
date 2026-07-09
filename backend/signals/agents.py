"""Multi-agent panel — the Premium Debate Engine's specialists.

Each agent analyzes the SAME MarketContext from ONE analytical lens and returns a
structured card (lean / conviction / rationale / evidence). Agents ground every
claim on a real data point in the context — same honesty rule as the Copilot.

Cost design: the specialist agents route to the router's cheap tier (MARKET_SCAN
-> DeepSeek). Many small calls, cheapest at scale. The Judge (in debate.py) uses
the premium tier. If a provider is down/unfunded the router falls back to
Anthropic automatically (hardened).

The Contrarian runs LAST and is told the panel's emerging lean, so it can argue
the other side with specifics — producing a real debate, not five echoes.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.ai.router import AIRouter, TaskClass
from backend.signals.context import MarketContext

# --- agent definitions -------------------------------------------------------
# Each: (key, display name, lens instruction). All share a strict output contract.

_OUTPUT_CONTRACT = """Respond ONLY with valid JSON in exactly this shape:
{
  "lean": "bullish" | "bearish" | "neutral",
  "conviction": <integer 0-100>,
  "rationale": "<2-3 sentences, each claim tied to a specific data point in the context>",
  "key_evidence": ["<specific datapoint that drove your view>", ...]
}
Rules: Every claim ties to a data point actually present in the MarketContext.
Never invent numbers. If your lens has weak signal here, say so and lower conviction.
State finished claims only — no self-correction, no hedging mid-sentence."""

# Directional lenses. `system` is the agent's persona + what it may look at.
AGENTS: list[dict] = [
    {
        "key": "trend",
        "name": "Trend Analyst",
        "system": (
            "You are a trend and market-structure analyst. Judge direction ONLY from "
            "trend evidence: price vs EMA20/EMA50/SMA200, EMA alignment/stacking, and "
            "higher-highs/lower-lows structure. Ignore momentum oscillators and "
            "positioning — other agents cover those. "
        ),
    },
    {
        "key": "momentum",
        "name": "Momentum Analyst",
        "system": (
            "You are a momentum and exhaustion analyst. Judge from RSI (overbought/"
            "oversold/divergence), MACD histogram (accelerating/fading), and ATR% "
            "(expanding/contracting volatility). Distinguish strong momentum from "
            "exhaustion. Ignore long-term trend structure and positioning. "
        ),
    },
    {
        "key": "positioning",
        "name": "Positioning Analyst",
        "system": (
            "You are a crowd-positioning and sentiment analyst. Judge from perp funding "
            "rate (crowded long if strongly positive, crowded short if negative) and open "
            "interest (rising OI + rising price = conviction; rising OI + falling price = "
            "trapped longs). If funding/OI are unavailable, say so and go neutral with low "
            "conviction. Ignore technicals — other agents cover those. "
        ),
    },
    {
        "key": "volatility",
        "name": "Volatility & Range Analyst",
        "system": (
            "You are a volatility and range analyst. Your job is RISK, not direction: use "
            "the Kronos range (or ATR-based band) and atr_pct to judge how much room price "
            "has and whether a move is likely to sustain or mean-revert within the band. "
            "Lean neutral unless volatility structure itself skews odds; keep conviction "
            "modest — you assess risk, not a call. "
        ),
    },
    {
        "key": "price_action",
        "name": "Price Action Analyst",
        "system": (
            "You are a pure price-action analyst. Judge ONLY from the `structure` block: "
            "swing highs/lows, market structure (HH/HL vs LH/LL), position_in_range_pct, "
            "and the nearest support/resistance with distance-to them. Read whether price "
            "is breaking, rejecting, or ranging between levels, and whether it's buying near "
            "support or chasing into resistance. Cite the actual levels. Ignore oscillators "
            "and positioning — other agents cover those. If `structure.available` is false, "
            "say so and go neutral with low conviction. "
        ),
    },
    {
        "key": "chart_pattern",
        "name": "Chart Pattern Analyst",
        "system": (
            "You are a classical chart-pattern analyst. From `structure` (swing highs/lows, "
            "period_high/low, position_in_range_pct) and `structure.recent_candles` (o/h/l/c, "
            "direction, body_pct), identify any FORMING pattern — double top/bottom, "
            "higher-low continuation, range/rectangle, breakout or failed breakout, or candle "
            "signals (large-body impulse, doji/indecision, engulfing). Name the pattern and "
            "the level that confirms or invalidates it. Only call a pattern the data actually "
            "supports — if none is clean, say 'no clear pattern' and go neutral, low conviction. "
            "Never invent price levels not present in the context. "
        ),
    },
]

CONTRARIAN = {
    "key": "contrarian",
    "name": "Contrarian",
    "system": (
        "You are the panel's devil's advocate. The other analysts are leaning {emerging}. "
        "Your job is to argue the OPPOSITE case as strongly as the data honestly allows: "
        "what are they missing, what would trap the crowd, where is the consensus fragile? "
        "Ground every counter-point in a real data point. If the contrarian case is "
        "genuinely weak, admit it and keep conviction low — do not manufacture a bear/bull "
        "case that the data doesn't support. "
    ),
}


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def _parse_card(raw: str, agent_key: str, agent_name: str) -> dict:
    try:
        d = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return {"agent": agent_key, "name": agent_name, "lean": "neutral",
                "conviction": 0, "rationale": "Agent returned unparseable output.",
                "key_evidence": [], "ok": False}
    lean = str(d.get("lean", "neutral")).lower()
    if lean not in ("bullish", "bearish", "neutral"):
        lean = "neutral"
    try:
        conv = max(0, min(100, int(d.get("conviction", 0))))
    except (TypeError, ValueError):
        conv = 0
    return {
        "agent": agent_key,
        "name": agent_name,
        "lean": lean,
        "conviction": conv,
        "rationale": d.get("rationale", ""),
        "key_evidence": d.get("key_evidence", []) if isinstance(d.get("key_evidence"), list) else [],
        "ok": True,
    }


def _agent_prompt(ctx: MarketContext) -> str:
    return (
        f"Analyze {ctx.symbol} ({ctx.interval}) from your lens only.\n\n"
        f"MarketContext:\n{ctx.to_prompt_json()}\n\n"
        f"{_OUTPUT_CONTRACT}"
    )


def run_agent(agent: dict, ctx: MarketContext, router: AIRouter,
              emerging: str | None = None) -> dict:
    """Run one specialist agent over the context. Cheap tier. Returns a card."""
    system = agent["system"]
    if agent["key"] == "contrarian":
        system = system.format(emerging=emerging or "mixed")
    system = system + "\n\n" + _OUTPUT_CONTRACT
    raw = router.complete(TaskClass.MARKET_SCAN, _agent_prompt(ctx),
                          system=system, max_tokens=500)
    return _parse_card(raw, agent["key"], agent["name"])


def _emerging_lean(cards: list[dict]) -> str:
    """Quick majority read of the directional agents, to brief the contrarian."""
    score = 0
    for c in cards:
        w = c["conviction"] / 100.0
        if c["lean"] == "bullish":
            score += w
        elif c["lean"] == "bearish":
            score -= w
    if score > 0.3:
        return "bullish"
    if score < -0.3:
        return "bearish"
    return "mixed/neutral"


def run_panel(ctx: MarketContext, router: AIRouter | None = None) -> list[dict]:
    """Run the full panel: directional agents in parallel, then the contrarian.

    The contrarian is briefed on the panel's emerging lean so it can push back
    with specifics — a real debate rather than independent monologues.
    """
    router = router or AIRouter()

    cards: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
        futures = {pool.submit(run_agent, a, ctx, router): a["key"] for a in AGENTS}
        for fut in as_completed(futures):
            cards.append(fut.result())

    # Stable display order (as_completed returns out of order).
    order = {a["key"]: i for i, a in enumerate(AGENTS)}
    cards.sort(key=lambda c: order.get(c["agent"], 99))

    # Contrarian goes last, briefed on where the panel is leaning.
    emerging = _emerging_lean(cards)
    cards.append(run_agent(CONTRARIAN, ctx, router, emerging=emerging))
    return cards
