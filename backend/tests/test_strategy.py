"""Tests for the AI Strategy Builder (Phase 3).

  * validate_spec()  — strict schema, rejects unknown indicators/ops/params.
  * backtest()       — deterministic, look-ahead safe, correct stats on known data.
  * nl_to_spec()     — LLM mocked; validated before use.
  * POST /strategy   — Premium gating + cache + bad-spec -> 422.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.signals.strategy import (
    SpecError,
    backtest,
    build_strategy,
    validate_spec,
)


# --- spec validation ---------------------------------------------------------

def _good_spec():
    return {
        "symbol": "BTCUSDT", "interval": "1h", "direction": "long",
        "entry": [{"left": {"indicator": "rsi", "period": 14},
                   "op": "<", "right": {"indicator": "value", "value": 30}}],
        "exit": [{"left": {"indicator": "rsi", "period": 14},
                  "op": ">", "right": {"indicator": "value", "value": 70}}],
        "stop_loss_pct": 3, "take_profit_pct": 6,
    }


def test_validate_good_spec():
    s = validate_spec(_good_spec())
    assert s["direction"] == "long"
    assert s["entry"][0]["left"]["period"] == 14
    assert s["stop_loss_pct"] == 3.0


def test_reject_unknown_indicator():
    s = _good_spec()
    s["entry"][0]["left"] = {"indicator": "bollinger", "period": 20}
    with pytest.raises(SpecError):
        validate_spec(s)


def test_reject_unknown_operator():
    s = _good_spec()
    s["entry"][0]["op"] = "moons_align"
    with pytest.raises(SpecError):
        validate_spec(s)


def test_reject_empty_entry():
    s = _good_spec()
    s["entry"] = []
    with pytest.raises(SpecError):
        validate_spec(s)


def test_reject_bad_direction():
    s = _good_spec()
    s["direction"] = "sideways"
    with pytest.raises(SpecError):
        validate_spec(s)


def test_reject_out_of_range_stop():
    s = _good_spec()
    s["stop_loss_pct"] = 500
    with pytest.raises(SpecError):
        validate_spec(s)


def test_reject_period_out_of_range():
    s = _good_spec()
    s["entry"][0]["left"] = {"indicator": "ema", "period": 99999}
    with pytest.raises(SpecError):
        validate_spec(s)


# --- backtest correctness + look-ahead safety --------------------------------

def _price_df(closes):
    closes = [float(c) for c in closes]
    # opens = previous close (so next-open fill is deterministic and testable)
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": [min(o, c) for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [1000.0] * len(closes),
    })


def test_backtest_price_cross_generates_trades():
    # Price oscillates around an SMA so a cross strategy trades.
    closes = [100 + (10 if i % 10 < 5 else -10) for i in range(120)]
    df = _price_df(closes)
    spec = {
        "symbol": "X", "interval": "1h", "direction": "long",
        "entry": [{"left": {"indicator": "price"}, "op": "cross_above",
                   "right": {"indicator": "sma", "period": 5}}],
        "exit": [{"left": {"indicator": "price"}, "op": "cross_below",
                  "right": {"indicator": "sma", "period": 5}}],
        "stop_loss_pct": None, "take_profit_pct": None,
    }
    bt = backtest(df, spec)
    assert bt["error"] is None
    assert bt["stats"]["trades"] > 0
    assert len(bt["equity_curve"]) == len(df)
    # win_rate is a valid probability
    assert 0 <= (bt["stats"]["win_rate"] or 0) <= 1


def test_backtest_take_profit_and_stop_respected():
    # Steady uptrend; a long with a 5% take-profit should exit at target.
    closes = [100 * (1.01 ** i) for i in range(60)]
    df = _price_df(closes)
    spec = {
        "symbol": "X", "interval": "1h", "direction": "long",
        "entry": [{"left": {"indicator": "price"}, "op": ">",
                   "right": {"indicator": "value", "value": 0}}],  # always-on entry
        "exit": [],
        "stop_loss_pct": None, "take_profit_pct": 5,
    }
    bt = backtest(df, spec)
    assert bt["stats"]["trades"] >= 1
    # every closed trade in a pure uptrend with a TP should be a target win
    assert all(t["reason"] in ("target", "signal") for t in bt["trades"])
    assert bt["stats"]["total_return_pct"] > 0


def test_backtest_no_lookahead_uses_next_open():
    # Entry fires on bar i (close) -> fill must be opens[i+1], not closes[i].
    # Price rises past 15 (entry), keeps rising, then a later exit closes the trade
    # so we can inspect the recorded entry_price.
    closes = [10, 20, 30, 40, 50, 60] + [70] * 20 + [5] * 20   # rise then crash triggers exit
    df = _price_df(closes)
    spec = {
        "symbol": "X", "interval": "1h", "direction": "long",
        "entry": [{"left": {"indicator": "price"}, "op": ">",
                   "right": {"indicator": "value", "value": 15}}],
        "exit": [{"left": {"indicator": "price"}, "op": "<",
                  "right": {"indicator": "value", "value": 10}}],   # closes on the crash
        "stop_loss_pct": None, "take_profit_pct": None,
    }
    bt = backtest(df, spec)
    # Entry signal (close>15) first true at bar index 1 (close=20); fill = open[2] = close[1] = 20.
    # The key point: fill is the NEXT bar's open, never the signal bar's own close.
    assert bt["trades"], "expected at least one closed trade"
    first = bt["trades"][0]
    assert first["entry_i"] == 2               # filled on the bar AFTER the signal
    assert first["entry_price"] == 20.0        # next open, not look-ahead


def test_backtest_insufficient_bars():
    df = _price_df([100, 101, 102])
    bt = backtest(df, {"direction": "long",
                       "entry": [{"left": {"indicator": "price"}, "op": ">",
                                  "right": {"indicator": "value", "value": 0}}],
                       "exit": []})
    assert bt["error"] is not None


# --- nl_to_spec + build_strategy (LLM mocked) --------------------------------

class _FakeRouter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

        class _CL:
            total_usd = 0.011
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.calls.append(task)
        return self.payload


def test_build_strategy_end_to_end_mocked():
    from backend.ai.router import TaskClass
    spec_json = json.dumps({
        "name": "RSI reversion",
        "direction": "long",
        "entry": [{"left": {"indicator": "rsi", "period": 14},
                   "op": "<", "right": {"indicator": "value", "value": 30}}],
        "exit": [{"left": {"indicator": "rsi", "period": 14},
                  "op": ">", "right": {"indicator": "value", "value": 60}}],
        "stop_loss_pct": 4, "take_profit_pct": 8,
    })
    r = _FakeRouter(spec_json)
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.standard_normal(200))
    df = _price_df(closes)
    out = build_strategy("buy oversold RSI, exit at 60", "BTCUSDT", "1h", router=r, df=df)
    assert out["error"] is None
    assert out["spec"]["name"] == "RSI reversion"
    assert out["stats"] is not None
    assert any("RSI" in line for line in out["rules_human"])
    assert out["cost_usd"] == 0.011
    assert r.calls[0] == TaskClass.STRATEGY_BUILDER


def test_build_strategy_rejects_bad_model_output():
    r = _FakeRouter('{"direction":"long","entry":[{"left":{"indicator":"bollinger"},'
                    '"op":"<","right":{"indicator":"value","value":1}}],"exit":[]}')
    df = _price_df([100] * 60)
    with pytest.raises(SpecError):
        build_strategy("some idea", "BTCUSDT", "1h", router=r, df=df)


# --- endpoint ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    from backend.api.main import app, strategy_cache
    from backend.api.auth import current_user_id
    strategy_cache.clear()
    state = {"user": "strat-user"}
    app.dependency_overrides[current_user_id] = lambda: state["user"]
    client = TestClient(app)
    client.set_api_user = lambda u: state.__setitem__("user", u)  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_strategy_endpoint_gated_below_premium(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "pro")
    r = api.post("/strategy", json={"prompt": "buy dips"})
    assert r.status_code == 402
    assert r.json()["upgrade"] is True


def test_strategy_endpoint_requires_prompt(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "premium")
    r = api.post("/strategy", json={"prompt": "   "})
    assert r.status_code == 422


def test_strategy_endpoint_full_path_mocked(api, monkeypatch):
    import backend.api.auth as auth
    import backend.billing.users as users
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(users, "get_tier", lambda uid: "premium")

    import backend.signals.strategy as strat
    fake = {"spec": {"name": "S", "direction": "long"}, "rules_human": ["ENTER when ALL: RSI(14) < 30"],
            "stats": {"trades": 3, "win_rate": 0.66, "total_return_pct": 12.0},
            "trades": [], "equity_curve": [1.0, 1.1], "error": None,
            "disclaimer": "nfa", "cost_usd": 0.01}
    monkeypatch.setattr(strat, "build_strategy", lambda *a, **k: fake)

    r = api.post("/strategy", json={"prompt": "buy oversold rsi"})
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["trades"] == 3
    assert body["cached"] is False

    r2 = api.post("/strategy", json={"prompt": "buy oversold rsi"})
    assert r2.json()["cached"] is True
