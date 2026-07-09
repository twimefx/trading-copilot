"""AI Strategy Builder — natural language -> validated rule-spec -> real backtest.

Phase 3 Premium feature. The hard honesty rule: the LLM ONLY translates the
user's plain-English idea into a STRUCTURED, typed rule-spec. That spec is
validated against a strict schema (known indicators / operators / params only —
no eval, no arbitrary code) and then executed by a DETERMINISTIC backtester built
on our existing indicator functions. Results are computed from the real trade
list, never produced by the model. If the model emits an unknown indicator or a
malformed rule, we reject it rather than guess.

Look-ahead safety: a rule is evaluated on a bar's CLOSE, and the resulting
entry/exit fills at the NEXT bar's OPEN. No peeking at future data.
"""
from __future__ import annotations

import json

import pandas as pd

from backend.ai.router import AIRouter, TaskClass
from backend.data import indicators as ind

STRATEGY_DISCLAIMER = (
    "Backtested on historical data — past performance does NOT predict future "
    "results. Backtests are prone to overfitting and exclude slippage, fees, and "
    "liquidity limits. This is a research tool, not financial advice."
)

# --- rule-spec schema --------------------------------------------------------
# Indicators an operand may reference, with which params they accept.
_INDICATORS = {
    "price": set(),               # last close
    "value": {"value"},           # a constant threshold
    "rsi": {"period"},
    "ema": {"period"},
    "sma": {"period"},
    "atr_pct": set(),             # ATR as % of price
    "macd_hist": set(),           # MACD histogram
    "macd": set(),                # MACD line
    "macd_signal": set(),         # MACD signal line
    "volume_ratio": {"period"},   # recent vol vs prior window
}
_OPS = {"<", ">", "<=", ">=", "cross_above", "cross_below"}
_DIRECTIONS = {"long", "short", "both"}
_MAX_CONDITIONS = 6
_MAX_PERIOD = 400


class SpecError(ValueError):
    """Raised when an LLM-produced (or user) spec is invalid/unsafe."""


def _validate_operand(o: dict, where: str) -> dict:
    if not isinstance(o, dict):
        raise SpecError(f"{where}: operand must be an object")
    kind = o.get("indicator")
    if kind not in _INDICATORS:
        raise SpecError(f"{where}: unknown indicator {kind!r}")
    allowed = _INDICATORS[kind]
    clean: dict = {"indicator": kind}
    if kind == "value":
        if "value" not in o:
            raise SpecError(f"{where}: 'value' operand needs a numeric value")
        try:
            clean["value"] = float(o["value"])
        except (TypeError, ValueError):
            raise SpecError(f"{where}: value must be numeric")
    if "period" in allowed:
        if "period" not in o:
            raise SpecError(f"{where}: {kind} needs a 'period'")
        try:
            p = int(o["period"])
        except (TypeError, ValueError):
            raise SpecError(f"{where}: period must be an integer")
        if not (1 <= p <= _MAX_PERIOD):
            raise SpecError(f"{where}: period {p} out of range 1..{_MAX_PERIOD}")
        clean["period"] = p
    return clean


def _validate_condition(c: dict, where: str) -> dict:
    if not isinstance(c, dict):
        raise SpecError(f"{where}: condition must be an object")
    op = c.get("op")
    if op not in _OPS:
        raise SpecError(f"{where}: unknown operator {op!r}")
    return {
        "left": _validate_operand(c.get("left", {}), f"{where}.left"),
        "op": op,
        "right": _validate_operand(c.get("right", {}), f"{where}.right"),
    }


def validate_spec(spec: dict) -> dict:
    """Validate + normalize a strategy spec. Raises SpecError on anything unsafe."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    direction = str(spec.get("direction", "long")).lower()
    if direction not in _DIRECTIONS:
        raise SpecError(f"direction must be one of {_DIRECTIONS}")

    entry = spec.get("entry") or []
    exit_ = spec.get("exit") or []
    if not isinstance(entry, list) or not entry:
        raise SpecError("entry must be a non-empty list of conditions")
    if not isinstance(exit_, list):
        raise SpecError("exit must be a list of conditions")
    if len(entry) > _MAX_CONDITIONS or len(exit_) > _MAX_CONDITIONS:
        raise SpecError(f"too many conditions (max {_MAX_CONDITIONS} each)")

    def _pct(key):
        v = spec.get(key)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise SpecError(f"{key} must be numeric or null")
        if not (0 < f <= 100):
            raise SpecError(f"{key} must be in (0, 100]")
        return round(f, 4)

    return {
        "symbol": str(spec.get("symbol", "BTCUSDT")).upper(),
        "interval": str(spec.get("interval", "1h")),
        "direction": direction,
        "entry": [_validate_condition(c, f"entry[{i}]") for i, c in enumerate(entry)],
        "exit": [_validate_condition(c, f"exit[{i}]") for i, c in enumerate(exit_)],
        "stop_loss_pct": _pct("stop_loss_pct"),
        "take_profit_pct": _pct("take_profit_pct"),
        "name": str(spec.get("name", "Custom strategy"))[:80],
    }


# --- operand computation (series) --------------------------------------------

def compute_operand(df: pd.DataFrame, o: dict) -> pd.Series:
    """Return a full-length series for an operand, aligned to df's index."""
    kind = o["indicator"]
    close = df["close"]
    if kind == "price":
        return close
    if kind == "value":
        return pd.Series(o["value"], index=df.index)
    if kind == "rsi":
        return ind.rsi(close, o["period"])
    if kind == "ema":
        return ind.ema(close, o["period"])
    if kind == "sma":
        return ind.sma(close, o["period"])
    if kind == "atr_pct":
        a = ind.atr(df["high"], df["low"], close, 14)
        return a / close * 100
    if kind in ("macd", "macd_signal", "macd_hist"):
        m = ind.macd(close)
        return m["hist" if kind == "macd_hist" else "signal" if kind == "macd_signal" else "macd"]
    if kind == "volume_ratio":
        p = o["period"]
        return df["volume"] / df["volume"].rolling(p).mean()
    raise SpecError(f"cannot compute operand {kind!r}")


def _eval_condition(df: pd.DataFrame, cond: dict) -> pd.Series:
    """Boolean series: True where the condition holds at each bar (on close)."""
    left = compute_operand(df, cond["left"])
    right = compute_operand(df, cond["right"])
    op = cond["op"]
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    if op == "cross_above":
        return (left > right) & (left.shift(1) <= right.shift(1))
    if op == "cross_below":
        return (left < right) & (left.shift(1) >= right.shift(1))
    raise SpecError(f"cannot evaluate op {op!r}")


def _combine(df: pd.DataFrame, conds: list[dict], how: str) -> pd.Series:
    """AND (entry) or OR (exit) a list of condition series."""
    if not conds:
        return pd.Series(False, index=df.index)
    series = [_eval_condition(df, c) for c in conds]
    out = series[0].fillna(False)
    for s in series[1:]:
        s = s.fillna(False)
        out = (out & s) if how == "and" else (out | s)
    return out


# --- backtest ----------------------------------------------------------------

def backtest(df: pd.DataFrame, spec: dict) -> dict:
    """Deterministic, look-ahead-safe backtest.

    Signals evaluate on bar close; fills happen at the NEXT bar's open. One
    position at a time. Returns trades, an equity curve, stats, and a buy&hold
    comparison over the same window.
    """
    spec = validate_spec(spec)
    df = df.reset_index(drop=True)
    n = len(df)
    if n < 30:
        return {"error": "not enough bars to backtest", "stats": None,
                "trades": [], "equity_curve": []}

    opens = df["open"].tolist()
    closes = df["close"].tolist()
    long_ok = spec["direction"] in ("long", "both")
    short_ok = spec["direction"] in ("short", "both")

    entry_sig = _combine(df, spec["entry"], "and").tolist()
    exit_sig = _combine(df, spec["exit"], "or").tolist() if spec["exit"] else [False] * n

    sl = spec["stop_loss_pct"]
    tp = spec["take_profit_pct"]

    trades: list[dict] = []
    equity = 1.0                 # normalized equity (1.0 = starting capital)
    equity_curve: list[float] = []
    position = None              # {"side", "entry_price", "entry_i"}
    bars_in_market = 0

    for i in range(n):
        # Mark-to-market the open position on this bar's close for the equity curve.
        if position is not None:
            side = position["side"]
            ep = position["entry_price"]
            cur = closes[i]
            ret = (cur - ep) / ep if side == "long" else (ep - cur) / ep
            equity_curve.append(round(equity * (1 + ret), 6))
        else:
            equity_curve.append(round(equity, 6))

        # --- manage an open position: stop/target intrabar, else exit signal ---
        if position is not None:
            side = position["side"]
            ep = position["entry_price"]
            exit_price = None
            reason = None
            hi, lo = df["high"][i], df["low"][i]
            if side == "long":
                if sl is not None and lo <= ep * (1 - sl / 100):
                    exit_price, reason = ep * (1 - sl / 100), "stop"
                elif tp is not None and hi >= ep * (1 + tp / 100):
                    exit_price, reason = ep * (1 + tp / 100), "target"
            else:  # short
                if sl is not None and hi >= ep * (1 + sl / 100):
                    exit_price, reason = ep * (1 + sl / 100), "stop"
                elif tp is not None and lo <= ep * (1 - tp / 100):
                    exit_price, reason = ep * (1 - tp / 100), "target"
            # Rule-based exit signal fires on THIS close -> fill next open (or last close).
            if exit_price is None and exit_sig[i]:
                exit_price = opens[i + 1] if i + 1 < n else closes[i]
                reason = "signal"
            if exit_price is not None:
                ret = (exit_price - ep) / ep if side == "long" else (ep - exit_price) / ep
                equity *= (1 + ret)
                trades.append({
                    "side": side,
                    "entry_i": position["entry_i"], "exit_i": i,
                    "entry_price": round(ep, 6), "exit_price": round(exit_price, 6),
                    "return_pct": round(ret * 100, 3), "reason": reason,
                })
                position = None

        if position is not None:
            bars_in_market += 1

        # --- open a new position: entry signal on this close -> fill next open ---
        if position is None and entry_sig[i] and i + 1 < n:
            fill = opens[i + 1]
            if long_ok:
                position = {"side": "long", "entry_price": fill, "entry_i": i + 1}
            elif short_ok:
                position = {"side": "short", "entry_price": fill, "entry_i": i + 1}

    stats = _compute_stats(trades, equity, closes, bars_in_market, n)
    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "stats": stats,
        "error": None,
    }


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0] if curve else 1.0
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return round(mdd * 100, 2)


def _compute_stats(trades: list[dict], equity: float, closes: list[float],
                   bars_in_market: int, n: int) -> dict:
    wins = [t for t in trades if t["return_pct"] > 0]
    losses = [t for t in trades if t["return_pct"] < 0]
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = -sum(t["return_pct"] for t in losses)
    total_return = round((equity - 1.0) * 100, 2)
    buy_hold = round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes and closes[0] else 0.0

    # Equity curve for drawdown from realized trades (compounded).
    eq = 1.0
    curve = [1.0]
    for t in trades:
        eq *= (1 + t["return_pct"] / 100)
        curve.append(eq)

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "total_return_pct": total_return,
        "buy_hold_return_pct": buy_hold,
        "vs_buy_hold_pct": round(total_return - buy_hold, 2),
        "avg_win_pct": round(gross_win / len(wins), 3) if wins else None,
        "avg_loss_pct": round(-gross_loss / len(losses), 3) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_trade_pct": round(sum(t["return_pct"] for t in trades) / len(trades), 3) if trades else None,
        "max_drawdown_pct": _max_drawdown(curve),
        "exposure_pct": round(bars_in_market / n * 100, 1) if n else 0.0,
    }


# --- natural language -> spec ------------------------------------------------

NL_SYSTEM_PROMPT = f"""You translate a trader's plain-English strategy idea into a STRICT
JSON rule-spec. You do NOT backtest or predict — you only produce the spec. A
deterministic engine validates and runs it.

Allowed indicators (with params):
  price (no params), value (needs "value"), rsi (period), ema (period), sma (period),
  atr_pct (no params), macd, macd_signal, macd_hist (no params), volume_ratio (period).
Allowed operators: "<", ">", "<=", ">=", "cross_above", "cross_below".

An operand is {{"indicator": <name>, "period": <int, if the indicator takes one>}}
or {{"indicator": "value", "value": <number>}}.
A condition is {{"left": <operand>, "op": <operator>, "right": <operand>}}.

Rules:
- entry: list of conditions, ALL must be true to enter (logical AND).
- exit: list of conditions, ANY true to exit (logical OR). Can be empty if using only stops.
- Use stop_loss_pct / take_profit_pct (percent, e.g. 3 = 3%) when the user mentions stops/targets, else null.
- Only use the allowed indicators/operators. Do NOT invent indicators (no bollinger, no stochastic, etc.).
  If the user asks for something unsupported, approximate with the closest allowed indicator and note it in "name".
- Keep it to at most 6 entry and 6 exit conditions.

Respond ONLY with valid JSON in exactly this shape:
{{
  "name": "<short human name of the strategy>",
  "direction": "long" | "short" | "both",
  "entry": [<condition>, ...],
  "exit": [<condition>, ...],
  "stop_loss_pct": <number|null>,
  "take_profit_pct": <number|null>
}}"""


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def nl_to_spec(prompt: str, symbol: str, interval: str,
               router: AIRouter | None = None) -> tuple[dict, float]:
    """Translate NL -> validated spec via the STRATEGY_BUILDER (premium) tier.

    Returns (validated_spec, cost_usd). Raises SpecError if the model produced an
    invalid/unsafe spec.
    """
    router = router or AIRouter()
    raw = router.complete(
        TaskClass.STRATEGY_BUILDER,
        f"Trader's idea: {prompt}\n\nProduce the strategy spec as JSON only.",
        system=NL_SYSTEM_PROMPT, max_tokens=800,
    )
    try:
        parsed = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        raise SpecError(f"model did not return valid JSON: {e}") from e
    parsed["symbol"] = symbol
    parsed["interval"] = interval
    spec = validate_spec(parsed)
    return spec, round(router.cost_log.total_usd, 5)


def _humanize(spec: dict) -> list[str]:
    """Human-readable rule lines for the UI (no LLM)."""
    def op_word(o):
        return {"<": "<", ">": ">", "<=": "≤", ">=": "≥",
                "cross_above": "crosses above", "cross_below": "crosses below"}[o]

    def operand_word(o):
        k = o["indicator"]
        if k == "price":
            return "price"
        if k == "value":
            return str(o["value"])
        if "period" in o:
            return f"{k.upper()}({o['period']})"
        return k.upper()

    def cond_line(c):
        return f"{operand_word(c['left'])} {op_word(c['op'])} {operand_word(c['right'])}"

    lines = [f"Direction: {spec['direction']}"]
    lines += [f"ENTER when ALL: " + " AND ".join(cond_line(c) for c in spec["entry"])]
    if spec["exit"]:
        lines += [f"EXIT when ANY: " + " OR ".join(cond_line(c) for c in spec["exit"])]
    if spec["stop_loss_pct"]:
        lines += [f"Stop loss: {spec['stop_loss_pct']}%"]
    if spec["take_profit_pct"]:
        lines += [f"Take profit: {spec['take_profit_pct']}%"]
    return lines


def build_strategy(nl: str, symbol: str = "BTCUSDT", interval: str = "1h",
                   candles: int = 500, router: AIRouter | None = None,
                   df: pd.DataFrame | None = None) -> dict:
    """Full pipeline: NL -> validated spec -> backtest on real OHLCV -> stats.

    `df` may be injected for tests; otherwise fetched live for the symbol.
    """
    router = router or AIRouter()
    spec, cost = nl_to_spec(nl, symbol, interval, router=router)

    if df is None:
        from backend.data.providers import get_provider
        df = get_provider(symbol).fetch_klines(symbol, interval, candles)

    bt = backtest(df, spec)
    return {
        "spec": spec,
        "rules_human": _humanize(spec),
        "stats": bt["stats"],
        "trades": bt["trades"][-50:],       # cap payload
        "equity_curve": bt["equity_curve"],
        "error": bt.get("error"),
        "disclaimer": STRATEGY_DISCLAIMER,
        "cost_usd": cost,
    }
