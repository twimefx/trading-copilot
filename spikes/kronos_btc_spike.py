"""
Phase 0 SPIKE: Validate Kronos forecasting on REAL BTC/USDT data.

Goal: prove the forecasting core works end-to-end before building a product on it.
- Fetch real hourly BTC/USDT candles from Binance public API (no key needed)
- Hold out the last PRED_LEN hours as ground truth
- Forecast them with Kronos-small
- Report directional accuracy + price error HONESTLY

This is a throwaway validation script, not production code.
"""
import sys
import os
import urllib.request
import json
import pandas as pd
import numpy as np

# Make Kronos importable
KRONOS_DIR = os.path.join(os.path.dirname(__file__), "..", "vendor", "Kronos")
sys.path.insert(0, KRONOS_DIR)

from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402

LOOKBACK = 360      # hours of history fed to model (<=512 context)
PRED_LEN = 24       # forecast horizon (next 24h)
SYMBOL = "BTCUSDT"
INTERVAL = "1h"


def fetch_binance_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Fetch real OHLCV candles from Binance public REST API."""
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode())
    rows = []
    for k in raw:
        rows.append({
            "timestamps": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "amount": float(k[7]),  # quote asset volume
        })
    return pd.DataFrame(rows)


def main():
    total = LOOKBACK + PRED_LEN
    print(f"Fetching {total} {INTERVAL} candles of {SYMBOL} from Binance...")
    df = fetch_binance_klines(SYMBOL, INTERVAL, total)
    print(f"Got {len(df)} candles. Range: {df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]}")

    print("\nLoading Kronos-small + tokenizer from HuggingFace (first run downloads ~100MB)...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512, device="cpu")

    # Split: history vs held-out ground truth
    x_df = df.loc[:LOOKBACK - 1, ["open", "high", "low", "close", "volume", "amount"]]
    x_timestamp = df.loc[:LOOKBACK - 1, "timestamps"]
    y_timestamp = df.loc[LOOKBACK:LOOKBACK + PRED_LEN - 1, "timestamps"]
    truth = df.loc[LOOKBACK:LOOKBACK + PRED_LEN - 1].reset_index(drop=True)

    last_close = float(x_df["close"].iloc[-1])
    print(f"\nLast known close (T0): {last_close:,.2f}")
    print(f"Forecasting next {PRED_LEN}h... (CPU, may take 1-3 min)")

    pred_df = predictor.predict(
        df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
        pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False,
    ).reset_index(drop=True)

    pred_final = float(pred_df["close"].iloc[-1])
    truth_final = float(truth["close"].iloc[-1])

    pred_dir = "UP" if pred_final > last_close else "DOWN"
    truth_dir = "UP" if truth_final > last_close else "DOWN"

    # Hour-by-hour directional accuracy
    pred_steps = pred_df["close"].values
    truth_steps = truth["close"].values
    prev = last_close
    correct = 0
    for i in range(PRED_LEN):
        pd_dir = pred_steps[i] > prev
        td_dir = truth_steps[i] > prev
        if pd_dir == td_dir:
            correct += 1
        prev = truth_steps[i]  # walk forward on truth
    dir_acc = correct / PRED_LEN * 100

    mape = np.mean(np.abs((pred_steps - truth_steps) / truth_steps)) * 100

    print("\n" + "=" * 50)
    print("KRONOS SPIKE RESULTS (honest)")
    print("=" * 50)
    print(f"Horizon final close  | predicted {pred_final:,.2f}  vs truth {truth_final:,.2f}")
    print(f"Overall direction    | predicted {pred_dir}  vs truth {truth_dir}  -> "
          f"{'CORRECT' if pred_dir == truth_dir else 'WRONG'}")
    print(f"Per-hour dir accuracy| {dir_acc:.1f}%  ({correct}/{PRED_LEN})  [50% = coin flip]")
    print(f"Price MAPE           | {mape:.2f}%  (avg % error of forecast vs actual)")
    print("=" * 50)
    print("\nNOTE: one sample proves the PIPELINE works, not profitability.")
    print("Real validation = rolling backtest over hundreds of windows (Phase 0 later).")


if __name__ == "__main__":
    main()
