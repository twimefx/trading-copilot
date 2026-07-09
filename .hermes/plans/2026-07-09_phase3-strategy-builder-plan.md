# Phase 3 — AI Strategy Builder (Implementation Plan)

> **Status:** Debate Engine (7 agents) + Institutional Flow shipped. This is the LAST Phase 3 feature.
> Built while Tim sets up Stripe.

**Goal:** Natural language → a STRUCTURED, validated strategy rule-spec → a REAL
deterministic backtest on live OHLCV → honest performance stats. The LLM only
translates NL into a typed spec that our engine validates and runs; the backtest
is pure code, so results are never LLM-fabricated.

## Honesty design (non-negotiable)
- LLM output is a rule-spec (JSON), NOT results. We validate the spec against a
  strict schema (known indicators, operators, params) and REJECT anything unknown
  — no eval, no arbitrary code. The LLM cannot invent a metric or a number.
- Backtest computes indicators with our existing functions (ema/sma/rsi/macd/atr),
  steps bar-by-bar, applies entries/exits, and reports trades + equity curve.
- Stats (win rate, P&L, max drawdown, Sharpe-ish, buy&hold comparison) are computed
  deterministically from the trade list. Heavy disclaimer: past-performance / overfit.
- Look-ahead safe: signals use bar close; fills at NEXT bar open (no peeking).

## Rule spec (typed, validated)
```
{
  "symbol": "BTCUSDT", "interval": "1h",
  "direction": "long" | "short" | "both",
  "entry":  [ {left, op, right}, ... ]   # ALL must be true to enter (AND)
  "exit":   [ {left, op, right}, ... ]   # ANY true to exit (OR)
  "stop_loss_pct": float|null, "take_profit_pct": float|null
}
operand: {"indicator": "rsi|ema|sma|macd_hist|price|atr_pct|value", "period": int?, "value": float?}
op: "<" | ">" | "cross_above" | "cross_below"
```

## Tasks
1. `backend/signals/strategy.py`
   - SPEC schema + `validate_spec()` (strict; unknown indicator/op/param -> ValueError)
   - `compute_operand()` -> series for each operand type (reuse indicators.py)
   - `backtest(df, spec)` -> {trades[], equity_curve, stats, buy_hold} — pure, testable,
     look-ahead safe (signal on close, fill next open)
   - `nl_to_spec(prompt, symbol, interval, router)` -> LLM (STRATEGY_BUILDER tier) returns
     spec JSON; we validate before use
   - `build_strategy(nl, symbol, interval, router)` -> nl_to_spec -> fetch klines -> backtest
     -> {spec, stats, trades, equity_curve, disclaimer, cost}
2. `POST /strategy {prompt, symbol, interval}` — Premium-gated (new F_STRATEGY), guards + cache.
3. Frontend: Strategy tab — NL input, resulting rules (human-readable), stats cards,
   equity curve vs buy&hold, trades table, Premium upsell.
4. Tests: spec validation (reject junk), backtest math (known crossover series),
   look-ahead safety, stats correctness, nl_to_spec mocked, endpoint gating/cache.
5. Commit + push (auto-deploy) + verify prod.

## Stats reported
trades, win_rate, total_return_pct, avg_win/avg_loss, profit_factor, max_drawdown_pct,
avg_trade_pct, exposure, vs buy&hold return. All from the deterministic trade list.
