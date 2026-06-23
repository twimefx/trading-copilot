"""Forex market data via Oanda v20 REST API.

Mirrors the Binance module's interface (returns the same OHLCV DataFrame shape)
so the Copilot / MarketContext work unchanged across crypto and forex.

Requires env vars (free Oanda *practice* account works):
    OANDA_API_TOKEN   - personal access token
    OANDA_ENV         - "practice" (default) or "live"

Oanda specifics handled here:
    - Instruments use underscore format: EUR_USD, GBP_JPY, XAU_USD
    - Candles come as {time, mid:{o,h,l,c}, volume} (tick volume, not notional)
    - No funding/open-interest concept in spot FX (returns unavailable)
"""
from __future__ import annotations

import json
import os
import urllib.request

import pandas as pd

_PRACTICE = "https://api-fxpractice.oanda.com"
_LIVE = "https://api-fxtrade.oanda.com"

# Oanda granularity codes mapped from our interval strings.
_GRAN = {"15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}


def _base_url() -> str:
    return _LIVE if os.environ.get("OANDA_ENV", "practice").lower() == "live" else _PRACTICE


def _headers() -> dict:
    token = os.environ.get("OANDA_API_TOKEN")
    if not token:
        raise RuntimeError("OANDA_API_TOKEN not set (free practice account at oanda.com)")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def normalize_instrument(symbol: str) -> str:
    """Accept EURUSD / EUR_USD / eur-usd and return Oanda's EUR_USD form."""
    s = symbol.upper().replace("-", "").replace("_", "").replace("/", "")
    if len(s) == 6:
        return f"{s[:3]}_{s[3:]}"
    return symbol.upper()  # already has separator or non-standard


def fetch_klines(symbol: str = "EUR_USD", interval: str = "1h", limit: int = 400) -> pd.DataFrame:
    """Fetch OHLCV candles from Oanda. Returns same columns as the Binance module."""
    instrument = normalize_instrument(symbol)
    gran = _GRAN.get(interval, "H1")
    url = (f"{_base_url()}/v3/instruments/{instrument}/candles"
           f"?count={min(limit, 5000)}&granularity={gran}&price=M")
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    rows = []
    for c in data.get("candles", []):
        if not c.get("complete", True):
            continue
        mid = c["mid"]
        vol = float(c.get("volume", 0))
        rows.append({
            "timestamps": pd.to_datetime(c["time"]).tz_localize(None),
            "open": float(mid["o"]), "high": float(mid["h"]),
            "low": float(mid["l"]), "close": float(mid["c"]),
            "volume": vol, "amount": vol,  # FX has tick volume only
        })
    return pd.DataFrame(rows, columns=["timestamps", "open", "high", "low", "close", "volume", "amount"])


def fetch_funding_rate(symbol: str = "EUR_USD") -> dict:
    """Spot FX has no perp funding. Return unavailable (Copilot handles gracefully)."""
    return {"funding_rate": None, "available": False, "note": "n/a for spot forex"}


def fetch_open_interest(symbol: str = "EUR_USD") -> dict:
    """Spot FX has no centralized open interest. Return unavailable."""
    return {"open_interest": None, "available": False, "note": "n/a for spot forex"}


if __name__ == "__main__":
    df = fetch_klines("EUR_USD", "1h", 5)
    print(df.to_string(index=False))
