"""Signal scan engine — screen a watchlist fast, rank by a rule-based score.

Design (cost-aware): computing indicators is free; calling Opus is not. So the
scanner does a FAST rule-based technical score for every symbol (no LLM), ranks
them, and lets the user drill into any one for the full Copilot (Opus) analysis.

This is the foundation for the premium "AI Market Scanner" feature.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.data.providers import get_provider, asset_class
from backend.data.indicators import snapshot


def _score_from_indicators(ind: dict) -> tuple[str, int, list[str]]:
    """Rule-based lean + 0-100 conviction from indicators alone (no LLM).

    Transparent heuristic: combine trend (price vs MAs), momentum (MACD hist),
    and RSI posture into a directional score. This is a SCREEN, not the final call —
    the Copilot (Opus) gives the nuanced reasoned verdict on drill-in.
    """
    reasons: list[str] = []
    score = 0  # positive = bullish, negative = bearish
    px = ind.get("last_close")
    ema20, ema50 = ind.get("ema_20"), ind.get("ema_50")
    sma200 = ind.get("sma_200")
    rsi = ind.get("rsi_14")
    hist = ind.get("macd_hist")

    if px and ema20:
        if px > ema20 * 1.001:
            score += 1; reasons.append("price > EMA20")
        elif px < ema20 * 0.999:
            score -= 1; reasons.append("price < EMA20")
    if px and ema50:
        if px > ema50 * 1.001:
            score += 1
        elif px < ema50 * 0.999:
            score -= 1
    if px and sma200:
        if px > sma200 * 1.001:
            score += 1; reasons.append("above SMA200 (long-term up)")
        elif px < sma200 * 0.999:
            score -= 1; reasons.append("below SMA200 (long-term down)")
    if hist is not None:
        if hist > 0:
            score += 1; reasons.append("MACD momentum up")
        elif hist < 0:
            score -= 1; reasons.append("MACD momentum down")
    if rsi is not None:
        if rsi > 70:
            score -= 1; reasons.append(f"RSI {rsi} overbought")
        elif rsi < 30:
            score += 1; reasons.append(f"RSI {rsi} oversold")

    # Map score (-5..+5) to lean + conviction
    if score >= 2:
        lean = "bullish"
    elif score <= -2:
        lean = "bearish"
    else:
        lean = "neutral"
    conviction = min(100, abs(score) * 20)
    return lean, conviction, reasons


def screen_symbol(symbol: str, interval: str = "1h") -> dict:
    """Fast technical screen for one symbol (no LLM). Returns a compact card."""
    try:
        provider = get_provider(symbol)
        df = provider.fetch_klines(symbol, interval, 300)
        ind = snapshot(df)
        lean, conviction, reasons = _score_from_indicators(ind)
        return {
            "symbol": symbol,
            "asset_class": asset_class(symbol),
            "lean": lean,
            "conviction": conviction,
            "last_close": ind.get("last_close"),
            "rsi_14": ind.get("rsi_14"),
            "macd_hist": ind.get("macd_hist"),
            "reasons": reasons,
            "ok": True,
        }
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "error": str(e)[:120]}


def scan_watchlist(symbols: list[str], interval: str = "1h") -> list[dict]:
    """Screen many symbols in parallel, return ranked by conviction (strongest first)."""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(screen_symbol, s, interval): s for s in symbols}
        for fut in as_completed(futures):
            results.append(fut.result())

    def sort_key(r):
        if not r.get("ok"):
            return (-1, 0)
        # Rank by conviction; bullish and bearish both "interesting", neutral last
        weight = 0 if r["lean"] == "neutral" else 1
        return (weight, r["conviction"])

    results.sort(key=sort_key, reverse=True)
    return results


if __name__ == "__main__":
    import json
    wl = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
    print(json.dumps(scan_watchlist(wl), indent=2, default=str))
