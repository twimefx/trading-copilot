# AI Market Copilot Architecture

## Product boundary

AI Market Copilot is a research and decision-support platform. It does not provide investment advice, promise returns, or permit a language model to invent financial values. The current production-oriented vertical slice supports crypto, spot FX, and US equities; it is deliberately research-first and has no live trading path.

## System overview

```text
Browser / Next.js terminal
  -> /api rewrite
  -> FastAPI API
      -> authentication + entitlement + rate / spend guards
      -> HERMES orchestration boundary (MarketContext assembly + AI Copilot)
          -> MarketDataProvider adapter
          -> deterministic technical / structure calculations
          -> optional Kronos range service
          -> KRONOS deterministic consensus engine
          -> LLM interpretation of the verified context
      -> journal / signal-history persistence
```

## Applications and deployment

- `frontend/`: Next.js/TypeScript terminal UI. Production builds route `/api/*` to `BACKEND_URL` through `frontend/next.config.js`.
- `backend/api/main.py`: FastAPI REST API, OpenAPI docs, authentication, billing, feature gates, rate limits, and API-level error handling.
- Railway hosts the lean FastAPI service from the root `Dockerfile`. It intentionally excludes Torch/Kronos model weights.
- Vercel hosts the frontend. The frontend must set `BACKEND_URL` to the Railway service domain; Railway must set `FRONTEND_ORIGIN` to the Vercel URL.
- A separate RunPod GPU service can provide the optional Kronos volatility/range forecast via `KRONOS_SERVICE_URL`. The API degrades to an ATR estimate if it is unavailable.

## HERMES orchestration boundary

The current HERMES boundary is `backend/signals/context.py` plus `backend/signals/copilot.py`:

1. Select the data provider using `backend/data/providers.py`.
2. Retrieve and normalize provider data to a common OHLCV shape.
3. Calculate deterministic indicators and price structure in `backend/data/indicators.py`.
4. Gather provider metadata, source provenance, and explicitly unavailable inputs.
5. Optionally retrieve a Kronos range forecast. It is a range/volatility input only, never directional evidence.
6. Pass the verified `MarketContext` to the LLM router for constrained explanation.
7. Merge code-owned facts (range, provenance, consensus) after the LLM response.

The LLM has no shell, database, or arbitrary-tool access. It owns plain-language interpretation only.

## Market-data provider abstraction

Every adapter provides `fetch_klines`, `fetch_funding_rate`, and `fetch_open_interest`, returning a common candle frame:

```text
timestamps, open, high, low, close, volume, amount
```

Current adapters:

| Asset class | Adapter | Notes |
| --- | --- | --- |
| Crypto | `backend/data/binance.py` | Binance public mirror for spot; derivatives fallback chain is explicit about partial/stale coverage. |
| FX / metals | `backend/data/oanda.py` | Oanda v20 REST API; provider token required; no perp funding/OI. |
| US equity | `backend/data/yahoo.py` | Yahoo Finance chart endpoint; normalized OHLCV plus source metadata. Funding/OI are explicitly unavailable. This is an MVP adapter and should be replaced or supplemented with a licensed provider before institutional-scale use. |

All raw values presented to the user must include source/freshness metadata. Missing fundamentals/news are represented as unavailable; no inference is fabricated from a price feed.

## KRONOS consensus

`backend/signals/kronos_consensus.py` implements the first deterministic KRONOS consensus interface.

- Technical: price vs moving averages, RSI, MACD.
- Quant: volume confirmation and deterministic price structure.
- Regime: structure and realized-volatility context.
- Risk: ATR and extension risk.
- Fundamental, macro, and sentiment are represented explicitly as unavailable until their verified provider adapters exist.

The engine emits component score, direction, confidence, evidence, source, timestamp, configurable weight, overall signal, consensus confidence, and model probability.

`consensus_confidence` measures evidence coverage/agreement. It is not a calibrated probability of profit. `model_probability` is kept separate and must not be interpreted as a return guarantee.

## NVDA vertical slice

The shipped equity workflow is:

```text
NVDA search
 -> Yahoo Finance chart data
 -> normalized OHLCV + source metadata
 -> indicators + price structure
 -> optional Kronos range / ATR fallback
 -> deterministic KRONOS component consensus
 -> constrained Copilot explanation
 -> UI provenance + evidence rendering
 -> journal snapshot persistence
```

The UI exposes an NVDA preset, a NASDAQ TradingView chart mapping, component-level KRONOS evidence, provenance, and the limits of the current fundamental/news data coverage.

## Persistence

The current persistence compatibility layer is `backend/journal/store.py`:

- PostgreSQL when `DATABASE_URL` is configured (production).
- SQLite when it is absent (local development and tests).

Journal entries preserve the full analysis snapshot. Signal calls are recorded in `signal_history` and later scored deterministically against subsequent market prices. User/billing/watchlist/alerts stores use the same database abstraction.

## Security and safety controls

- Clerk JWT verification when auth is configured.
- Per-user quotas, per-IP rate limits, result cache, and daily LLM-spend guard.
- Stripe webhook signature verification.
- Server-only provider and broker secrets.
- Explicit feature flags/tier gates for paid features.
- No live execution by default; no broker credentials are sent to the browser.
- AI outputs always carry a research-only/not-financial-advice disclaimer.

## Next increments

1. Add a licensed equity fundamentals, earnings, news, and macro adapter behind the same provider interface.
2. Persist formal `signals` and `signal_components` tables/migrations rather than only storing the analysis snapshot in the journal.
3. Move in-memory cache/rate/spend guards to Redis before horizontal scaling.
4. Add a background job queue for ingestion, research, and report generation.
5. Add reproducible backtests, walk-forward testing, and robustness scoring before any execution capability is introduced.
6. Add organizations/RBAC/audit-log expansion and production observability/tracing.
