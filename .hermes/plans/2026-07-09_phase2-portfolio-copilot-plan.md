# Phase 2 — Portfolio Copilot (Implementation Plan)

> **For Hermes:** Build TDD, deterministic risk core first, LLM layer grounded on it. Commit when green.
> **Status:** Journal + AI coaching shipped (85a6e3a, live). Scanner (rule-based) shipped. Portfolio Copilot is the remaining Phase-2 piece.

**Goal:** Turn a trader's OPEN positions (from their journal) into a portfolio-level
intelligence read: real exposure, concentration, directional bias, unrealized P&L,
and an AI risk assessment. Same honesty bar as coaching — deterministic numbers
first, LLM explains/prioritizes what the math already found, never invents risk.

## Design (mirrors coaching)
```
open journal entries (status=open, has direction/entry/size/symbol)
    │
    ├─► live price + fast indicator snapshot per symbol   [free, no LLM, reuse scanner.screen_symbol / indicators.snapshot]
    │
    ├─► DETERMINISTIC portfolio risk (backend/signals/portfolio.py):
    │      • per-position: notional, unrealized P&L (mark vs entry, direction-aware), % move
    │      • gross + net exposure ($ long vs $ short)
    │      • net directional bias (net long / net short / balanced)
    │      • concentration: largest position share, per-asset-class share, crypto share
    │      • flags: overexposed single name, one-directional book, crypto-only concentration,
    │              position underwater past a stop, oversized position vs rest
    │
    └─► LLM portfolio read (router SIGNAL_SUMMARY tier), grounded on the risk profile ONLY:
           { headline, risks[], suggestions[], disclaimer }
```

## Tasks
1. `backend/signals/portfolio.py`
   - `mark_positions(open_entries, price_lookup)` → per-position enriched dicts (pure, testable)
   - `assess(open_entries, ...)` → deterministic risk profile + flags
   - `portfolio_copilot(open_entries, router=None)` → risk profile + LLM read; honest
     "no open positions" / "not enough data" path with NO LLM call
   - Live price via a small helper reusing providers.get_provider (mockable in tests)
2. `GET /portfolio` endpoint
   - auth; reads journal open entries for the user
   - guards: per-user cache + rate limit + global spend cap (only when LLM actually runs)
   - Pro perk? Portfolio copilot is intelligence → gate behind F_JOURNAL (Pro), like coaching
3. Frontend: Portfolio panel (open positions table + AI risk read), on-demand LLM
4. Tests: mark math (long/short/underwater), concentration/bias flags, empty path,
   LLM path mocked, endpoint gating/cache
5. Run full suite green; commit; push (auto-deploys prod).

## Honesty / cost rules
- Never fabricate a position or a risk. LLM sees only the computed profile.
- Cheap tier, cached per (user, position-set), guarded by the same spend cap.
- Unrealized P&L needs entry_price + size + direction; positions missing those are
  marked "incomplete" and excluded from P&L math (but still counted for concentration).
- Not financial advice; risk read reflects the current book, not a prediction.
