"""Market regime risk gate — a deterministic 4-state gate run BEFORE generating
candidates or a daily brief (idea borrowed from the hermes-trading-research package).

States:
  FULL_RISK_ALLOWED — broad uptrend participation, no major stress. Full-size ideas.
  SELECTIVE_ONLY    — mixed evidence; only the strongest setups, smaller size.
  CASH_PRIORITY     — risk-off / deteriorating breadth; no new discretionary swing.
  RESEARCH_ONLY     — data missing / uncertain regime; research and journaling only.

The gate is DETERMINISTIC (no LLM): it reads indicator snapshots across a small
universe and summarizes breadth, trend participation, and momentum stress. It is a
decision-SUPPORT gate for a human — it never makes an execution recommendation.
"""
from __future__ import annotations

from backend.data.providers import get_provider
from backend.data.indicators import snapshot

STATES = ("FULL_RISK_ALLOWED", "SELECTIVE_ONLY", "CASH_PRIORITY", "RESEARCH_ONLY")

# Breadth = % of evaluated symbols in an uptrend posture (price above key MAs).
_FULL_BREADTH = 70.0    # >= this % -> broad participation
_CASH_BREADTH = 25.0    # <= this % -> risk-off


def _is_uptrend(ind: dict) -> bool:
    """A symbol participates in the uptrend if price sits above EMA20/EMA50/SMA200
    and short-term momentum isn't negative. Mirrors the scanner's trend inputs."""
    px = ind.get("last_close")
    if px is None:
        return False
    above = 0
    checks = 0
    for key in ("ema_20", "ema_50", "sma_200"):
        ma = ind.get(key)
        if ma is not None:
            checks += 1
            if px > ma:
                above += 1
    if checks == 0:
        return False
    hist = ind.get("macd_hist")
    momentum_ok = hist is None or hist >= 0
    return above >= max(2, checks - 1) and momentum_ok


def evaluate(snapshots: dict[str, dict | None]) -> dict:
    """Classify the regime from {symbol: indicator_snapshot | None}.

    None values are provider/data failures — enough of them flips the gate to
    RESEARCH_ONLY (uncertain regime), not to a confident risk state.
    """
    symbols = list(snapshots.keys())
    missing = [s for s, ind in snapshots.items() if not ind]
    valid = {s: ind for s, ind in snapshots.items() if ind}
    evaluated = len(valid)

    reasons: list[str] = []
    allowed: list[str] = []
    blocked: list[str] = []

    if evaluated == 0 or evaluated <= len(symbols) // 2:
        if missing:
            reasons.append(f"data unavailable for {len(missing)}/{len(symbols)} symbols")
        else:
            reasons.append("no symbols to evaluate")
        return {
            "state": "RESEARCH_ONLY",
            "confidence": "low",
            "breadth_pct": None,
            "symbols_evaluated": evaluated,
            "reasons": reasons or ["insufficient data to classify regime"],
            "allowed_actions": ["research", "journaling"],
            "blocked_actions": ["new_candidates", "sizing"],
            "missing_data": missing,
        }

    uptrend = sum(1 for ind in valid.values() if _is_uptrend(ind))
    breadth = round(100.0 * uptrend / evaluated, 1)
    reasons.append(f"{uptrend}/{evaluated} symbols in uptrend posture (breadth {breadth}%)")

    # Momentum stress: how many are overbought (distribution risk).
    overbought = sum(1 for ind in valid.values()
                     if isinstance(ind.get("rsi_14"), (int, float)) and ind["rsi_14"] > 70)
    if overbought:
        reasons.append(f"{overbought} symbol(s) overbought (RSI>70) — distribution risk")

    if breadth >= _FULL_BREADTH:
        state = "FULL_RISK_ALLOWED"
        confidence = "high" if not overbought else "medium"
        allowed = ["new_candidates", "sizing", "full_participation"]
        blocked = []
        reasons.append("broad participation — full engagement permitted")
    elif breadth <= _CASH_BREADTH:
        state = "CASH_PRIORITY"
        confidence = "high"
        allowed = ["research", "journaling", "hedge_review"]
        blocked = ["new_candidates", "sizing"]
        reasons.append("deteriorating breadth — preserve capital, no new discretionary swing")
    else:
        state = "SELECTIVE_ONLY"
        confidence = "medium"
        allowed = ["strongest_setups_only", "reduced_size"]
        blocked = ["broad_participation"]
        reasons.append("mixed evidence — only strongest setups, smaller size")

    return {
        "state": state,
        "confidence": confidence,
        "breadth_pct": breadth,
        "symbols_evaluated": evaluated,
        "reasons": reasons,
        "allowed_actions": allowed,
        "blocked_actions": blocked,
        "missing_data": missing,
    }


def assess_universe(symbols: list[str], interval: str = "1h", candles: int = 300) -> dict:
    """Build snapshots for a universe via the data providers, then evaluate the gate.

    Provider failures for a symbol become None (counted as missing data) rather than
    raising, so a single bad symbol doesn't break the regime read.
    """
    snapshots: dict[str, dict | None] = {}
    for sym in symbols:
        try:
            df = get_provider(sym).fetch_klines(sym, interval, candles)
            snapshots[sym] = snapshot(df)
        except Exception:  # noqa: BLE001
            snapshots[sym] = None
    return evaluate(snapshots)


def evaluate_from_cards(cards: list[dict]) -> dict:
    """Evaluate the gate from scanner cards (no extra provider calls).

    Scanner cards carry lean/rsi_14/macd_hist but not the moving averages, so we map
    the card's rule-based lean to a synthetic trend posture: bullish counts as
    uptrend participation, bearish/neutral does not. Same breadth thresholds as
    evaluate(); failed cards count as missing data.
    """
    snapshots: dict[str, dict | None] = {}
    for c in cards:
        if not c.get("ok"):
            snapshots[c.get("symbol", "?")] = None
            continue
        # Synthesize just enough of an indicator dict for _is_uptrend(): a bullish
        # card implies price above the trend MAs with non-negative momentum.
        bullish = c.get("lean") == "bullish"
        px = 100.0
        snapshots[c.get("symbol", "?")] = {
            "last_close": px,
            "ema_20": 99.0 if bullish else 101.0,
            "ema_50": 98.0 if bullish else 102.0,
            "sma_200": 95.0 if bullish else 105.0,
            "rsi_14": c.get("rsi_14"),
            "macd_hist": c.get("macd_hist"),
        }
    return evaluate(snapshots)
