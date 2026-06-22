"""Market data ingestion — crypto (Binance public API, no key required).

Reusable module used by the signal engine and backtester.
Forex (Oanda) ingestion lands in Phase 1.
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

BINANCE_BASE = "https://api.binance.com/api/v3/klines"

# Binance returns max 1000 candles per request.
_MAX_LIMIT = 1000

_OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch recent OHLCV candles from Binance.

    Returns a DataFrame with columns:
        timestamps (datetime), open, high, low, close, volume, amount

    `amount` is Binance "quote asset volume" — Kronos accepts it as an optional column.
    """
    if limit > _MAX_LIMIT:
        raise ValueError(f"limit must be <= {_MAX_LIMIT} (Binance cap); got {limit}")

    url = f"{BINANCE_BASE}?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode())

    rows = [
        {
            "timestamps": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "amount": float(k[7]),
        }
        for k in raw
    ]
    df = pd.DataFrame(rows, columns=["timestamps", *_OHLCV_COLS])
    return df


if __name__ == "__main__":
    df = fetch_klines("BTCUSDT", "1h", 5)
    print(df.to_string(index=False))
