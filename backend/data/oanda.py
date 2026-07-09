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
import urllib.error
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


# Curated forex universe for the scanner / symbol pickers: majors, key crosses,
# and metals. Oanda underscore form. Kept small and liquid on purpose.
FOREX_MAJORS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD"]
FOREX_CROSSES = ["EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_AUD"]
FOREX_METALS = ["XAU_USD", "XAG_USD"]
FOREX_UNIVERSE = FOREX_MAJORS + FOREX_CROSSES + FOREX_METALS


def fetch_position_book(symbol: str = "EUR_USD") -> dict:
    """Retail positioning from Oanda's position book (long/short % across price buckets).

    This is the forex analog of a crypto exchange's long/short ratio — it shows
    where the retail crowd is positioned and where they're offside relative to price.

    NOTE: this endpoint requires a token with trading-account authorization. If the
    token lacks it (HTTP 401) or the instrument has no book, we degrade gracefully to
    {available: False} — the flow dashboard still works from price/volatility data,
    and this section lights up automatically once a book-scoped token is supplied.
    """
    instrument = normalize_instrument(symbol)
    url = f"{_base_url()}/v3/instruments/{instrument}/positionBook"
    try:
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:  # 401 (no book scope), 404, etc.
        return {"available": False, "note": f"position book unavailable (HTTP {e.code})"}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "note": f"position book error: {str(e)[:80]}"}

    pb = data.get("positionBook") or {}
    buckets = pb.get("buckets") or []
    if not buckets:
        return {"available": False, "note": "empty position book"}

    price = float(pb.get("price") or 0) or None
    long_pct = sum(float(b.get("longCountPercent", 0)) for b in buckets)
    short_pct = sum(float(b.get("shortCountPercent", 0)) for b in buckets)
    total = long_pct + short_pct
    # Normalize to a clean long/short split + a ratio comparable to crypto L/S.
    long_share = round(long_pct / total, 4) if total else None
    ratio = round(long_pct / short_pct, 3) if short_pct else None

    # How much of the crowd is offside (positioned the wrong side of current price):
    # longs below price are in profit, longs above price are underwater, etc.
    longs_underwater = shorts_underwater = 0.0
    if price is not None:
        for b in buckets:
            bp = float(b.get("price", 0))
            lp = float(b.get("longCountPercent", 0))
            sp = float(b.get("shortCountPercent", 0))
            if bp > price:      # entries above current price
                longs_underwater += lp   # longs entered higher -> underwater
            elif bp < price:    # entries below current price
                shorts_underwater += sp  # shorts entered lower -> underwater

    return {
        "available": True,
        "price": price,
        "time": pb.get("time"),
        "long_pct": round(long_pct, 2),
        "short_pct": round(short_pct, 2),
        "long_share": long_share,
        "ratio": ratio,
        "longs_underwater_pct": round(longs_underwater, 2),
        "shorts_underwater_pct": round(shorts_underwater, 2),
    }


if __name__ == "__main__":
    df = fetch_klines("EUR_USD", "1h", 5)
    print(df.to_string(index=False))
