# Phase 3 — Multi-Agent Debate Engine (Implementation Plan)

> **For Hermes:** TDD, deterministic orchestration, agents grounded on real MarketContext.
> **Status:** Phase 2 shipped (coaching, portfolio, scanner). Router multi-provider + resilient fallback live. DeepSeek working; OpenAI keyed but no quota (degrades to Anthropic).

**Goal:** The flagship Premium feature. Several specialized AI "agents" each analyze
the SAME asset from a distinct lens over the SAME real MarketContext, then a Judge
synthesizes their (often conflicting) votes into a consensus verdict with an honest
confidence and a transparent record of who agreed/disagreed and why.

This is the product's differentiator: not one opinion, but a panel — and the
disagreement itself is signal (high spread = low conviction, shown honestly).

## Why it uses the router hardest (cost design)
- Cheap agents → DeepSeek (MARKET_SCAN tier). Many calls, bulk, cheapest.
- Judge / synthesis → Claude Opus (AGENT_CONSENSUS tier). One call, best reasoning.
- Falls back to Anthropic automatically if a provider is down/unfunded (already hardened).

## Agents (each grounds on MarketContext — NO vibes)
| Agent | Lens | Grounds on |
|---|---|---|
| Trend | trend/structure | EMA20/50, SMA200, price structure |
| Momentum | momentum/exhaustion | RSI, MACD hist, ATR% |
| Positioning | crowd/sentiment | funding rate, open interest |
| Volatility/Range | risk & likely band | Kronos range (or ATR band), atr_pct |
| Contrarian | devil's advocate | deliberately argues the OTHER side of the emerging consensus |

Each agent returns: `{lean: bull/bear/neutral, conviction 0-100, rationale, key_evidence[]}`.
Contrarian runs AFTER the others so it can push back on the majority (real debate).

## Consensus (deterministic + judged)
1. Deterministic vote tally: weighted mean lean, agreement spread (stdev of convictions
   + directional disagreement), majority direction. This is computed in code — the
   "vote" is auditable, not LLM-invented.
2. Judge (Opus) reads all agent cards + the deterministic tally and writes the final
   verdict: `{consensus_lean, confidence, synthesis, dissent (who disagreed & why),
   what_would_change_our_mind}`. Confidence is DOWN-WEIGHTED by disagreement spread.
3. Honesty: if agents split badly, confidence is low and we SAY the panel is divided.

## Tasks
1. `backend/signals/agents.py` — agent definitions (system prompts + lenses), `run_agent()`,
   `run_panel()` (parallel cheap agents, then contrarian). Router-driven, mockable.
2. `backend/signals/debate.py` — `tally_votes()` (deterministic, pure/testable),
   `debate()` orchestrator (build context → panel → tally → judge → structured result
   + disclaimer + cost). Reuses build_market_context + AIRouter.
3. `POST /debate {symbol, interval}` — Premium-gated (new F_DEBATE feature on premium tier),
   guards: cache + rate limit + spend cap (this is the most expensive call — several LLM hits).
4. Frontend: Debate tab — agent cards (lean/conviction/rationale), consensus verdict,
   dissent, confidence meter, cost. Premium upsell for lower tiers.
5. Tests: tally math (agreement/spread/split), panel orchestration (agents mocked),
   judge path (mocked), endpoint gating/cache; full suite green.
6. Commit + push (auto-deploy) + verify prod.

## Honesty / cost rules
- Agents ground every claim on a MarketContext data point (same rule as Copilot).
- Deterministic tally is the auditable backbone; the judge explains, never fabricates votes.
- Confidence must fall when the panel disagrees — divergence shown, not hidden.
- Most expensive endpoint → tightest guards + a dedicated cache. Premium tier only.
- Not financial advice; a panel of models is still assistive, not a guarantee.
