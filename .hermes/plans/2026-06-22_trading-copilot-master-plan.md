# AI Trading Copilot — Master Build Plan

> **Status:** Strategic plan. Per-phase bite-sized task breakdowns are written at the start of each phase.
> **Owner:** Tim (founder / financial-market analyst & trader)
> **Builder:** Hermes Agent

**Goal:** Build an "AI Bloomberg Terminal for retail traders" — an intelligence/explainability/coaching platform where AI-driven market reasoning is the hero and signals are a byproduct. MVP focuses on **Crypto + Forex**; Stocks/ETFs added later.

**Architecture (one line):** Market data → Kronos forecasting → LLM reasoning layer (model-routed) → Signal/Explainability engine → Dashboard + alerts; execution is opt-in, paper-first.

---

## ⚠️ PHASE 0 VALIDATION FINDING (2026-06-22) — shapes everything

**40-window rolling backtest on real BTC/USDT (base Kronos-small, no fine-tuning):**
- Final-direction accuracy: **35%** (worse than coin flip — NOT a directional oracle)
- Per-hour direction: **51.4%** (≈ coin flip)
- Price MAPE: **2.08%** (median 1.66%) — **genuinely accurate at price LEVEL / range**

**Decision:** Kronos is repositioned as a **volatility / range / magnitude forecaster**, NOT a buy/sell signal source. It is good at "where price will likely sit (range)" and bad at "which way." Therefore:
1. Direction comes from the **reasoning layer synthesizing multiple inputs** (technicals, funding/OI, sentiment, macro) — Kronos is ONE weighted voice, used for range/risk not direction.
2. Kronos output feeds **risk sizing, stop/target placement, and the explainability engine** (its strength).
3. Crypto fine-tuning is a Phase 2 EXPERIMENT (scripts in vendor/Kronos/finetune), not a product blocker.

This validates the core thesis: **the moat is intelligence + risk + explainability, not raw signal accuracy.**

---

## Guiding Principles (non-negotiable)

1. **The moat is intelligence, not raw signal accuracy.** No model reliably predicts price profitably out-of-sample. We win on explainability, coaching, portfolio intelligence, and risk — so the business survives mediocre signal accuracy.
2. **Provider-agnostic AI from day one.** A `model router` layer switches Claude / Hermes / GPT / DeepSeek by task class for cost control.
3. **Execution is dangerous.** MVP = signals/alerts only. Real-money auto-execution is opt-in, paper-trading first, users' own API keys, and gated behind legal review.
4. **Ship the MVP fast (4–6 weeks), then iterate on retention.** Avoid the 12-month-no-launch trap.
5. **TDD + frequent commits + per-task review.**

---

## Confirmed Decisions

| Decision | Choice |
|---|---|
| MVP markets | **Crypto + Forex** (stocks later) |
| Forecasting engine | **Kronos** (open-source, AAAI 2026, HuggingFace models — mini/small/base run on CPU/cheap GPU) |
| Reasoning layer | LLM via model router (the "Hermes" box in the ChatGPT plan = whatever LLM does reasoning) |
| Dev machine | This box (2 CPU / 7.8GB RAM, no GPU) = **dev/staging only** |
| Production hosting | Frontend → Vercel; Backend → Railway/Fly; Kronos → cheap GPU host (RunPod/Lambda) when needed |

## Pending From Tim

- [ ] Anthropic (Claude) API key
- [ ] HuggingFace account (for Kronos model download)
- [ ] GitHub username (repo target) — or keep local for now

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js + Tailwind + shadcn/ui |
| Backend | FastAPI (Python) |
| Forecasting | Kronos (`NeoQuasar/Kronos-small` to start) |
| Reasoning | Claude Opus (heavy) / cheaper models (bulk) via router |
| DB | Postgres + TimescaleDB (Supabase for managed option) |
| Cache/Queue | Redis |
| Market data | Binance/Bybit (crypto, free), Oanda (forex) |
| Charts | TradingView widget + webhook ingestion |
| Payments | Stripe |
| Exec (later) | CCXT + broker APIs, paper-first |

---

## LLM Model Router (Tim's key requirement)

Route by task class to balance quality vs. cost:

| Task class | Model tier | Rationale |
|---|---|---|
| Market Copilot, trade explanations, strategy builder, agent consensus | **Claude Opus** (premium) | Best reasoning |
| Signal summaries, structured output, tool calls | Mid (GPT-class / Hermes) | Fast, cheap-ish |
| Large market scans, bulk backtest analysis | **DeepSeek** (cheap) | Cost at scale |

Implemented as `backend/ai/router.py` — config-driven, swappable, with per-call cost logging.

---

## Phased Roadmap

### Phase 0 — Foundation (this week)
- Repo scaffold (monorepo: `frontend/`, `backend/`, `infra/`)
- Validate Kronos locally: install, download `Kronos-small`, run a BTC forecast end-to-end (SPIKE before committing).
- Crypto data ingestion (Binance OHLCV) → Postgres/TimescaleDB.
- LLM router skeleton + cost logging.

### Phase 1 — MVP (4–6 weeks) "Launch Fast"
- Dashboard (live charts, watchlist)
- AI Market Copilot (Claude) — "Why is BTC bullish?"
- Signal engine (Kronos forecast → LLM reasoning → BUY/SELL/HOLD + confidence)
- **Explainability engine** (trend/momentum/volume/sentiment → final confidence)
- TradingView widget embed + webhook ingestion
- Stripe billing (Free / Pro tiers)
- Forex via Oanda added here

### Phase 2 — Retention (Month 2–3)
- Portfolio Copilot
- AI Trade Journal (+ behavioral coaching)
- Market Scanner (uses DeepSeek for bulk scans)

### Phase 3 — Premium (Month 3–6)
- Multi-Agent Debate Engine (Kronos/Reasoning/News/Macro/Risk agents → consensus vote)
- Institutional Flow Dashboard (funding, OI, liquidations, whale activity)
- AI Strategy Builder (NL → entry/exit/backtest)

### Phase 4 — Platform (Month 6+)
- Strategy Marketplace
- Market Replay Mode

---

## Revenue Model (from ChatGPT plan, sound)

- **Free:** delayed signals, limited watchlist
- **Pro ($49–99/mo):** live signals, scanner, journal, portfolio copilot
- **Premium ($199–499/mo):** market copilot, institutional flow, strategy builder, multi-agent

---

## Risks & Open Questions

| Risk | Mitigation |
|---|---|
| Signal accuracy disappoints | Position as intelligence platform; signals assistive not guaranteed; heavy disclaimers |
| Regulatory exposure (auto-execution, "financial advice") | Signals-only MVP; legal review before execution; clear "not financial advice" disclaimers |
| Kronos forecast quality on crypto/forex unknown | Phase 0 SPIKE validates before we build on it; backtest honestly |
| LLM cost blowout | Model router + caching + cost logging from day one |
| This dev box too small for prod | Deploy to managed hosts; never serve users from here |
| Vendor lock-in | Provider-agnostic router |

## Open Questions for Tim
1. Anthropic + HuggingFace accounts ready? (pending)
2. GitHub username / push target?
3. Comfortable with the honest "intelligence platform, signals are assistive" framing?
4. Coding alongside or full-drive-and-review?
5. Monthly infra budget ceiling?

---

## Tooling / Skills To Use During Build
- `spike` — validate Kronos before building on it
- `test-driven-development` — tests before code
- `claude-code` / `codex` / `opencode` — delegate heavy code generation
- `github-pr-workflow`, `github-repo-management` — repo + PRs + CI
- `systematic-debugging` — root-cause bugs
- `serving-llms-vllm` / `llama-cpp` / `huggingface-hub` — model hosting if self-hosting
- terminal / file / web / browser / cron — scaffolding, pipelines, scheduled jobs, monitoring
