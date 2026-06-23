"""Market data ingestion — crypto (Binance public API, no key required).

Reusable module used by the signal engine and backtester.
Forex (Oanda) ingestion lands in Phase 1.
"""
from __future__ import annotations

import json
import os
import urllib.request

import pandas as pd

# Spot klines: default to Binance's public data mirror (data-api.binance.vision),
# which serves the identical schema and is NOT geo-blocked on many cloud IPs
# (api.binance.com returns HTTP 451 from e.g. Railway). Override with BINANCE_BASE
# if you proxy/region-shift. Trailing path kept identical to the spot API.
BINANCE_BASE = os.environ.get(
    "BINANCE_BASE", "https://data-api.binance.vision/api/v3/klines"
)
# Futures (perp) endpoints for funding rate + open interest — sentiment/positioning.
# No public .vision mirror exists; these may 451 on blocked IPs but fail GRACEFULLY
# (callers return {"available": False}), so the Copilot still works without them.
_FUTURES_BASE = os.environ.get("BINANCE_FUTURES_BASE", "https://fapi.binance.com")
FUTURES_FUNDING = f"{_FUTURES_BASE}/fapi/v1/fundingRate"
FUTURES_OI = f"{_FUTURES_BASE}/fapi/v1/openInterest"
FUTURES_PREMIUM = f"{_FUTURES_BASE}/fapi/v1/premiumIndex"

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


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Latest perp funding rate. Positive = longs pay shorts (bullish crowding)."""
    try:
        data = _get_json(f"{FUTURES_FUNDING}?symbol={symbol}&limit=1")
        if not data:
            return {"funding_rate": None, "available": False}
        latest = data[-1]
        return {
            "funding_rate": float(latest["fundingRate"]),
            "funding_rate_pct": round(float(latest["fundingRate"]) * 100, 4),
            "available": True,
        }
    except Exception as e:  # noqa: BLE001
        return {"funding_rate": None, "available": False, "error": str(e)[:120]}


def fetch_open_interest(symbol: str = "BTCUSDT") -> dict:
    """Current open interest (total open perp contracts). Rising OI = new money."""
    try:
        data = _get_json(f"{FUTURES_OI}?symbol={symbol}")
        return {"open_interest": float(data["openInterest"]), "available": True}
    except Exception as e:  # noqa: BLE001
        return {"open_interest": None, "available": False, "error": str(e)[:120]}


if __name__ == "__main__":
    df = fetch_klines("BTCUSDT", "1h", 5)
    print(df.to_string(index=False))
    print("Funding:", fetch_funding_rate("BTCUSDT"))
    print("OI:", fetch_open_interest("BTCUSDT"))
