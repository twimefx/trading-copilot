"""Traceable US-equity market data adapter using Yahoo Finance's chart endpoint.

The adapter normalizes the provider payload into the same OHLCV schema used by
crypto and forex. It deliberately reports unsupported derivatives fields as
unavailable rather than inventing funding or open-interest data for equities.

Yahoo is an upstream source, not an inference engine: every response carries the
provider timestamp in the MarketContext provenance added by the orchestration
layer. A paid/licensed feed can replace this module without changing callers.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from backend.data.errors import UnknownSymbolError

_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m",
    "1d": "1d", "1wk": "1wk", "1mo": "1mo",
}
_RANGES = {
    "1m": "5d", "5m": "1mo", "15m": "1mo", "30m": "1mo", "1h": "2y",
    "1d": "2y", "1wk": "5y", "1mo": "10y",
}


def normalize_symbol(symbol: str) -> str:
    """Normalize a US ticker accepted by the Yahoo chart API."""
    return symbol.strip().upper().replace("/", "-")


def _chart(symbol: str, interval: str) -> dict:
    normalized = normalize_symbol(symbol)
    provider_interval = _INTERVALS.get(interval)
    if provider_interval is None:
        raise ValueError(f"Unsupported equity interval '{interval}'.")
    query = urllib.parse.urlencode({"interval": provider_interval, "range": _RANGES[interval]})
    url = f"{_CHART_BASE}/{urllib.parse.quote(normalized, safe='.-')}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AI-Market-Copilot/0.5)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 404}:
            raise UnknownSymbolError(symbol, "Yahoo Finance") from exc
        raise

    chart = body.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise UnknownSymbolError(symbol, "Yahoo Finance")
    return chart["result"][0]


def fetch_klines(symbol: str = "NVDA", interval: str = "1d", limit: int = 500) -> pd.DataFrame:
    """Fetch equity OHLCV and normalize it to the project-wide candle schema."""
    result = _chart(symbol, interval)
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    rows = []
    for timestamp, opening, high, low, close, volume in zip(
        timestamps, quote.get("open") or [], quote.get("high") or [], quote.get("low") or [],
        quote.get("close") or [], quote.get("volume") or [],
    ):
        if None in (opening, high, low, close, volume):
            continue
        close_float = float(close)
        volume_float = float(volume)
        rows.append({
            "timestamps": pd.to_datetime(timestamp, unit="s", utc=True),
            "open": float(opening), "high": float(high), "low": float(low),
            "close": close_float, "volume": volume_float, "amount": close_float * volume_float,
        })
    frame = pd.DataFrame(rows, columns=["timestamps", "open", "high", "low", "close", "volume", "amount"])
    if frame.empty:
        raise UnknownSymbolError(symbol, "Yahoo Finance")
    return frame.tail(limit).reset_index(drop=True)


def fetch_funding_rate(_symbol: str = "NVDA") -> dict:
    return {"available": False, "note": "n/a for equities"}


def fetch_open_interest(_symbol: str = "NVDA") -> dict:
    return {"available": False, "note": "n/a for equities"}


def fetch_fundamentals(symbol: str = "NVDA") -> dict:
    """Return only source-verified chart metadata available from this adapter."""
    result = _chart(symbol, "1d")
    meta = result.get("meta") or {}
    return {
        "available": True,
        "provider": "Yahoo Finance chart API",
        "symbol": meta.get("symbol"),
        "exchange": meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "market_state": meta.get("marketState"),
        "regular_market_price": meta.get("regularMarketPrice"),
    }


def fetch_news(_symbol: str = "NVDA") -> dict:
    """News is intentionally not inferred from a price provider in the MVP slice."""
    return {"available": False, "note": "news provider not configured"}


def provenance(symbol: str, interval: str) -> dict:
    return {
        "provider": "Yahoo Finance chart API",
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "freshness": "provider response time",
    }
