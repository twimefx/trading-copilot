"""KRONOS consensus engine — deterministic, evidence-backed signal aggregation.

This is intentionally separate from Kronos forecasting. The forecasting model supplies
an optional volatility band; KRONOS consensus combines independent technical, quant,
regime, and risk observations. It never asks an LLM to create market values or scores.
"""
from __future__ import annotations

from typing import Any

from backend.signals.context import MarketContext

_DEFAULT_WEIGHTS = {
    "technical": 0.30,
    "quant": 0.25,
    "fundamental": 0.0,
    "macro": 0.0,
    "sentiment": 0.0,
    "regime": 0.25,
    "risk": 0.20,
    "portfolio": 0.0,
}


def _direction(score: float, deadband: float = 5.0) -> str:
    if score >= 50 + deadband:
        return "bullish"
    if score <= 50 - deadband:
        return "bearish"
    return "neutral"


def _component(component_type: str, score: float, confidence: float, evidence: list[str], source: str,
               as_of: str | None = None) -> dict[str, Any]:
    bounded_score = round(max(0.0, min(100.0, score)), 1)
    return {
        "component_type": component_type,
        "score": bounded_score,
        "direction": _direction(bounded_score),
        "confidence": round(max(0.0, min(100.0, confidence)), 1),
        "evidence": evidence,
        "source": source,
        "as_of": as_of,
    }


def _technical(context: MarketContext, as_of: str | None = None) -> dict[str, Any]:
    ind = context.indicators or {}
    price = ind.get("last_close")
    score = 50.0
    evidence: list[str] = []
    observations = 0

    for label, key in (("EMA 20", "ema_20"), ("EMA 50", "ema_50"), ("SMA 200", "sma_200")):
        average = ind.get(key)
        if isinstance(price, (int, float)) and isinstance(average, (int, float)):
            observations += 1
            if price > average:
                score += 8
                evidence.append(f"Price is above {label} ({average:g}).")
            else:
                score -= 8
                evidence.append(f"Price is below {label} ({average:g}).")

    macd_hist = ind.get("macd_hist")
    if isinstance(macd_hist, (int, float)):
        observations += 1
        score += 8 if macd_hist > 0 else -8 if macd_hist < 0 else 0
        evidence.append(f"MACD histogram is {'positive' if macd_hist > 0 else 'negative' if macd_hist < 0 else 'flat'} ({macd_hist:g}).")

    rsi = ind.get("rsi_14")
    if isinstance(rsi, (int, float)):
        observations += 1
        if rsi > 70:
            score -= 6
            evidence.append(f"RSI is {rsi:g}, signalling elevated short-term extension risk.")
        elif rsi < 30:
            score += 4
            evidence.append(f"RSI is {rsi:g}, indicating an oversold condition rather than trend confirmation.")
        else:
            score += 3 if rsi >= 50 else -3
            evidence.append(f"RSI is {rsi:g}, a {'constructive' if rsi >= 50 else 'soft'} momentum posture.")

    if not evidence:
        evidence.append("Technical inputs are unavailable.")
    return _component("technical", score, min(95, observations * 18), evidence, "technical indicators", as_of)


def _quant(context: MarketContext, as_of: str | None = None) -> dict[str, Any]:
    ind = context.indicators or {}
    score = 50.0
    evidence: list[str] = []
    observations = 0

    volume_trend = ind.get("volume_trend")
    if isinstance(volume_trend, (int, float)):
        observations += 1
        if volume_trend > 1.05:
            score += 8
            evidence.append(f"Recent volume is {volume_trend:.2f}× its comparison window, supporting participation.")
        elif volume_trend < 0.95:
            score -= 5
            evidence.append(f"Recent volume is {volume_trend:.2f}× its comparison window, reducing confirmation.")
        else:
            evidence.append("Volume is broadly in line with its comparison window.")

    structure = (context.structure or {}).get("structure", "")
    if "uptrend" in structure:
        observations += 1
        score += 12
        evidence.append("Price structure shows higher highs and higher lows.")
    elif "downtrend" in structure:
        observations += 1
        score -= 12
        evidence.append("Price structure shows lower highs and lower lows.")
    elif structure:
        observations += 1
        evidence.append("Price structure is mixed/range-bound.")

    if not evidence:
        evidence.append("Quant factor inputs are unavailable.")
    return _component("quant", score, min(85, observations * 30), evidence, "deterministic factor model", as_of)


def _regime(context: MarketContext, as_of: str | None = None) -> dict[str, Any]:
    structure = (context.structure or {}).get("structure", "")
    atr_pct = (context.indicators or {}).get("atr_pct")
    score = 50.0
    evidence: list[str] = []
    observations = 0

    if "uptrend" in structure:
        observations += 1
        score += 14
        evidence.append("Trend regime is bullish from current price structure.")
    elif "downtrend" in structure:
        observations += 1
        score -= 14
        evidence.append("Trend regime is bearish from current price structure.")
    else:
        evidence.append("Trend regime is range/mixed.")

    if isinstance(atr_pct, (int, float)):
        observations += 1
        if atr_pct > 5:
            score -= 5
            evidence.append(f"ATR is {atr_pct:g}% of price, an elevated-volatility regime.")
        else:
            evidence.append(f"ATR is {atr_pct:g}% of price.")

    return _component("regime", score, min(80, 35 + observations * 25), evidence, "market regime service", as_of)


def _risk(context: MarketContext, as_of: str | None = None) -> dict[str, Any]:
    ind = context.indicators or {}
    atr_pct = ind.get("atr_pct")
    rsi = ind.get("rsi_14")
    score = 50.0
    evidence: list[str] = []
    observations = 0

    if isinstance(atr_pct, (int, float)):
        observations += 1
        if atr_pct > 5:
            score -= 18
            evidence.append(f"ATR at {atr_pct:g}% implies high sizing and gap-risk sensitivity.")
        elif atr_pct > 3:
            score -= 7
            evidence.append(f"ATR at {atr_pct:g}% implies moderate volatility risk.")
        else:
            score += 5
            evidence.append(f"ATR at {atr_pct:g}% indicates comparatively contained realized volatility.")

    if isinstance(rsi, (int, float)) and rsi > 75:
        observations += 1
        score -= 10
        evidence.append(f"RSI at {rsi:g} adds extension risk.")

    if not evidence:
        evidence.append("Risk inputs are incomplete; confidence is reduced.")
    return _component("risk", score, min(85, 30 + observations * 25), evidence, "risk analytics", as_of)


def _missing(component_type: str, note: str, as_of: str | None = None) -> dict[str, Any]:
    return _component(component_type, 50, 0, [note], "not configured", as_of)


def _signal(score: float, confidence: float) -> str:
    if confidence < 35:
        return "WAIT"
    if score >= 78:
        return "STRONG BUY"
    if score >= 63:
        return "BUY"
    if score >= 55:
        return "MODERATE BUY"
    if score <= 22:
        return "STRONG SELL"
    if score <= 37:
        return "SELL"
    if score <= 45:
        return "MODERATE SELL"
    return "NEUTRAL"


def score_context(context: MarketContext, weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Return a deterministic, explainable KRONOS consensus for one MarketContext."""
    effective_weights = {**_DEFAULT_WEIGHTS, **(weights or {})}
    as_of = (context.provenance or {}).get("as_of")
    components = [
        _technical(context, as_of), _quant(context, as_of), _missing("fundamental", "Fundamental provider is not configured for this slice.", as_of),
        _missing("macro", "Macro provider is not configured for this slice.", as_of),
        _missing("sentiment", "News/sentiment provider is not configured for this slice.", as_of), _regime(context, as_of), _risk(context, as_of),
    ]
    weighted = [(component, effective_weights.get(component["component_type"], 0.0)) for component in components]
    active = [(component, weight) for component, weight in weighted if weight > 0 and component["confidence"] > 0]
    denominator = sum(weight for _, weight in active)
    overall = 50.0 if not denominator else sum(component["score"] * weight for component, weight in active) / denominator
    agreement = 100.0 - sum(abs(component["score"] - overall) * weight for component, weight in active) / denominator if denominator else 0.0
    data_confidence = sum(component["confidence"] * weight for component, weight in active) / denominator if denominator else 0.0
    consensus_confidence = round(max(0.0, min(100.0, (data_confidence * 0.65) + (agreement * 0.35))), 1)
    model_probability = round(max(0.0, min(100.0, 50 + (overall - 50) * (consensus_confidence / 100))), 1)

    return {
        "engine": "KRONOS consensus v1",
        "asset_class": context.asset_class,
        "overall_score": round(overall, 1),
        "direction": _direction(overall),
        "signal": _signal(overall, consensus_confidence),
        "consensus_confidence": consensus_confidence,
        "model_probability": model_probability,
        "confidence_note": "Consensus confidence measures evidence coverage and agreement; it is not a calibrated probability of profit.",
        "components": [{**component, "weight": effective_weights.get(component["component_type"], 0.0)} for component in components],
        "as_of": as_of,
    }
