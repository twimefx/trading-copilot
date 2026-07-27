"""Tests for the RunPod-serverless path of the Kronos client."""
import json

import pandas as pd
import pytest

from backend.signals.context import _fetch_kronos_range


def _fake_df(n=60):
    return pd.DataFrame({
        "timestamps": pd.date_range("2026-06-01", periods=n, freq="h"),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n, "amount": [1000.0] * n,
    })


def _patch_urlopen(monkeypatch, body):
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(body).encode()

    captured = {}

    import urllib.request
    def fake(req, timeout=0, **kw):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return FakeResp()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return captured


def test_runpod_mode_unwraps_output(monkeypatch):
    monkeypatch.setenv("KRONOS_RUNPOD", "1")
    monkeypatch.setenv("KRONOS_SERVICE_URL", "https://api.runpod.ai/v2/abc123/runsync")
    monkeypatch.setenv("KRONOS_API_KEY", "rk_test")
    cap = _patch_urlopen(monkeypatch, {
        "status": "COMPLETED",
        "output": {"low": 95.0, "high": 105.0, "expected_close": 100.5,
                   "band_width_pct": 10.0, "source": "Kronos", "device": "cuda"},
    })
    out = _fetch_kronos_range(_fake_df())
    assert out["source"] == "Kronos" and out["low"] == 95.0
    # Auth header sent; ohlcv wrapped in RunPod's {input: {...}} envelope.
    assert any(v == "Bearer rk_test" for v in cap["headers"].values())
    body = json.loads(cap["data"].decode())
    assert "input" in body and "ohlcv" in body["input"]


def test_runpod_mode_inner_error_degrades(monkeypatch):
    monkeypatch.setenv("KRONOS_RUNPOD", "1")
    monkeypatch.setenv("KRONOS_SERVICE_URL", "https://api.runpod.ai/v2/abc123/runsync")
    monkeypatch.setenv("KRONOS_API_KEY", "rk_test")
    _patch_urlopen(monkeypatch, {"status": "COMPLETED",
                                 "output": {"error": "ValueError: bad candles"}})
    out = _fetch_kronos_range(_fake_df())
    assert out.get("available") is False and "low" not in out


def test_runpod_mode_non_completed_status_degrades(monkeypatch):
    monkeypatch.setenv("KRONOS_RUNPOD", "1")
    monkeypatch.setenv("KRONOS_SERVICE_URL", "https://api.runpod.ai/v2/abc123/runsync")
    monkeypatch.setenv("KRONOS_API_KEY", "rk_test")
    _patch_urlopen(monkeypatch, {"status": "FAILED", "error": "worker died"})
    out = _fetch_kronos_range(_fake_df())
    assert out.get("available") is False and "low" not in out


def test_direct_mode_still_works_when_runpod_unset(monkeypatch):
    # Regression: without KRONOS_RUNPOD, the plain HTTP service path is unchanged.
    monkeypatch.delenv("KRONOS_RUNPOD", raising=False)
    monkeypatch.delenv("KRONOS_API_KEY", raising=False)
    monkeypatch.setenv("KRONOS_SERVICE_URL", "http://kronos.internal:8012")
    cap = _patch_urlopen(monkeypatch, {"low": 1.0, "high": 2.0, "source": "Kronos"})
    out = _fetch_kronos_range(_fake_df())
    assert out == {"low": 1.0, "high": 2.0, "source": "Kronos"}
    assert cap["url"].endswith("/forecast")
