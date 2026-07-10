"""Crypto derivatives snapshot via CoinGecko (cloud-IP-friendly fallback).

Both Binance futures (451) and Bybit (403) geo-block datacenter IPs like Railway's,
which kills the crypto Institutional Flow dashboard. CoinGecko's public derivatives
endpoint is NOT geo-blocked on those IPs and exposes the current funding rate and
open interest per exchange.

LIMITATION vs the exchange APIs: CoinGecko gives a point-in-time SNAPSHOT, not a
historical series. So this fallback can populate:
  - funding regime (from the current rate) — the single most useful positioning signal
  - current open interest (as a one-point series; trend shows 'flat', honestly)
It CANNOT provide funding/OI trend over time or the long/short account ratio —
those stay unavailable (the dashboard degrades per-stream, never fabricates a trend).

Functions mirror the binance.py return shapes so flow.py uses them interchangeably.
"""
from __future__ import annotations

import json
import os
import urllib.request

_BASE = os.environ.get("COINGECKO_BASE", "https://api.coingecko.com/api/v3")
# Optional Pro key (higher rate limits); falls back to the free public endpoint.
_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()

# Which derivatives market to read (CoinGecko labels Binance perps this way).
_PREFERRED_MARKETS = ("Binance (Futures)", "Bybit (Futures)", "OKX (Futures)")


def _get_json(url: str):
    headers = {"User-Agent": "trading-copilot/0.1"}
    if _API_KEY:
        headers["x-cg-demo-api-key"] = _API_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _snapshot(symbol: str) -> dict | None:
    """Return the best available derivatives snapshot for `symbol` (e.g. BTCUSDT)."""
    base = symbol.upper().replace("USDT", "").replace("USD", "").replace("PERP", "")
    data = _get_json(f"{_BASE}/derivatives?include_tickers=unexpired")
    # Prefer a major venue; match on base asset + perpetual contract.
    candidates = [
        t for t in data
        if t.get("contract_type") == "perpetual"
        and t.get("symbol", "").upper().replace("_", "").startswith(base)
    ]
    if not candidates:
        return None
    for mkt in _PREFERRED_MARKETS:
        for t in candidates:
            if t.get("market") == mkt:
                return t
    return candidates[0]


def fetch_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Current perp funding rate from CoinGecko."""
    try:
        snap = _snapshot(symbol)
        if not snap or snap.get("funding_rate") is None:
            return {"funding_rate": None, "available": False}
        # CoinGecko reports funding_rate as a percentage already (e.g. 0.009 = 0.009%).
        rate = float(snap["funding_rate"]) / 100.0
        return {"funding_rate": rate,
                "funding_rate_pct": round(float(snap["funding_rate"]), 4),
                "available": True, "source": "coingecko"}
    except Exception as e:  # noqa: BLE001
        return {"funding_rate": None, "available": False, "error": str(e)[:120]}


def fetch_open_interest(symbol: str = "BTCUSDT") -> dict:
    """Current open interest (USD) from CoinGecko."""
    try:
        snap = _snapshot(symbol)
        if not snap or snap.get("open_interest") is None:
            return {"open_interest": None, "available": False}
        return {"open_interest": float(snap["open_interest"]),
                "available": True, "source": "coingecko"}
    except Exception as e:  # noqa: BLE001
        return {"open_interest": None, "available": False, "error": str(e)[:120]}


def fetch_funding_history(symbol: str = "BTCUSDT", limit: int = 30) -> dict:
    """Funding as a ONE-POINT series (CoinGecko has no history).

    Enough for flow.py to compute the funding *regime* (crowded long/short) from the
    current rate. Trend will read 'flat' (single point) — honest, not fabricated.
    """
    try:
        snap = _snapshot(symbol)
        if not snap or snap.get("funding_rate") is None:
            return {"series": [], "available": False}
        rate = float(snap["funding_rate"]) / 100.0
        return {"series": [{"time": 0, "rate": rate}],
                "available": True, "source": "coingecko", "snapshot_only": True}
    except Exception as e:  # noqa: BLE001
        return {"series": [], "available": False, "error": str(e)[:120]}


def fetch_oi_history(symbol: str = "BTCUSDT", period: str = "1h", limit: int = 30) -> dict:
    """Open interest as a ONE-POINT series (CoinGecko has no history)."""
    try:
        snap = _snapshot(symbol)
        if not snap or snap.get("open_interest") is None:
            return {"series": [], "available": False}
        oi = float(snap["open_interest"])
        return {"series": [{"time": 0, "oi": oi, "oi_value": oi}],
                "available": True, "source": "coingecko", "snapshot_only": True}
    except Exception as e:  # noqa: BLE001
        return {"series": [], "available": False, "error": str(e)[:120]}


if __name__ == "__main__":
    print("funding:", fetch_funding_rate("BTCUSDT"))
    print("oi:", fetch_open_interest("BTCUSDT"))
    print("funding_hist:", fetch_funding_history("BTCUSDT"))
