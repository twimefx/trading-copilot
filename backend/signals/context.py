"""MarketContext — assembles all signals into one structured object for the LLM.

This is the single source of truth the reasoning layer reasons over. Combining:
  - live price + technical indicators (momentum/trend)   [direction inputs]
  - funding rate + open interest (positioning/sentiment) [direction inputs]
  - Kronos range forecast (volatility/level)             [risk/range input]
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from backend.data.providers import get_provider, asset_class
from backend.data.indicators import snapshot


@dataclass
class MarketContext:
    symbol: str
    interval: str
    asset_class: str = "crypto"
    indicators: dict = field(default_factory=dict)
    funding: dict = field(default_factory=dict)
    open_interest: dict = field(default_factory=dict)
    kronos_range: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


def build_market_context(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    candles: int = 400,
    include_kronos: bool = True,
) -> MarketContext:
    """Fetch all live inputs and assemble a MarketContext.

    Provider (Binance/Oanda) is chosen automatically from the symbol.
    Kronos is optional (it's the slow CPU part) — callers can skip it for speed.
    """
    provider = get_provider(symbol)
    df = provider.fetch_klines(symbol, interval, candles)
    ctx = MarketContext(
        symbol=symbol,
        interval=interval,
        asset_class=asset_class(symbol),
        indicators=snapshot(df),
        funding=provider.fetch_funding_rate(symbol),
        open_interest=provider.fetch_open_interest(symbol),
    )
    if include_kronos:
        from backend.signals.kronos_range import forecast_range
        ctx.kronos_range = forecast_range(df, pred_len=24, sample_count=3)
    return ctx


if __name__ == "__main__":
    ctx = build_market_context("BTCUSDT", include_kronos=False)
    print(ctx.to_prompt_json())
