"""Shared data-layer exceptions."""
from __future__ import annotations


class UnknownSymbolError(ValueError):
    """Raised when a market-data provider rejects a symbol as invalid/unknown.

    Endpoints catch this and return a friendly 422 instead of a raw 500, so a
    user typo ('NOTACOIN') gets a clear message rather than a leaked stack trace.
    """

    def __init__(self, symbol: str, provider: str = "market data"):
        self.symbol = symbol
        self.provider = provider
        super().__init__(
            f"Unknown or unsupported symbol '{symbol}'. "
            f"Check the ticker (e.g. BTCUSDT for crypto, EUR_USD for forex)."
        )
