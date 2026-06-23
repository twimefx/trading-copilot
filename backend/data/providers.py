"""Data provider selector — routes a symbol to the right market data source.

Crypto (USDT/USDC/BTC pairs) -> Binance (free public API)
Forex  (6-letter FX or EUR_USD form) -> Oanda (requires OANDA_API_TOKEN)

Each provider exposes the same interface: fetch_klines, fetch_funding_rate,
fetch_open_interest. So MarketContext stays provider-agnostic.
"""
from __future__ import annotations

from backend.data import binance, oanda

_CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "BTC", "ETH")
# Common FX + metals quote/base codes for detection.
_FX_CODES = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG"}


def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if any(s.endswith(q) for q in _CRYPTO_QUOTES) and "_" not in s:
        return "crypto"
    # EUR_USD or EURUSD style
    cleaned = s.replace("_", "").replace("/", "").replace("-", "")
    if len(cleaned) == 6 and cleaned[:3] in _FX_CODES and cleaned[3:] in _FX_CODES:
        return "forex"
    return "crypto"  # default


def get_provider(symbol: str):
    """Return the data module appropriate for this symbol."""
    return oanda if asset_class(symbol) == "forex" else binance
