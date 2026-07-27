"""Daily market brief — scheduled regime + movers + a short brief to Telegram.

Mirrors the cost-digest scheduler pattern: POST /brief/daily is guarded by the
shared scheduler key, scans the watchlist, computes the regime gate, composes a
deterministic brief (with one optional cheap-LLM paragraph), and delivers via the
existing alert channels. Data-fresh, source-attributed, human-decision framing.
"""
import pytest

from backend.api import main as main_mod


def _cards():
    return [
        {"symbol": "BTCUSDT", "ok": True, "lean": "bullish", "conviction": 80,
         "last_close": 65000, "rsi_14": 58, "macd_hist": 1, "reasons": ["price > EMA20"]},
        {"symbol": "ETHUSDT", "ok": True, "lean": "bullish", "conviction": 60,
         "last_close": 3200, "rsi_14": 55, "macd_hist": 1, "reasons": []},
        {"symbol": "SOLUSDT", "ok": True, "lean": "bearish", "conviction": 70,
         "last_close": 140, "rsi_14": 35, "macd_hist": -1, "reasons": ["below SMA200"]},
        {"symbol": "XRPUSDT", "ok": True, "lean": "neutral", "conviction": 20,
         "last_close": 0.5, "rsi_14": 50, "macd_hist": 0, "reasons": []},
    ]


@pytest.fixture
def brief_env(monkeypatch):
    """Stub the scanner + regime + LLM + telegram so the brief is fully deterministic."""
    import backend.signals.scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "scan_watchlist", lambda symbols, interval: _cards())
    # No LLM paragraph by default (deterministic); capture telegram sends.
    sent = []
    monkeypatch.setattr(main_mod.alert_store, "_notify_telegram",
                        lambda chat_id, text: sent.append((chat_id, text)) or True)
    monkeypatch.setenv("ALERT_TELEGRAM_DEFAULT_CHAT_ID", "7373566")
    monkeypatch.setenv("ALERT_SCHEDULER_KEY", "test-key")
    return sent


def test_brief_requires_scheduler_key(client, brief_env):
    r = client.post("/brief/daily", json={"scheduler_key": "wrong"})
    assert r.status_code == 403


def test_brief_happy_path_returns_regime_and_movers(client, brief_env):
    r = client.post("/brief/daily", json={"scheduler_key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["regime"]["state"] in (
        "FULL_RISK_ALLOWED", "SELECTIVE_ONLY", "CASH_PRIORITY", "RESEARCH_ONLY")
    assert "movers" in body and len(body["movers"]) > 0
    assert body["brief"]
    assert body["delivered"] == ["telegram"]


def test_brief_delivers_telegram_with_regime_header(client, brief_env):
    client.post("/brief/daily", json={"scheduler_key": "test-key"})
    assert brief_env, "a telegram message should have been sent"
    chat_id, text = brief_env[0]
    assert chat_id == "7373566"
    assert "Market brief" in text
    assert "regime" in text.lower()
    # Movers named in the message.
    assert "BTCUSDT" in text


def test_brief_includes_decision_gate_not_advice(client, brief_env):
    r = client.post("/brief/daily", json={"scheduler_key": "test-key"})
    assert "not financial advice" in r.json()["brief"].lower() or \
           "decision" in r.json()["brief"].lower()


def test_brief_no_telegram_configured_reports_undelivered(client, monkeypatch):
    import backend.signals.scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "scan_watchlist", lambda symbols, interval: _cards())
    monkeypatch.setenv("ALERT_SCHEDULER_KEY", "test-key")
    monkeypatch.delenv("ALERT_TELEGRAM_DEFAULT_CHAT_ID", raising=False)
    monkeypatch.delenv("ALERT_DIGEST_EMAIL", raising=False)
    r = client.post("/brief/daily", json={"scheduler_key": "test-key"})
    assert r.status_code == 200
    assert r.json()["delivered"] == []
