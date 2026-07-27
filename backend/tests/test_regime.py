"""Market regime risk gate — a deterministic 4-state gate run BEFORE generating
candidates or a daily brief (idea borrowed from the hermes-trading-research package).

States:
  FULL_RISK_ALLOWED — broad uptrend participation, no major stress. Full size ideas.
  SELECTIVE_ONLY    — mixed evidence; only the strongest setups, smaller size.
  CASH_PRIORITY     — risk-off / deteriorating breadth; no new discretionary swing.
  RESEARCH_ONLY     — data missing/uncertain regime; research and journaling only.

The gate is DETERMINISTIC (no LLM): it reads indicator snapshots across a small
universe and summarizes breadth / trend participation / momentum stress. It is a
decision-SUPPORT gate for a human — it never makes an execution recommendation.
"""
import pytest

from backend.signals import regime


def _ind(px=100.0, ema20=99.0, ema50=98.0, sma200=95.0, rsi=55.0, hist=1.0):
    return {"last_close": px, "ema_20": ema20, "ema_50": ema50,
            "sma_200": sma200, "rsi_14": rsi, "macd_hist": hist}


# --- state classification -------------------------------------------------------

def test_full_risk_when_broad_uptrend():
    snaps = {s: _ind() for s in ["A", "B", "C", "D", "E"]}  # all bullish posture
    g = regime.evaluate(snapshots=snaps)
    assert g["state"] == "FULL_RISK_ALLOWED"
    assert g["breadth_pct"] >= 80


def test_cash_priority_when_broad_downtrend():
    bear = _ind(px=90.0, ema20=95.0, ema50=96.0, sma200=97.0, rsi=35.0, hist=-2.0)
    snaps = {s: bear for s in ["A", "B", "C", "D", "E"]}
    g = regime.evaluate(snapshots=snaps)
    assert g["state"] == "CASH_PRIORITY"
    assert g["breadth_pct"] <= 20


def test_selective_when_mixed():
    bull = _ind()
    bear = _ind(px=90.0, ema20=95.0, ema50=96.0, sma200=97.0, rsi=35.0, hist=-2.0)
    snaps = {"A": bull, "B": bull, "C": bear, "D": bear, "E": bull}
    g = regime.evaluate(snapshots=snaps)
    assert g["state"] == "SELECTIVE_ONLY"


def test_research_only_when_data_missing():
    snaps = {"A": None, "B": None, "C": None}  # provider failures -> no snapshots
    g = regime.evaluate(snapshots=snaps)
    assert g["state"] == "RESEARCH_ONLY"
    assert g["missing_data"]


def test_research_only_when_empty():
    g = regime.evaluate(snapshots={})
    assert g["state"] == "RESEARCH_ONLY"


def test_gate_has_reasons_and_action_lists():
    snaps = {s: _ind() for s in ["A", "B", "C"]}
    g = regime.evaluate(snapshots=snaps)
    assert isinstance(g["reasons"], list) and g["reasons"]
    assert isinstance(g["allowed_actions"], list)
    assert isinstance(g["blocked_actions"], list)
    assert g["confidence"] in ("low", "medium", "high")


def test_gate_counts_symbols():
    snaps = {s: _ind() for s in ["A", "B", "C", "D"]}
    g = regime.evaluate(snapshots=snaps)
    assert g["symbols_evaluated"] == 4


# --- convenience: build from a live universe ------------------------------------

def test_assess_universe_uses_provider(monkeypatch):
    class _P:
        def fetch_klines(self, symbol, interval, limit):
            import pandas as pd
            # Minimal frame that snapshot() can read.
            closes = [100 + i * 0.1 for i in range(250)]
            return pd.DataFrame({
                "open": closes, "high": closes, "low": closes,
                "close": closes, "volume": [1.0] * 250,
            })
    monkeypatch.setattr(regime, "get_provider", lambda symbol: _P())
    g = regime.assess_universe(["BTCUSDT", "ETHUSDT"], interval="1h")
    assert g["symbols_evaluated"] == 2
    assert g["state"] in ("FULL_RISK_ALLOWED", "SELECTIVE_ONLY",
                          "CASH_PRIORITY", "RESEARCH_ONLY")


def test_evaluate_from_scan_cards():
    # Scanner cards carry last_close/rsi_14/macd_hist but not the MAs — the gate
    # reads them via a lean-based fallback so /scan can attach a regime for free.
    cards = [
        {"symbol": "A", "ok": True, "lean": "bullish", "rsi_14": 55, "macd_hist": 1},
        {"symbol": "B", "ok": True, "lean": "bullish", "rsi_14": 52, "macd_hist": 1},
        {"symbol": "C", "ok": True, "lean": "bullish", "rsi_14": 58, "macd_hist": 1},
        {"symbol": "D", "ok": True, "lean": "bearish", "rsi_14": 40, "macd_hist": -1},
    ]
    g = regime.evaluate_from_cards(cards)
    assert g["symbols_evaluated"] == 4
    assert g["state"] in ("FULL_RISK_ALLOWED", "SELECTIVE_ONLY")


def test_scan_endpoint_attaches_regime(client, monkeypatch):
    from backend.api import main as main_mod
    monkeypatch.setattr(main_mod.scan_cache, "get", lambda k: None)
    monkeypatch.setattr(
        main_mod, "scan_watchlist" if hasattr(main_mod, "scan_watchlist") else "scan_watchlist",
        lambda symbols, interval: [
            {"symbol": s, "ok": True, "lean": "bullish", "conviction": 60,
             "rsi_14": 55, "macd_hist": 1, "reasons": []} for s in symbols
        ],
        raising=False,
    )
    # scan imports scan_watchlist lazily inside the handler; patch the source module.
    import backend.signals.scanner as scanner_mod
    monkeypatch.setattr(
        scanner_mod, "scan_watchlist",
        lambda symbols, interval: [
            {"symbol": s, "ok": True, "lean": "bullish", "conviction": 60,
             "rsi_14": 55, "macd_hist": 1, "reasons": []} for s in symbols
        ],
    )
    r = client.post("/scan", json={"symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1h"})
    assert r.status_code == 200
    body = r.json()
    assert "regime" in body
    assert body["regime"]["state"] in (
        "FULL_RISK_ALLOWED", "SELECTIVE_ONLY", "CASH_PRIORITY", "RESEARCH_ONLY")
