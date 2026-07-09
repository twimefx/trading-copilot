"""Institutional Flow Dashboard — positioning & derivatives-flow intelligence.

Phase 3 Premium feature. Aggregates Binance perp/futures data (funding-rate
trend, open-interest build/unwind, retail long/short account ratio, and
aggressive taker buy/sell flow) into a DETERMINISTIC, evidence-backed read of
where positioning is stretched and who is in control.

Same honesty architecture as the rest of the platform:
  1. Deterministic interpretation of each stream (no LLM) — every read carries
     the numbers that produced it, and streams that Binance blocks/omits are
     honestly marked unavailable rather than guessed.
  2. An optional cheap LLM narrative (router SIGNAL_SUMMARY tier) that ties the
     signals together — grounded ONLY on the computed reads, never fabricated.

Note: the futures "data" endpoints can be geo-blocked (HTTP 451) on some cloud
IPs. The dashboard shows whatever is reachable and flags the rest; it never
errors just because one stream is unavailable.
"""
from __future__ import annotations

import json

from backend.ai.router import AIRouter, TaskClass
from backend.data import binance
from backend.data.providers import asset_class

FLOW_DISCLAIMER = (
    "Institutional-flow data reflects crowd positioning and derivatives activity, "
    "not a directional guarantee. Crowded positioning can stay crowded. This is "
    "assistive analysis, not financial advice."
)


def _pct_change(series: list[float]) -> float | None:
    if len(series) < 2 or series[0] == 0:
        return None
    return round((series[-1] - series[0]) / abs(series[0]) * 100, 2)


def _trend(series: list[float]) -> str:
    if len(series) < 2:
        return "flat"
    if series[-1] > series[0]:
        return "rising"
    if series[-1] < series[0]:
        return "falling"
    return "flat"


def _interpret_funding(hist: dict) -> dict:
    if not hist.get("available") or not hist.get("series"):
        return {"available": False}
    rates = [p["rate"] for p in hist["series"]]
    latest = rates[-1]
    avg = sum(rates) / len(rates)
    # Funding sign: positive => longs pay shorts (crowded long) and vice versa.
    if latest > 0.0005:
        regime = "crowded_long"
    elif latest < -0.0005:
        regime = "crowded_short"
    else:
        regime = "balanced"
    return {
        "available": True,
        "latest_pct": round(latest * 100, 4),
        "avg_pct": round(avg * 100, 4),
        "trend": _trend(rates),
        "regime": regime,
        "note": {
            "crowded_long": "Positive funding — longs are paying to hold; crowd is long.",
            "crowded_short": "Negative funding — shorts are paying; crowd is short.",
            "balanced": "Funding near neutral — no strong crowd bias.",
        }[regime],
    }


def _interpret_oi(hist: dict, funding_regime: str | None) -> dict:
    if not hist.get("available") or not hist.get("series"):
        return {"available": False}
    ois = [p["oi"] for p in hist["series"]]
    change = _pct_change(ois)
    trend = _trend(ois)
    return {
        "available": True,
        "change_pct": change,
        "trend": trend,
        "note": (
            "Open interest rising — fresh money/positioning entering."
            if trend == "rising" else
            "Open interest falling — positions unwinding/closing."
            if trend == "falling" else
            "Open interest flat."
        ),
    }


def _interpret_long_short(ls: dict) -> dict:
    if not ls.get("available") or not ls.get("series"):
        return {"available": False}
    latest = ls["series"][-1]
    ratio = latest["ratio"]
    if ratio > 1.5:
        regime = "retail_heavily_long"
    elif ratio > 1.1:
        regime = "retail_long"
    elif ratio < 0.67:
        regime = "retail_heavily_short"
    elif ratio < 0.9:
        regime = "retail_short"
    else:
        regime = "retail_balanced"
    return {
        "available": True,
        "ratio": round(ratio, 3),
        "long_pct": round(latest["long_pct"] * 100, 1),
        "short_pct": round(latest["short_pct"] * 100, 1),
        "trend": _trend([p["ratio"] for p in ls["series"]]),
        "regime": regime,
        "note": (
            "Retail crowd is heavily long — contrarian caution on a squeeze lower."
            if regime == "retail_heavily_long" else
            "Retail crowd is heavily short — contrarian caution on a squeeze higher."
            if regime == "retail_heavily_short" else
            "Retail positioning is not at an extreme."
        ),
    }


def _interpret_taker(taker: dict) -> dict:
    if not taker.get("available") or not taker.get("series"):
        return {"available": False}
    ratios = [p["ratio"] for p in taker["series"]]
    latest = ratios[-1]
    avg = sum(ratios) / len(ratios)
    if latest > 1.05:
        flow = "buyers_aggressive"
    elif latest < 0.95:
        flow = "sellers_aggressive"
    else:
        flow = "balanced"
    return {
        "available": True,
        "latest_ratio": round(latest, 3),
        "avg_ratio": round(avg, 3),
        "trend": _trend(ratios),
        "flow": flow,
        "note": {
            "buyers_aggressive": "Takers are lifting offers — aggressive buy flow.",
            "sellers_aggressive": "Takers are hitting bids — aggressive sell flow.",
            "balanced": "Taker flow is balanced.",
        }[flow],
    }


def _positioning_summary(funding: dict, oi: dict, ls: dict, taker: dict) -> dict:
    """A compact deterministic read of who is in control and where risk sits."""
    signals: list[str] = []
    squeeze_risk = None

    if funding.get("available"):
        if funding["regime"] == "crowded_long" and oi.get("trend") == "rising":
            signals.append("Crowded longs with rising OI — vulnerable to a long squeeze if price stalls.")
            squeeze_risk = "downside (long squeeze)"
        elif funding["regime"] == "crowded_short" and oi.get("trend") == "rising":
            signals.append("Crowded shorts with rising OI — vulnerable to a short squeeze on a pop.")
            squeeze_risk = "upside (short squeeze)"

    if ls.get("available") and ls.get("regime", "").startswith("retail_heavily"):
        signals.append(ls["note"])

    if taker.get("available") and funding.get("available"):
        if taker["flow"] == "sellers_aggressive" and funding["regime"] == "crowded_long":
            signals.append("Aggressive sell flow into a crowded-long book — distribution risk.")
        elif taker["flow"] == "buyers_aggressive" and funding["regime"] == "crowded_short":
            signals.append("Aggressive buy flow into a crowded-short book — squeeze fuel.")

    return {"signals": signals, "squeeze_risk": squeeze_risk}


FLOW_SYSTEM_PROMPT = """You are a derivatives/positioning desk analyst. You are given a
DETERMINISTIC read of one asset's perp funding, open interest, retail long/short ratio,
and taker buy/sell flow — each with the numbers behind it, plus pre-computed positioning
signals.

RULES:
- Explain ONLY what the provided reads show. Do NOT invent numbers or streams. If a stream
  is unavailable, don't speculate about it.
- Be concrete: cite the funding %, OI trend, L/S ratio, taker flow.
- Focus on WHERE POSITIONING IS STRETCHED and the resulting squeeze/reversal risk.
- Never guarantee direction. Crowded can stay crowded. This is assistive, not advice.
- Finished claims only; no mid-sentence self-correction.

Respond ONLY with valid JSON in exactly this shape:
{
  "headline": "<one-sentence read of who controls the tape and where risk sits>",
  "key_points": ["<specific positioning/flow observation tied to a number>", ...],
  "squeeze_watch": "<the more likely squeeze direction and its trigger, or 'no clear squeeze setup'>"
}"""


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def institutional_flow(symbol: str = "BTCUSDT", period: str = "1h",
                       narrative: bool = True,
                       router: AIRouter | None = None,
                       fetchers: dict | None = None) -> dict:
    """Build the institutional-flow dashboard for a symbol.

    `fetchers` lets tests inject the raw data (defaults to live Binance). Forex
    symbols have no perp/futures data — returns available=False cleanly.
    Returns deterministic reads + (optional) a cheap grounded LLM narrative.
    """
    sym = symbol.upper()
    if asset_class(sym) != "crypto":
        return {"symbol": sym, "available": False,
                "message": "Institutional flow (perp/derivatives) is crypto-only.",
                "disclaimer": FLOW_DISCLAIMER, "cost_usd": 0.0}

    f = fetchers or {
        "funding": binance.fetch_funding_history(sym, limit=30),
        "oi": binance.fetch_oi_history(sym, period=period, limit=30),
        "ls": binance.fetch_long_short_ratio(sym, period=period, limit=30),
        "taker": binance.fetch_taker_ratio(sym, period=period, limit=30),
    }

    funding = _interpret_funding(f["funding"])
    oi = _interpret_oi(f["oi"], funding.get("regime"))
    ls = _interpret_long_short(f["ls"])
    taker = _interpret_taker(f["taker"])

    any_available = any(x.get("available") for x in (funding, oi, ls, taker))
    summary = _positioning_summary(funding, oi, ls, taker)

    result = {
        "symbol": sym,
        "period": period,
        "available": any_available,
        "funding": funding,
        "open_interest": oi,
        "long_short": ls,
        "taker_flow": taker,
        "positioning": summary,
        "series": {
            "funding": f["funding"].get("series", []),
            "oi": f["oi"].get("series", []),
            "long_short": f["ls"].get("series", []),
            "taker": f["taker"].get("series", []),
        },
        "disclaimer": FLOW_DISCLAIMER,
        "cost_usd": 0.0,
    }

    if not any_available:
        result["message"] = (
            "Derivatives-flow data is currently unavailable (the venue may be "
            "geo-blocking this server). Try again later."
        )
        result["narrative"] = None
        return result

    if narrative:
        router = router or AIRouter()
        prompt = (
            f"Asset: {sym} ({period}). Deterministic positioning read:\n\n"
            f"{json.dumps({'funding': funding, 'open_interest': oi, 'long_short': ls, 'taker_flow': taker, 'positioning': summary}, indent=2)}\n\n"
            "Write the desk's positioning read per your rules. Respond with JSON only."
        )
        raw = router.complete(TaskClass.SIGNAL_SUMMARY, prompt,
                              system=FLOW_SYSTEM_PROMPT, max_tokens=600)
        try:
            result["narrative"] = json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            result["narrative"] = {
                "headline": (summary["signals"][0] if summary["signals"]
                             else "No stretched positioning detected."),
                "key_points": summary["signals"],
                "squeeze_watch": summary.get("squeeze_risk") or "no clear squeeze setup",
                "generated": "rule-based-fallback",
            }
        result["cost_usd"] = round(router.cost_log.total_usd, 5)
    else:
        result["narrative"] = None

    return result
