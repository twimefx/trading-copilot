"""Tests for the traceable Yahoo Finance equity market-data adapter."""
from __future__ import annotations

import json

from backend.data import yahoo


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _chart_payload() -> dict:
    return {
        "chart": {"result": [{
            "meta": {"symbol": "NVDA", "exchangeName": "NMS", "regularMarketPrice": 123.45},
            "timestamp": [1704067200, 1704153600, 1704240000],
            "indicators": {"quote": [{
                "open": [120.0, 121.0, 122.0], "high": [122.0, 123.0, 124.0],
                "low": [119.0, 120.0, 121.0], "close": [121.0, 122.0, 123.45],
                "volume": [1000, 1100, 1200],
            }]},
        }], "error": None},
    }


def test_fetch_klines_normalizes_yahoo_chart_payload(monkeypatch):
    monkeypatch.setattr(yahoo.urllib.request, "urlopen", lambda *_a, **_k: _Response(_chart_payload()))

    frame = yahoo.fetch_klines("nvda", "1d", limit=3)

    assert list(frame.columns) == ["timestamps", "open", "high", "low", "close", "volume", "amount"]
    assert len(frame) == 3
    assert frame["close"].iloc[-1] == 123.45
    assert frame["amount"].iloc[-1] == 123.45 * 1200
    assert str(frame["timestamps"].dtype).startswith("datetime64")


def test_equity_provider_marks_derivatives_data_unavailable():
    assert yahoo.fetch_funding_rate("NVDA") == {"available": False, "note": "n/a for equities"}
    assert yahoo.fetch_open_interest("NVDA") == {"available": False, "note": "n/a for equities"}


def test_fetch_klines_range_uses_bounded_window_and_filters_future_candles(monkeypatch):
    captured: dict[str, str] = {}

    def _urlopen(request, **_kwargs):
        captured["url"] = request.full_url
        return _Response(_chart_payload())

    monkeypatch.setattr(yahoo.urllib.request, "urlopen", _urlopen)
    frame = yahoo.fetch_klines_range("NVDA", "1d", 1704067200000, 1704153600000)

    assert "period1=1704067200" in captured["url"]
    assert "period2=1704153600" in captured["url"]
    assert "range=" not in captured["url"]
    assert list(frame["close"]) == [121.0, 122.0]
    assert frame["timestamps"].is_monotonic_increasing
