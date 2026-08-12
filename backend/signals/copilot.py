"""AI Market Copilot — the hero feature.

Takes a MarketContext and asks the premium reasoning model (Claude Opus, via the
router) to synthesize a transparent, explainable market call. Direction + conviction
come from the LLM reasoning over MULTIPLE inputs; Kronos contributes range only.

Output is structured and ALWAYS carries a not-financial-advice disclaimer.
"""
from __future__ import annotations

import json
import re

from backend.ai.router import AIRouter, TaskClass
from backend.signals import history as signal_history
from backend.signals.context import MarketContext, build_market_context
from backend.signals.kronos_consensus import score_context

DISCLAIMER = (
    "This is AI-generated market analysis, not financial advice. "
    "Signals are assistive and NOT a guarantee of profit. Trading involves substantial risk of loss."
)

# Matches an LLM mid-sentence self-correction such as
#   "... and below... actually above its signal ..."
#   "... is X, wait no, Y ..."
#   "... rising — no, falling ..."
# i.e. an aborted clause followed by an ellipsis/dash and a correction marker.
# We drop the aborted fragment and keep the corrected assertion so production
# text never exposes the model second-guessing itself.
_SELF_CORRECTION_RE = re.compile(
    r"\s*\b(\w+(?:\s+\w+){0,3})\s*"          # short aborted fragment (1-4 words)
    r"(?:\.{2,}|—|–|-{1,2}|,)\s*"             # ellipsis / dash / comma break
    r"(?:actually|wait,?\s*no|no,|correction:|i mean)\s+",  # correction marker
    re.IGNORECASE,
)


def _sanitize_text(s: str) -> str:
    """Remove leaked LLM self-corrections from a single string.

    Keeps the corrected clause, drops the aborted fragment + correction marker.
    Best-effort and idempotent; leaves clean text untouched.
    """
    if not isinstance(s, str) or not s:
        return s
    cleaned = _SELF_CORRECTION_RE.sub(" ", s)
    # Tidy any doubled spaces / space-before-punctuation left behind.
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def _clean_result(result: dict) -> dict:
    """Sanitize all free-text LLM fields in-place before returning to the client."""
    if isinstance(result.get("summary"), str):
        result["summary"] = _sanitize_text(result["summary"])
    for key in ("drivers", "risks"):
        items = result.get(key)
        if isinstance(items, list):
            result[key] = [_sanitize_text(x) if isinstance(x, str) else x for x in items]
    if isinstance(result.get("suggested_invalidation"), str):
        result["suggested_invalidation"] = _sanitize_text(result["suggested_invalidation"])
    return result

SYSTEM_PROMPT = """You are an elite, brutally honest market analyst for a retail trading platform.

You are given a structured MarketContext for one asset: technical indicators (RSI, MACD,
EMAs, ATR, volume trend), perp funding rate, open interest, and (when available) a Kronos
RANGE forecast.

CRITICAL RULES:
- The Kronos forecast, WHEN PRESENT, is a VOLATILITY/RANGE estimate only. It has NO directional
  skill (validated: ~35% directional accuracy). Use it ONLY for the likely price band, stop/target
  placement, and risk sizing. NEVER treat it as a buy/sell signal.
- DIRECTION and conviction must come from YOUR synthesis of the technicals + positioning
  (funding/OI) + price structure. Weigh the evidence honestly.
- Be transparent. Every claim ties to a specific data point. No black-box assertions.
- Never guarantee profit. Express uncertainty honestly. If the picture is mixed, say so and
  lower conviction.
- Do NOT invent a price range. The system computes the range deterministically and adds it
  AFTER you respond. Do not output a range_24h field.
- Write every field as a FINISHED statement. Do NOT narrate your own reasoning or self-correct
  mid-sentence (no "...actually...", "wait, no", "I mean"). State only the final, correct claim.

Respond ONLY with valid JSON in exactly this shape:
{
  "lean": "bullish" | "bearish" | "neutral",
  "conviction": <integer 0-100>,
  "summary": "<2-3 sentence plain-English answer to 'what's happening and why'>",
  "drivers": ["<specific bullish/bearish driver tied to data>", ...],
  "risks": ["<what could invalidate this / key risk>", ...],
  "suggested_invalidation": "<price level or condition that would flip the thesis>"
}"""


def _price_decimals(value: float) -> int:
    """Choose a sensible number of decimal places for a price band.

    Crypto majors trade in the thousands (round to 2dp); forex (~1.14) and
    metals/JPY need more precision or the low/high collapse to the same number
    when rounded to 2dp. Scale decimals to the price magnitude.
    """
    v = abs(value)
    if v >= 100:      # BTC, ETH(hundreds+), XAU(~2000), indices
        return 2
    if v >= 1:        # most FX majors (EURUSD ~1.14), mid-priced alts
        return 4
    if v >= 0.01:     # XRP, small alts
        return 5
    return 6          # micro-priced assets


def _compute_range(ctx: MarketContext) -> dict:
    """Authoritative 24h range — NEVER LLM-invented.

    Priority:
      1. Real Kronos forecast if it actually ran (source "Kronos").
      2. Honest ATR-based band from live indicators (source "ATR estimate").
      3. Null band if we can't even compute ATR (source "unavailable").
    """
    kr = ctx.kronos_range
    if isinstance(kr, dict) and kr.get("low") is not None and kr.get("high") is not None:
        return {"low": kr["low"], "high": kr["high"], "source": "Kronos"}

    ind = ctx.indicators or {}
    close = ind.get("last_close")
    atr = ind.get("atr_14")
    if close is not None and atr is not None:
        # ~24 1h-bars of ATR drift as a rough 1-sigma-ish band; honest heuristic, labeled as such.
        span = atr * 4.0
        # Precision must scale with price magnitude, else forex/JPY bands round
        # to identical low==high (e.g. 1.14 – 1.14). Base decimals on the close.
        dp = _price_decimals(close)
        return {
            "low": round(close - span, dp),
            "high": round(close + span, dp),
            "source": "ATR estimate",
        }
    return {"low": None, "high": None, "source": "unavailable"}


def analyze(ctx: MarketContext, router: AIRouter | None = None) -> dict:
    """Run the Copilot analysis on a MarketContext. Returns structured dict + disclaimer."""
    router = router or AIRouter()
    # Reflection loop: inject our own recent, honestly-scored track record on this
    # symbol so the model reasons with its history instead of in a vacuum.
    reflection = signal_history.reflection(ctx.symbol)
    prompt = (
        f"Analyze {ctx.symbol} ({ctx.interval}). Here is the live MarketContext:\n\n"
        f"{ctx.to_prompt_json()}\n\n"
        + (f"{reflection}\n\n" if reflection else "")
        + "Synthesize a transparent directional call per your rules. Respond with JSON only."
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
    # Surface the reflection block that informed this call (None when no record yet)
    # so the UI can show the call was made with our track record in view.
    result["track_record"] = reflection
    # Reference price at call time — powers the track record's honest scoring.
    last_close = ctx.indicators.get("last_close")
    if isinstance(last_close, (int, float)):
        result["entry_price"] = last_close
    # Range is authoritative/deterministic — never trust an LLM-invented band.
    result["range_24h"] = _compute_range(ctx)
    # KRONOS is deterministic: the LLM explains verified context but cannot invent
    # component scores, consensus labels, or their source metadata.
    result["kronos_consensus"] = score_context(ctx)
    result["data_provenance"] = ctx.provenance
    result["fundamentals"] = ctx.fundamentals
    result["news"] = ctx.news
    # Strip any leaked mid-sentence self-corrections from free-text fields.
    _clean_result(result)
    return result


def analyze_symbol(symbol: str = "BTCUSDT", interval: str = "1h",
                   include_kronos: bool = True) -> dict:
    """Convenience: build context for a symbol and analyze it end-to-end."""
    ctx = build_market_context(symbol, interval, include_kronos=include_kronos)
    return analyze(ctx)
