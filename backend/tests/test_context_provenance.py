"""Lineage tests for the exact candle frame used by MarketContext."""
from __future__ import annotations

import pandas as pd

from backend.signals import context as context_module


def test_context_normalizes_symbol_and_records_candle_as_of(monkeypatch):
    class EquityProvider:
        __name__ = "fake_equity"

        @staticmethod
        def fetch_klines(symbol, interval, candles):
            assert symbol == "NVDA"
            assert interval == "1d"
            assert candles == 3
            return pd.DataFrame({
                "timestamps": pd.to_datetime(["2026-08-10", "2026-08-11"]),
                "open": [100.0, 101.0], "high": [102.0, 103.0],
                "low": [99.0, 100.0], "close": [101.0, 102.0],
                "volume": [10.0, 11.0], "amount": [1010.0, 1122.0],
            })

        @staticmethod
        def fetch_funding_rate(_symbol):
            return {"available": False, "note": "n/a for equities"}

        @staticmethod
        def fetch_open_interest(_symbol):
            return {"available": False, "note": "n/a for equities"}

        @staticmethod
        def provenance(symbol, interval):
            return {"provider": "Fixture feed", "symbol": symbol, "interval": interval}

    monkeypatch.setattr(context_module, "get_provider", lambda symbol: EquityProvider)
    monkeypatch.setattr(context_module, "asset_class", lambda symbol: "equity")
    ctx = context_module.build_market_context(" nvda ", "1d", candles=3, include_kronos=False)

    assert ctx.symbol == "NVDA"
    assert ctx.provenance["as_of"] == "2026-08-11T00:00:00"
    assert ctx.provenance["status"] == "provider_timestamp_only"
    assert ctx.provenance["retrieved_at"].endswith("+00:00")