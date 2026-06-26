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
