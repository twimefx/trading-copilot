# AI Trading Copilot

> AI intelligence terminal for retail traders. **Crypto + Forex** (MVP), stocks later.
> The product is market *intelligence, explainability, and coaching* — signals are assistive, **not** guaranteed. Not financial advice.

## Status: Phase 2 — Retention (in progress)

| Component | Status |
|---|---|
| Kronos forecasting (real BTC, end-to-end) | ✅ validated locally |
| Binance OHLCV ingestion | ✅ working |
| LLM model router (multi-provider, cost-routed) | ✅ Claude + OpenAI + DeepSeek |
| AI Market Copilot | ✅ live |
| Market Scanner (rule-based screen) | ✅ live |
| Trade Journal + AI behavioral coaching | ✅ live |
| Portfolio Copilot (open-book risk read) | ✅ live |
| Multi-Agent Debate Engine (7 agents + judge, consensus vote) | ✅ live (Premium) |
| Institutional Flow Dashboard (funding/OI/L-S/taker flow) | ✅ live (Premium) |
| AI Strategy Builder (NL → rules → real backtest) | ✅ live (Premium) |
| Market Replay (historical Copilot/Debate + honest outcome) | ✅ live (Premium) |
| Forex (Oanda): Copilot/Debate/Strategy parity + retail position-book flow | ✅ live |
| Alerts (price + scanner-lean rules → Telegram/email, 15-min scheduler) | ✅ live |
| Signal track record (every Copilot call logged + honestly scored 24 periods later) | ✅ live |
| Per-user saved watchlists | ✅ live |
| Weekly LLM cost digest (Telegram) | ✅ live |
| Kronos range service (separate Railway service, torch) | ✅ deployed |
| Clerk auth + Stripe billing + tier quotas | ✅ auth live; Stripe test mode |
| Monorepo scaffold | ✅ |

## Architecture

```
Market data (Binance/Oanda) → Kronos forecast → LLM reasoning (model-routed)
   → Signal + Explainability engine → Dashboard + alerts
   → Execution (opt-in, paper-first, LATER)
```

## Layout

```
backend/
  ai/router.py        # provider-agnostic LLM router (Claude/GPT/DeepSeek by task class)
  data/binance.py     # crypto OHLCV ingestion
  signals/            # signal + explainability engine (Phase 1)
  tests/
frontend/             # Next.js + Tailwind + shadcn (Phase 1)
infra/                # docker-compose (postgres/timescale, redis), deploy config
spikes/               # throwaway validation (kronos_btc_spike.py)
vendor/Kronos/        # cloned forecasting model (gitignored)
.hermes/plans/        # build plans
```

## Dev setup

```bash
uv venv .venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY etc.

# Clone the forecasting model (gitignored)
git clone --depth 1 https://github.com/shiyu-coder/Kronos.git vendor/Kronos

# Validate the forecasting core
python spikes/kronos_btc_spike.py
```

## LLM cost routing

| Task | Model | Why |
|---|---|---|
| Market copilot, explanations, strategy, consensus | Claude Opus | best reasoning |
| Signal summaries | Claude Sonnet | fast/cheaper |
| Bulk market scans | DeepSeek | cheapest at scale |

Per-call token cost is logged so spend is visible as we scale.

## Honest disclaimer
This software provides analysis and assistive signals. It does **not** guarantee profit and is **not** financial advice. Trading involves substantial risk of loss.
