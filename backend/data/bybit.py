"""Crypto derivatives data via Bybit v5 (fallback when Binance futures geo-block).

Binance's `fapi.binance.com` returns HTTP 451 from many cloud/datacenter IPs
(e.g. Railway), which kills the Institutional Flow dashboard for crypto. Bybit's
v5 public market API serves the same class of positioning/flow data and is not
geo-blocked on those IPs.

Each function returns the SAME dict shape as the matching `binance.py` function,
so `flow.py` can use either interchangeably. All degrade to
`{"available": False}` on any error (never raise).

Bybit v5 endpoints used (public, no key):
  /v5/market/funding/history      funding-rate history
  /v5/market/open-interest        open-interest history
  /v5/market/account-ratio        long/short account ratio (crowd positioning)
  /v5/market/tickers              latest funding + mark (for point-in-time fetches)

Note: Bybit has no direct taker buy/sell *volume* ratio endpoint equivalent to
Binance's takerlongshortRatio, so `fetch_taker_ratio` is intentionally absent —
that single stream stays unavailable under the Bybit fallback (3 of 4 streams,
vs 0 when Binance is blocked).
"""
from __future__ import annotations

import json
import os
import urllib.request

_BASE = os.environ.get("BYBIT_BASE", "https://api.bybit.com")
_CATEGORY = "linear"  # USDT perpetuals

# Map our interval strings to Bybit's intervalTime / period params.
_OI_INTERVAL = {"5m": "5min", "15m": "15min", "30m": "30min",
                "1h": "1h", "4h": "4h", "1d": "1d"}
_RATIO_PERIOD = {"5m": "5min", "15m": "15min", "30m": "30min",
                 "1h": "1h", "4h": "4h", "1d": "1d"}


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _rows(data: dict) -> list:
    """Bybit wraps payloads in {retCode, result:{list:[...]}}. Raise on API error."""
    if data.get("retCode") != 0:
        raise RuntimeError(f"bybit retCode={data.get('retCode')}: {data.get('retMsg')}")
    return (data.get("result") or {}).get("list") or []


def fetch_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Latest perp funding rate. Positive = longs pay shorts (bullish crowding)."""
    try:
        rows = _rows(_get_json(
            f"{_BASE}/v5/market/tickers?category={_CATEGORY}&symbol={symbol}"))
        if not rows:
            return {"funding_rate": None, "available": False}
        rate = float(rows[0]["fundingRate"])
        return {"funding_rate": rate,
                "funding_rate_pct": round(rate * 100, 4),
                "available": True, "source": "bybit"}
    except Exception as e:  # noqa: BLE001
        return {"funding_rate": None, "available": False, "error": str(e)[:120]}


def fetch_open_interest(symbol: str = "BTCUSDT") -> dict:
    """Current open interest (latest bucket). Rising OI = new money."""
    try:
        rows = _rows(_get_json(
            f"{_BASE}/v5/market/open-interest?category={_CATEGORY}"
            f"&symbol={symbol}&intervalTime=1h&limit=1"))
        if not rows:
            return {"open_interest": None, "available": False}
        return {"open_interest": float(rows[0]["openInterest"]),
                "available": True, "source": "bybit"}
    except Exception as e:  # noqa: BLE001
        return {"open_interest": None, "available": False, "error": str(e)[:120]}


def fetch_funding_history(symbol: str = "BTCUSDT", limit: int = 30) -> dict:
    """Recent funding-rate history — is crowding building or fading?"""
    try:
        rows = _rows(_get_json(
            f"{_BASE}/v5/market/funding/history?category={_CATEGORY}"
            f"&symbol={symbol}&limit={int(limit)}"))
        # Bybit returns newest-first; reverse to oldest-first to match Binance.
        series = [
            {"time": int(d["fundingRateTimestamp"]), "rate": float(d["fundingRate"])}
            for d in reversed(rows)
        ]
        return {"series": series, "available": bool(series), "source": "bybit"}
    except Exception as e:  # noqa: BLE001
        return {"series": [], "available": False, "error": str(e)[:120]}


def fetch_oi_history(symbol: str = "BTCUSDT", period: str = "1h", limit: int = 30) -> dict:
    """Open-interest history — building OI = fresh positioning, falling = unwinding."""
    try:
        itv = _OI_INTERVAL.get(period, "1h")
        rows = _rows(_get_json(
            f"{_BASE}/v5/market/open-interest?category={_CATEGORY}"
            f"&symbol={symbol}&intervalTime={itv}&limit={int(limit)}"))
        series = [
            {"time": int(d["timestamp"]),
             "oi": float(d["openInterest"]),
             # Bybit OI is in base-asset contracts; no separate USD value field,
             # so oi_value mirrors oi (flow.py only trends the series, not the unit).
             "oi_value": float(d["openInterest"])}
            for d in reversed(rows)
        ]
        return {"series": series, "available": bool(series), "source": "bybit"}
    except Exception as e:  # noqa: BLE001
        return {"series": [], "available": False, "error": str(e)[:120]}


def fetch_long_short_ratio(symbol: str = "BTCUSDT", period: str = "1h", limit: int = 30) -> dict:
    """Long/short ACCOUNT ratio — retail crowd positioning (>1 = more longs)."""
    try:
        per = _RATIO_PERIOD.get(period, "1h")
        rows = _rows(_get_json(
            f"{_BASE}/v5/market/account-ratio?category={_CATEGORY}"
            f"&symbol={symbol}&period={per}&limit={int(limit)}"))
        series = []
        for d in reversed(rows):
            buy = float(d["buyRatio"])
            sell = float(d["sellRatio"])
            series.append({
                "time": int(d["timestamp"]),
                "ratio": round(buy / sell, 4) if sell else None,
                "long_pct": buy, "short_pct": sell,
            })
        return {"series": series, "available": bool(series), "source": "bybit"}
    except Exception as e:  # noqa: BLE001
        return {"series": [], "available": False, "error": str(e)[:120]}


if __name__ == "__main__":
    print("funding:", fetch_funding_history("BTCUSDT", 3))
    print("oi:", fetch_oi_history("BTCUSDT", "1h", 3))
    print("ls:", fetch_long_short_ratio("BTCUSDT", "1h", 3))
