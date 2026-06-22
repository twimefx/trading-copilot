# Phase 1 — AI Market Copilot (Implementation Plan)

> **For Hermes:** Build task-by-task, TDD where sensible, commit after each task.

**Goal:** Ship the hero feature — an AI Market Copilot that answers "Why is BTC bullish/bearish right now?" by synthesizing live market data + technical indicators + Kronos range forecast into a transparent, explainable conviction call. Crypto first (Binance), forex (Oanda) right after.

**Key design (from Phase 0 finding):** Kronos provides RANGE/volatility only. DIRECTION + conviction come from the Opus reasoning layer synthesizing multiple inputs. Every answer is explainable (no black box) and carries a "not financial advice" disclaimer.

**Architecture:**
```
Binance OHLCV ──► indicators (RSI/MACD/vol/ATR) ─┐
Binance funding/OI ──────────────────────────────┤
Kronos range forecast (24h likely band) ─────────┼─► MarketContext (structured)
                                                  │
                          MarketContext ──► AIRouter[MARKET_COPILOT=Opus] ──► answer
                                                  (conviction + reasoning + range + disclaimer)
```

---

## Task 1: Technical indicators module
**Objective:** Compute RSI, MACD, SMA/EMA, ATR, volume trend from OHLCV (pure pandas/numpy, no heavy TA lib).
**Files:** Create `backend/data/indicators.py`, `backend/tests/test_indicators.py`
- TDD: test RSI on a known series (e.g. classic Wilder example) → implement → pass.
- Functions: `rsi(close, 14)`, `macd(close)`, `ema(series, n)`, `sma(series, n)`, `atr(high, low, close, 14)`, `volume_trend(volume)`.

## Task 2: Funding rate + open interest fetch
**Objective:** Pull perp funding rate + open interest from Binance Futures public API.
**Files:** Modify `backend/data/binance.py` (add `fetch_funding_rate`, `fetch_open_interest`).
- These feed the reasoning layer (sentiment/positioning) — Kronos can't see them.

## Task 3: Kronos range adapter
**Objective:** Wrap Kronos to return a clean 24h RANGE forecast (not direction): `{low, high, expected_close, mape_confidence}`.
**Files:** Create `backend/signals/kronos_range.py`
- Use sample_count>1 to get a distribution → percentile band (e.g. p10–p90).
- Cache model load (load once, reuse).

## Task 4: MarketContext builder
**Objective:** Assemble all inputs into one structured object the LLM reasons over.
**Files:** Create `backend/signals/context.py`, `backend/tests/test_context.py`
- `build_market_context(symbol)` → dataclass with price, indicators, funding, OI, Kronos range.
- Serializes to a compact dict for the prompt.

## Task 5: Copilot reasoning engine
**Objective:** Feed MarketContext to Opus via the router, get structured conviction + explanation.
**Files:** Create `backend/signals/copilot.py`
- System prompt: "You are a market analyst. Synthesize the data. Kronos gives RANGE not direction. Output: direction lean, conviction 0-100, key drivers, risks, the range, and a disclaimer. Never guarantee profit."
- Returns structured: `{lean, conviction, drivers[], risks[], range, explanation, disclaimer}`.

## Task 6: CLI to prove it end-to-end
**Objective:** `python -m backend.copilot_cli BTCUSDT` → prints a full Copilot analysis on live data.
**Files:** Create `backend/copilot_cli.py`
- This is the demoable milestone. Run it, verify a real coherent answer.

## Task 7: FastAPI endpoint
**Objective:** `POST /copilot {symbol}` → JSON analysis. Foundation for the UI.
**Files:** Create `backend/api/main.py`, `backend/tests/test_api.py`
- Health check + the copilot route. Run uvicorn, curl it.

## Task 8: Commit + checkpoint with Tim
- Demo the CLI + API output. Decide UI next (Next.js dashboard) vs. more backend (signal engine, scanner).

---

## Validation gates
- Indicators match reference values (unit tests)
- Kronos range adapter returns sane bands on live BTC
- Copilot produces a coherent, explainable answer on live data (manual review)
- API returns valid JSON, health check passes
- Cost per Copilot call logged & reasonable (<$0.05 expected with Opus)

## Risks
- Opus cost per call — monitor via router cost log; consider Sonnet for cheaper tier later.
- Binance Futures API rate limits — cache, back off.
- Indicator correctness — TDD against known values.
