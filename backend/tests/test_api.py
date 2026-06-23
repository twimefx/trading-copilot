"""API smoke tests + guard behavior (cache prevents repeat LLM calls)."""
from fastapi.testclient import TestClient

import backend.signals.copilot as copilot_mod
from backend.api.main import app
from backend.api.guards import copilot_cache, spend_guard

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "spend_today_usd" in body


def test_copilot_caches_and_avoids_second_llm_call(monkeypatch):
    copilot_cache.clear()
    calls = {"n": 0}

    def fake_analyze(symbol, interval, include_kronos=True):
        calls["n"] += 1
        return {"lean": "neutral", "conviction": 50, "cost_usd": 0.05,
                "range_24h": {"low": 1, "high": 2, "source": "ATR estimate"}}

    monkeypatch.setattr(copilot_mod, "analyze_symbol", fake_analyze)

    body = {"symbol": "BTCUSDT", "interval": "1h", "include_kronos": False}
    r1 = client.post("/copilot", json=body)
    assert r1.status_code == 200
    assert r1.json()["cached"] is False

    r2 = client.post("/copilot", json=body)        # identical -> served from cache
    assert r2.status_code == 200
    assert r2.json()["cached"] is True

    assert calls["n"] == 1                          # LLM path ran ONCE, not twice
