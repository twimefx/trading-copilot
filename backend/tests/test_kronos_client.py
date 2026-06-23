"""Tests for the Kronos range fetch: remote service, fallback, and graceful degrade."""
import json

import pandas as pd
import pytest

from backend.signals import context as ctx_mod
from backend.signals.copilot import _compute_range
from backend.signals.context import MarketContext, _fetch_kronos_range


def _fake_df(n=60):
    return pd.DataFrame({
        "timestamps": pd.date_range("2026-06-01", periods=n, freq="h"),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n, "amount": [1000.0] * n,
    })


def test_no_service_and_no_torch_degrades_gracefully(monkeypatch):
    monkeypatch.delenv("KRONOS_SERVICE_URL", raising=False)
    # Force the in-process import to fail (simulates lean build without torch).
    import builtins
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if "kronos_range" in name:
            raise ImportError("no torch")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    out = _fetch_kronos_range(_fake_df())
    assert out.get("available") is False
    assert "low" not in out  # no fabricated band


def test_remote_service_used_when_url_set(monkeypatch):
    monkeypatch.setenv("KRONOS_SERVICE_URL", "http://kronos.internal:8012")
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"low": 95.0, "high": 105.0, "source": "Kronos"}).encode()

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = _fetch_kronos_range(_fake_df())
    assert out == {"low": 95.0, "high": 105.0, "source": "Kronos"}
    assert captured["url"].endswith("/forecast")


def test_remote_service_timeout_degrades(monkeypatch):
    monkeypatch.setenv("KRONOS_SERVICE_URL", "http://kronos.internal:8012")
    import urllib.request

    def boom(req, timeout=0):
        raise TimeoutError("slow")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = _fetch_kronos_range(_fake_df())
    assert out.get("available") is False
    assert "low" not in out


def test_compute_range_consumes_remote_kronos_shape():
    # The remote service returns {low, high, source:"Kronos"} — _compute_range must honor it.
    ctx = MarketContext(symbol="BTCUSDT", interval="1h",
                        indicators={"last_close": 100.0, "atr_14": 1.0},
                        kronos_range={"low": 95.0, "high": 105.0, "source": "Kronos"})
    r = _compute_range(ctx)
    assert r["source"] == "Kronos"
    assert r["low"] == 95.0 and r["high"] == 105.0
