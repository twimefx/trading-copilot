"""Tests for alerts (rules + evaluation), watchlists, signal history, spend log."""
import time

import pytest

from backend import alerts as alert_store
from backend.billing import users as user_store
from backend.signals import history as signal_history

U = "user_test_123"


# --- watchlists ----------------------------------------------------------------

def test_watchlist_roundtrip():
    assert user_store.get_watchlist(U) == []
    saved = user_store.set_watchlist(U, ["BTCUSDT", "ethusdt", "BTCUSDT", " EUR_USD "])
    assert saved == ["BTCUSDT", "ETHUSDT", "EUR_USD"]   # dedupe + upper
    assert user_store.get_watchlist(U) == saved


def test_watchlist_cap():
    with pytest.raises(ValueError):
        user_store.set_watchlist(U, [f"S{i}" for i in range(user_store.MAX_WATCHLIST_SYMBOLS + 1)])


# --- spend log -----------------------------------------------------------------

def test_spend_log_rollup():
    user_store.record_spend(U, "copilot", 0.012)
    user_store.record_spend(U, "copilot", 0.008)
    user_store.record_spend(U, "copilot", 0)          # ignored (non-positive)
    days = user_store.spend_by_day(7)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    row = next(d for d in days if d["day"] == today)
    assert row["usd"] == pytest.approx(0.02, abs=1e-4)
    assert row["calls"] == 2


# --- alert rules ----------------------------------------------------------------

def test_rule_validation():
    with pytest.raises(ValueError):
        alert_store.create_rule(U, "nonsense", {})
    with pytest.raises(ValueError):
        alert_store.create_rule(U, "price_above", {"symbol": "BTCUSDT"})        # no value
    with pytest.raises(ValueError):
        alert_store.create_rule(U, "scanner_lean", {"symbols": [], "lean": "bullish"})
    with pytest.raises(ValueError):
        alert_store.create_rule(U, "scanner_lean", {"symbols": ["BTC"], "lean": "sideways"})


def test_rule_crud_and_scoping():
    r = alert_store.create_rule(U, "price_above",
                                {"symbol": "BTCUSDT", "value": 70000})
    assert r["active"] is True
    got = alert_store.get_rule(U, r["id"])
    assert got is not None and got["config"]["value"] == 70000
    assert alert_store.get_rule("someone_else", r["id"]) is None   # scoped by user
    assert len(alert_store.list_rules(U)) == 1

    r2 = alert_store.update_rule(U, r["id"], active=False, cooldown_s=60)
    assert r2 is not None
    assert r2["active"] is False and r2["cooldown_s"] == 60

    assert alert_store.delete_rule(U, r["id"]) is True
    assert alert_store.delete_rule(U, r["id"]) is False


def test_rule_limit():
    for i in range(alert_store.MAX_RULES_PER_USER):
        alert_store.create_rule(U, "price_below", {"symbol": "ETHUSDT", "value": i + 1})
    with pytest.raises(ValueError):
        alert_store.create_rule(U, "price_below", {"symbol": "ETHUSDT", "value": 1})


def _klines_df(closes: list[float]):
    import pandas as pd

    return pd.DataFrame({
        "timestamps": pd.date_range("2026-01-01", periods=len(closes), freq="h"),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes),
    })


def _provider_for(closes: list[float]):
    df = _klines_df(closes)
    return type("P", (), {"fetch_klines": lambda self, *a, **k: df})()


def test_price_rule_fires_and_cools_down(monkeypatch):
    monkeypatch.setattr("backend.data.providers.get_provider",
                        lambda s: _provider_for([68000.0, 70500.0]))
    delivered = []
    monkeypatch.setattr(alert_store, "_deliver", lambda rule, msg: delivered.append(msg) or ["test"])

    alert_store.create_rule(U, "price_above", {"symbol": "BTCUSDT", "value": 70000},
                            cooldown_s=3600)
    out = alert_store.evaluate_rules()
    assert out["fired"] == 1
    assert delivered and "BTCUSDT" in delivered[0]

    # Second sweep: cooldown blocks re-firing.
    out2 = alert_store.evaluate_rules()
    assert out2["fired"] == 0

    # Test-trigger ignores cooldown but still reports honestly.
    out3 = alert_store.evaluate_rules(trigger_test_rule_id=alert_store.list_rules(U)[0]["id"])
    assert out3["checked"] == 1

    events = alert_store.list_events(U)
    assert len(events) == 2
    assert events[0]["channels"] == ["test"]


def test_scanner_rule_fires_on_lean(monkeypatch):
    monkeypatch.setattr(
        "backend.signals.scanner.scan_watchlist",
        lambda symbols, interval: [
            {"symbol": "BTCUSDT", "lean": "bullish", "score": 3},
            {"symbol": "ETHUSDT", "lean": "bearish", "score": -2},
        ],
    )
    delivered = []
    monkeypatch.setattr(alert_store, "_deliver", lambda rule, msg: delivered.append(msg) or ["test"])

    alert_store.create_rule(U, "scanner_lean",
                            {"symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1h", "lean": "bullish"})
    out = alert_store.evaluate_rules()
    assert out["fired"] == 1
    assert "BTCUSDT" in delivered[0] and "bullish" in delivered[0]


# --- signal history ---------------------------------------------------------------

def test_signal_log_and_pending_stats():
    signal_history.log_signal("BTCUSDT", "1h", "crypto", "bullish", 72, 65000.0)
    s = signal_history.list_signals("BTCUSDT")
    assert len(s) == 1 and s[0]["outcome"] is None and s[0]["entry_price"] == 65000.0
    st = signal_history.stats()
    assert st["total_signals"] == 1 and st["scored"] == 0
    assert st["accuracy_pct"] is None     # nothing scored yet — never fake a number


def test_signal_resolution_scores_honestly(monkeypatch):
    # Signal created 25h ago (past the 24x1h horizon).
    sid = signal_history.log_signal("ETHUSDT", "1h", "crypto", "bullish", 80, 3000.0)
    with signal_history._conn() as conn:
        conn.cursor().execute(
            "UPDATE signal_history SET created_at = ? WHERE id = ?",
            (time.time() - 25 * 3600, sid),
        )

    class _DF:
        @property
        def close(self):
            class _S:
                @property
                def iloc(self):
                    return [3150.0]     # price went UP for a bullish call
            return _S()

    monkeypatch.setattr(
        "backend.data.providers.get_provider",
        lambda s: type("P", (), {"fetch_klines": lambda self, *a: _klines_df([3000.0, 3150.0])})(),
    )
    out = signal_history.resolve_pending()
    assert out["resolved"] == 1
    sig = signal_history.list_signals("ETHUSDT")[0]
    assert sig["outcome"] == "correct" and sig["outcome_price"] == 3150.0
    st = signal_history.stats()
    assert st["accuracy_pct"] == 100.0


def test_neutral_signals_excluded_from_accuracy(monkeypatch):
    signal_history.log_signal("SOLUSDT", "1h", "crypto", "neutral", None, 100.0)
    with signal_history._conn() as conn:
        conn.cursor().execute(
            "UPDATE signal_history SET created_at = ? ", (time.time() - 25 * 3600,)
        )

    class _DF:
        @property
        def close(self):
            class _S:
                @property
                def iloc(self):
                    return [110.0]
            return _S()

    monkeypatch.setattr(
        "backend.data.providers.get_provider",
        lambda s: type("P", (), {"fetch_klines": lambda self, *a: _klines_df([100.0, 110.0])})(),
    )
    signal_history.resolve_pending()
    st = signal_history.stats()
    assert st["scored"] == 0
    assert st["flat_or_neutral"] == 1
