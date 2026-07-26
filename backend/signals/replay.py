"""Market Replay — run the Copilot/Debate as of a historical moment.

The LLM never sees candles after `as_of`: the indicator snapshot is computed on
the truncated frame, and positioning (funding/OI) is honestly marked unavailable
(no reliable free history from cloud IPs). The outcome pass is pure pandas math
on the forward window — no model narration, no hindsight bias.
"""
from __future__ import annotations

import pandas as pd

from backend.data.providers import get_provider, asset_class
from backend.data.indicators import snapshot, price_structure
from backend.signals.context import MarketContext

_INTERVAL_MS = {"15m": 15 * 60_000, "1h": 3_600_000, "4h": 4 * 3_600_000, "1d": 86_400_000}
# Candles of context the indicators need before as_of (matches live's 400).
_LOOKBACK = 400
OUTCOME_PERIODS = 24


def interval_ms(interval: str) -> int:
    return _INTERVAL_MS.get(interval, 3_600_000)


def _fetch_window(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    provider = get_provider(symbol)
    return provider.fetch_klines_range(symbol, interval, start_ms, end_ms)


def build_replay_context(symbol: str, interval: str, as_of_s: int,
                         include_kronos: bool = True) -> MarketContext:
    """MarketContext as it would have looked at `as_of_s` (epoch seconds)."""
    step = interval_ms(interval)
    end_ms = as_of_s * 1000
    start_ms = end_ms - _LOOKBACK * step
    df = _fetch_window(symbol, interval, start_ms, end_ms)
    df = df[df["timestamps"] <= pd.to_datetime(end_ms, unit="ms")]
    if len(df) < 60:
        raise ValueError(f"Not enough history before {as_of_s} for {symbol} ({len(df)} candles).")

    ctx = MarketContext(
        symbol=symbol,
        interval=interval,
        asset_class=asset_class(symbol),
        indicators=snapshot(df),
        funding={"available": False, "note": "unavailable for historical replay"},
        open_interest={"available": False, "note": "unavailable for historical replay"},
        structure=price_structure(df),
    )
    if include_kronos:
        from backend.signals.context import _fetch_kronos_range
        ctx.kronos_range = _fetch_kronos_range(df)
    return ctx


def fetch_outcome(symbol: str, interval: str, as_of_s: int,
                  periods: int = OUTCOME_PERIODS) -> pd.DataFrame:
    """Candles AFTER as_of (the honest 'what happened next' window)."""
    step = interval_ms(interval)
    start_ms = as_of_s * 1000 + step  # strictly after as_of
    end_ms = start_ms + periods * step
    return _fetch_window(symbol, interval, start_ms, end_ms)


def score_outcome(entry_price: float | None, lean: str | None, outcome_df) -> dict:
    """Deterministic replay verdict, mirroring history.resolve_pending logic."""
    if outcome_df is None or len(outcome_df) == 0 or entry_price is None:
        return {"available": False, "note": "outcome window not yet elapsed"}
    final = float(outcome_df["close"].iloc[-1])
    hi = float(outcome_df["high"].max())
    lo = float(outcome_df["low"].min())
    move_pct = round((final - entry_price) / entry_price * 100, 2)
    if lean == "bullish":
        verdict = "correct" if final > entry_price else "incorrect"
    elif lean == "bearish":
        verdict = "correct" if final < entry_price else "incorrect"
    else:
        verdict = "flat"
    return {
        "available": True,
        "entry_price": entry_price,
        "final_close": final,
        "move_pct": move_pct,
        "max_excursion_up_pct": round((hi - entry_price) / entry_price * 100, 2),
        "max_excursion_down_pct": round((lo - entry_price) / entry_price * 100, 2),
        "verdict": verdict,
        "periods": len(outcome_df),
    }
