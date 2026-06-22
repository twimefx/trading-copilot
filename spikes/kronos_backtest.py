"""
Phase 0 ROLLING BACKTEST: honest Kronos directional accuracy across many windows.

One forecast proves nothing. This walks Kronos across N non-overlapping-ish windows
of real BTC history and reports aggregate stats so we know if the forecasting edge
is real (>>50% directional) or noise (~50%).

Tests the question that decides the whole product:
    "Is Kronos meaningfully better than a coin flip on crypto direction?"

Honest by design: reports per-horizon directional hit-rate, final-direction hit-rate,
and MAPE, averaged over all windows. No cherry-picking.
"""
import sys
import os
import time
import urllib.request
import json
import pandas as pd
import numpy as np

KRONOS_DIR = os.path.join(os.path.dirname(__file__), "..", "vendor", "Kronos")
sys.path.insert(0, KRONOS_DIR)
from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402

LOOKBACK = 360
PRED_LEN = 24
N_WINDOWS = 40        # number of backtest windows
STEP = 12             # hours to slide between windows
SYMBOL = "BTCUSDT"
INTERVAL = "1h"


def fetch_klines(symbol, interval, limit):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode())
    return pd.DataFrame([{
        "timestamps": pd.to_datetime(k[0], unit="ms"),
        "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
        "close": float(k[4]), "volume": float(k[5]), "amount": float(k[7]),
    } for k in raw])


def main():
    # Need enough data for all windows: LOOKBACK + PRED_LEN + (N_WINDOWS-1)*STEP
    needed = LOOKBACK + PRED_LEN + (N_WINDOWS - 1) * STEP
    needed = min(needed, 1000)  # Binance cap
    print(f"Fetching {needed} {INTERVAL} {SYMBOL} candles...", flush=True)
    df = fetch_klines(SYMBOL, INTERVAL, needed).reset_index(drop=True)
    print(f"Got {len(df)} candles: {df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]}", flush=True)

    max_windows = (len(df) - LOOKBACK - PRED_LEN) // STEP + 1
    n_windows = min(N_WINDOWS, max_windows)
    print(f"Running {n_windows} backtest windows (lookback={LOOKBACK}, horizon={PRED_LEN}h)...", flush=True)

    print("Loading Kronos-small...", flush=True)
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512, device="cpu")

    final_dir_hits = 0
    perhour_hits = 0
    perhour_total = 0
    mapes = []
    t0 = time.time()

    for w in range(n_windows):
        start = w * STEP
        h_end = start + LOOKBACK
        f_end = h_end + PRED_LEN
        if f_end > len(df):
            break

        x_df = df.loc[start:h_end - 1, ["open", "high", "low", "close", "volume", "amount"]]
        x_ts = df.loc[start:h_end - 1, "timestamps"]
        y_ts = df.loc[h_end:f_end - 1, "timestamps"]
        truth = df.loc[h_end:f_end - 1].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        pred = predictor.predict(df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                                 pred_len=PRED_LEN, T=1.0, top_p=0.9,
                                 sample_count=1, verbose=False).reset_index(drop=True)

        p_close = pred["close"].values
        t_close = truth["close"].values

        # final-direction
        if (p_close[-1] > last_close) == (t_close[-1] > last_close):
            final_dir_hits += 1

        # per-hour direction (step-over-step), walking forward on truth
        prev = last_close
        for i in range(PRED_LEN):
            if (p_close[i] > prev) == (t_close[i] > prev):
                perhour_hits += 1
            perhour_total += 1
            prev = t_close[i]

        mapes.append(np.mean(np.abs((p_close - t_close) / t_close)) * 100)
        elapsed = time.time() - t0
        print(f"  window {w+1}/{n_windows} done  (elapsed {elapsed:.0f}s, "
              f"running final-dir {final_dir_hits}/{w+1})", flush=True)

    print("\n" + "=" * 56, flush=True)
    print("KRONOS ROLLING BACKTEST — HONEST AGGREGATE RESULTS", flush=True)
    print("=" * 56, flush=True)
    print(f"Windows tested        | {len(mapes)}", flush=True)
    print(f"Final-direction acc   | {final_dir_hits/len(mapes)*100:.1f}%  "
          f"({final_dir_hits}/{len(mapes)})   [50% = coin flip]", flush=True)
    print(f"Per-hour direction acc| {perhour_hits/perhour_total*100:.1f}%  "
          f"({perhour_hits}/{perhour_total})", flush=True)
    print(f"Mean price MAPE       | {np.mean(mapes):.2f}%   (median {np.median(mapes):.2f}%)", flush=True)
    print("=" * 56, flush=True)
    print("\nINTERPRETATION:", flush=True)
    print(" ~50% = no directional edge (don't bet the product on raw signals)", flush=True)
    print(" 55-60% = modest edge worth combining with risk mgmt + reasoning layer", flush=True)
    print(" >60% = strong; validate harder before believing it", flush=True)
    print("\nThis is base Kronos-small, zero fine-tuning. Fine-tuning on crypto", flush=True)
    print("(finetune scripts exist in vendor/Kronos/finetune) may improve it.", flush=True)


if __name__ == "__main__":
    main()
