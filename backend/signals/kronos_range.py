"""Kronos range adapter — uses Kronos for what it's GOOD at: range/volatility.

Phase 0 backtest finding: Kronos has no directional edge (35%) but strong price
accuracy (MAPE ~2%). So we use it to forecast a probabilistic 24h RANGE — the
likely high/low band — NOT a buy/sell direction. This feeds risk sizing,
stop/target placement, and the explainability engine.

Model is loaded once and cached (module-level singleton).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_VENDOR = os.path.join(os.path.dirname(__file__), "..", "..", "vendor", "Kronos")
sys.path.insert(0, _VENDOR)

_predictor = None  # cached singleton


def _get_predictor():
    global _predictor
    if _predictor is None:
        from model import Kronos, KronosTokenizer, KronosPredictor
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
        _predictor = KronosPredictor(model, tokenizer, max_context=512, device="cpu")
    return _predictor


def forecast_range(
    df: pd.DataFrame,
    pred_len: int = 24,
    lookback: int = 360,
    sample_count: int = 5,
) -> dict:
    """Forecast a probabilistic price RANGE over the next `pred_len` periods.

    Args:
        df: OHLCV DataFrame with a 'timestamps' column + open/high/low/close/volume/amount.
        pred_len: forecast horizon (periods).
        lookback: history window fed to the model (<=512).
        sample_count: number of stochastic forecast paths to average (more = smoother band).

    Returns a dict describing the expected range — NOT a direction call.
    """
    df = df.reset_index(drop=True)
    lookback = min(lookback, len(df) - 1)
    cols = ["open", "high", "low", "close", "volume", "amount"]

    x_df = df.loc[len(df) - lookback:, cols].reset_index(drop=True)
    x_ts = df.loc[len(df) - lookback:, "timestamps"].reset_index(drop=True)

    # Build future timestamps by extending the last interval
    last_ts = pd.to_datetime(df["timestamps"].iloc[-1])
    interval = pd.to_datetime(df["timestamps"].iloc[-1]) - pd.to_datetime(df["timestamps"].iloc[-2])
    y_ts = pd.Series([last_ts + interval * (i + 1) for i in range(pred_len)])

    predictor = _get_predictor()
    pred = predictor.predict(
        df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
        pred_len=pred_len, T=1.0, top_p=0.9, sample_count=sample_count, verbose=False,
    ).reset_index(drop=True)

    last_close = float(df["close"].iloc[-1])
    highs = pred["high"].values
    lows = pred["low"].values
    closes = pred["close"].values

    band_low = float(np.percentile(lows, 10))
    band_high = float(np.percentile(highs, 90))
    expected_close = float(closes[-1])

    return {
        "last_close": round(last_close, 2),
        "horizon_periods": pred_len,
        "expected_band_low": round(band_low, 2),
        "expected_band_high": round(band_high, 2),
        "expected_close": round(expected_close, 2),
        "band_width_pct": round((band_high - band_low) / last_close * 100, 2),
        "note": "Kronos range forecast (volatility/level). NOT a direction signal.",
    }


if __name__ == "__main__":
    from backend.data.binance import fetch_klines
    import json
    df = fetch_klines("BTCUSDT", "1h", 400)
    print(json.dumps(forecast_range(df, pred_len=24, sample_count=3), indent=2))
