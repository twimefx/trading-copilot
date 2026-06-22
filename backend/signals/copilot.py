"""AI Market Copilot — the hero feature.

Takes a MarketContext and asks the premium reasoning model (Claude Opus, via the
router) to synthesize a transparent, explainable market call. Direction + conviction
come from the LLM reasoning over MULTIPLE inputs; Kronos contributes range only.

Output is structured and ALWAYS carries a not-financial-advice disclaimer.
"""
from __future__ import annotations

import json

from backend.ai.router import AIRouter, TaskClass
from backend.signals.context import MarketContext, build_market_context

DISCLAIMER = (
    "This is AI-generated market analysis, not financial advice. "
    "Signals are assistive and NOT a guarantee of profit. Trading involves substantial risk of loss."
)

SYSTEM_PROMPT = """You are an elite, brutally honest market analyst for a retail trading platform.

You are given a structured MarketContext for one asset: technical indicators (RSI, MACD,
EMAs, ATR, volume trend), perp funding rate, open interest, and a Kronos RANGE forecast.

CRITICAL RULES:
- The Kronos forecast is a VOLATILITY/RANGE estimate only. It has NO directional skill
  (validated: ~35% directional accuracy). Use it ONLY for the likely price band, stop/target
  placement, and risk sizing. NEVER treat it as a buy/sell signal.
- DIRECTION and conviction must come from YOUR synthesis of the technicals + positioning
  (funding/OI) + price structure. Weigh the evidence honestly.
- Be transparent. Every claim ties to a specific data point. No black-box assertions.
- Never guarantee profit. Express uncertainty honestly. If the picture is mixed, say so and
  lower conviction.

Respond ONLY with valid JSON in exactly this shape:
{
  "lean": "bullish" | "bearish" | "neutral",
  "conviction": <integer 0-100>,
  "summary": "<2-3 sentence plain-English answer to 'what's happening and why'>",
  "drivers": ["<specific bullish/bearish driver tied to data>", ...],
  "risks": ["<what could invalidate this / key risk>", ...],
  "range_24h": {"low": <num>, "high": <num>, "source": "Kronos"},
  "suggested_invalidation": "<price level or condition that would flip the thesis>"
}"""


def analyze(ctx: MarketContext, router: AIRouter | None = None) -> dict:
    """Run the Copilot analysis on a MarketContext. Returns structured dict + disclaimer."""
    router = router or AIRouter()
    prompt = (
        f"Analyze {ctx.symbol} ({ctx.interval}). Here is the live MarketContext:\n\n"
        f"{ctx.to_prompt_json()}\n\n"
        "Synthesize a transparent directional call per your rules. Respond with JSON only."
    )
    raw = router.complete(TaskClass.MARKET_COPILOT, prompt, system=SYSTEM_PROMPT, max_tokens=1200)

    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"lean": "neutral", "conviction": 0,
                  "summary": "Model returned unparseable output.", "raw": raw[:500]}

    result["disclaimer"] = DISCLAIMER
    result["cost_usd"] = round(router.cost_log.total_usd, 5)
    return result


def analyze_symbol(symbol: str = "BTCUSDT", interval: str = "1h",
                   include_kronos: bool = True) -> dict:
    """Convenience: build context for a symbol and analyze it end-to-end."""
    ctx = build_market_context(symbol, interval, include_kronos=include_kronos)
    return analyze(ctx)
