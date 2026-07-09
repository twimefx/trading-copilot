"""Technical indicators — pure pandas/numpy, no native deps.

These feed the reasoning layer with the technical picture Kronos can't provide
(Kronos forecasts range/volatility, not momentum/trend signals).
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=n, adjust=False).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/n
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    # When avg_loss == 0, RSI is 100
    out = out.where(avg_loss != 0, 100.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line, histogram."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average True Range — volatility measure."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def volume_trend(volume: pd.Series, n: int = 20) -> float:
    """Ratio of recent avg volume to longer avg volume. >1 = volume rising."""
    if len(volume) < n * 2:
        return float("nan")
    recent = volume.iloc[-n:].mean()
    base = volume.iloc[-n * 2:-n].mean()
    return float(recent / base) if base else float("nan")


def _price_dp(value: float) -> int:
    """Decimal places for a price-level field, scaled to magnitude.

    Crypto majors (thousands) need 2dp; forex (~1.14) and sub-dollar pairs need
    more, or rounding silently zeroes small but real values. Critically, ATR for
    EURUSD is ~0.0008 — round(…, 2) == 0.0, which collapses the volatility band.
    """
    v = abs(value)
    if v >= 100:
        return 2
    if v >= 1:
        return 5      # FX majors: 1.14375 — enough for ATR ~0.0008 to survive
    if v >= 0.01:
        return 6
    return 8


def snapshot(df: pd.DataFrame) -> dict:
    """Compute a compact indicator snapshot from an OHLCV DataFrame.

    Expects columns: open, high, low, close, volume. Returns latest values.
    """
    close = df["close"]
    rsi14 = rsi(close, 14)
    macd_df = macd(close)
    atr14 = atr(df["high"], df["low"], close, 14)
    last_close = float(close.iloc[-1])
    atr_val = float(atr14.iloc[-1])
    # Price-level fields round with magnitude-aware precision so forex/metals
    # values aren't silently zeroed (ATR) or flattened (EMAs) by fixed 2dp.
    pdp = _price_dp(last_close)
    # ATR is a (usually small) delta, not an absolute price — give it precision
    # based on its own magnitude so a ~0.0008 FX ATR is preserved, not rounded to 0.
    adp = max(pdp, _price_dp(atr_val))
    return {
        "last_close": round(last_close, pdp),
        "rsi_14": round(float(rsi14.iloc[-1]), 2),
        "macd": round(float(macd_df["macd"].iloc[-1]), 6),
        "macd_signal": round(float(macd_df["signal"].iloc[-1]), 6),
        "macd_hist": round(float(macd_df["hist"].iloc[-1]), 6),
        "ema_20": round(float(ema(close, 20).iloc[-1]), pdp),
        "ema_50": round(float(ema(close, 50).iloc[-1]), pdp),
        "sma_200": round(float(sma(close, 200).iloc[-1]), pdp) if len(close) >= 200 else None,
        "atr_14": round(atr_val, adp),
        "atr_pct": round(atr_val / last_close * 100, 2) if last_close else None,
        "volume_trend": round(volume_trend(df["volume"]), 3),
    }


def _swings(highs: list[float], lows: list[float], k: int = 2) -> tuple[list[float], list[float]]:
    """Fractal swing pivots: a bar is a swing high if its high is >= the `k` bars
    on each side (and symmetric for lows). Deterministic, no lookahead beyond the
    fixed window. Returns (swing_highs, swing_lows) as price levels."""
    sh, sl = [], []
    n = len(highs)
    for i in range(k, n - k):
        window_h = highs[i - k:i + k + 1]
        window_l = lows[i - k:i + k + 1]
        if highs[i] == max(window_h):
            sh.append(highs[i])
        if lows[i] == min(window_l):
            sl.append(lows[i])
    return sh, sl


def price_structure(df: pd.DataFrame, lookback: int = 60, recent_bars: int = 12) -> dict:
    """Deterministic price-action / structure summary for the reasoning layer.

    Gives price-action and chart-pattern agents REAL structure to reason over
    (swings, support/resistance, position within range, recent candles) instead
    of a single latest-bar snapshot — so they ground claims, not hallucinate
    patterns. Pure pandas; no LLM.

    Expects columns: open, high, low, close, volume.
    """
    tail = df.tail(lookback).reset_index(drop=True)
    if len(tail) < 5:
        return {"available": False, "note": "insufficient bars for structure"}

    highs = [float(x) for x in tail["high"].tolist()]
    lows = [float(x) for x in tail["low"].tolist()]
    closes = [float(x) for x in tail["close"].tolist()]
    opens = [float(x) for x in tail["open"].tolist()]
    last = closes[-1]

    period_high = max(highs)
    period_low = min(lows)
    rng = period_high - period_low
    # Where price sits within the lookback range: 0 = at lows, 100 = at highs.
    pos_in_range = round((last - period_low) / rng * 100, 1) if rng else None

    sh, sl = _swings(highs, lows, k=2)
    dp = _price_dp(last)

    # Nearest resistance above / support below the current price, from swing levels.
    res_above = sorted([h for h in sh if h > last])
    sup_below = sorted([l for l in sl if l < last], reverse=True)
    nearest_res = round(res_above[0], dp) if res_above else round(period_high, dp)
    nearest_sup = round(sup_below[0], dp) if sup_below else round(period_low, dp)

    # Swing-structure read: compare the last two swing highs / lows for HH/HL vs LH/LL.
    def _trend_of(levels: list[float]) -> str:
        if len(levels) < 2:
            return "flat"
        return "up" if levels[-1] > levels[-2] else "down" if levels[-1] < levels[-2] else "flat"
    sh_trend, sl_trend = _trend_of(sh), _trend_of(sl)
    if sh_trend == "up" and sl_trend == "up":
        structure = "uptrend (higher highs & higher lows)"
    elif sh_trend == "down" and sl_trend == "down":
        structure = "downtrend (lower highs & lower lows)"
    else:
        structure = "range/mixed structure"

    # Compact recent-candle summary the pattern agent can eyeball.
    rb = tail.tail(recent_bars)
    recent = []
    for o, h, l, c in zip(rb["open"], rb["high"], rb["low"], rb["close"]):
        o, h, l, c = float(o), float(h), float(l), float(c)
        body = c - o
        rng_bar = h - l
        recent.append({
            "o": round(o, dp), "h": round(h, dp), "l": round(l, dp), "c": round(c, dp),
            "dir": "up" if body > 0 else "down" if body < 0 else "flat",
            "body_pct": round(abs(body) / rng_bar * 100, 1) if rng_bar else 0.0,
        })

    return {
        "available": True,
        "lookback_bars": len(tail),
        "period_high": round(period_high, dp),
        "period_low": round(period_low, dp),
        "position_in_range_pct": pos_in_range,
        "nearest_resistance": nearest_res,
        "nearest_support": nearest_sup,
        "dist_to_resistance_pct": round((nearest_res - last) / last * 100, 2) if last else None,
        "dist_to_support_pct": round((last - nearest_sup) / last * 100, 2) if last else None,
        "swing_highs": [round(x, dp) for x in sh[-4:]],
        "swing_lows": [round(x, dp) for x in sl[-4:]],
        "structure": structure,
        "recent_candles": recent,
    }
