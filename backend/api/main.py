"""FastAPI app exposing the AI Market Copilot.

Endpoints:
    GET  /health           -> liveness + today's LLM spend
    GET  /me               -> current user's tier, quota, and usage (auth)
    POST /copilot          -> {symbol, interval, include_kronos} -> analysis JSON (auth + quota)
    POST /scan             -> {symbols, interval} -> rule-based watchlist screen (auth, tier-capped)
    POST /debate           -> {symbol, interval} -> multi-agent debate + consensus (auth, Premium)
    GET  /flow             -> ?symbol&period -> institutional flow dashboard (auth, Premium)
    POST /strategy         -> {prompt, symbol, interval} -> NL strategy + backtest (auth, Premium)
    POST /billing/checkout -> {tier} -> Stripe Checkout URL (auth)
    POST /billing/portal   -> Stripe billing-portal URL to manage/cancel (auth)
    POST /billing/webhook  -> Stripe subscription events (signature-verified, no auth)
    /journal*              -> Trade Journal CRUD, scoped to the authenticated user
    GET  /journal/coaching -> AI behavioral coaching over own closed trades (auth, Pro)
    GET  /portfolio        -> AI risk read over own open positions (auth, Pro)

Identity: a Clerk session JWT (Authorization: Bearer ***) is verified and its
`sub` becomes the owner/user id everything is scoped by. In dev/tests, when
AUTH_DEV_ALLOW_HEADER=1, a legacy X-Owner-Id header is accepted instead.
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.api import auth as auth_mod
from backend.api.auth import current_user_id
from backend.api.guards import (
    TTLCache,
    client_key,
    copilot_cache,
    copilot_limiter,
    scan_cache,
    spend_guard,
)
from backend.billing import PRO, PREMIUM, F_JOURNAL, F_DEBATE, F_FLOW, F_STRATEGY, get_tier as tier_config
from backend.billing import users as user_store
from backend.journal import store as journal_store

logger = logging.getLogger("copilot.api")

app = FastAPI(title="AI Trading Copilot", version="0.2.0")

# Initialize DBs once at import (idempotent — safe on every worker boot).
journal_store.init_db()
user_store.init_db()

# CORS — set FRONTEND_ORIGIN in prod (comma-separated allowed origins).
# allow_credentials stays False; auth travels in the Authorization header, not cookies.
_origins = os.environ.get("FRONTEND_ORIGIN", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CopilotRequest(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    include_kronos: bool = True


class ScanRequest(BaseModel):
    symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    interval: str = "1h"


class CheckoutRequest(BaseModel):
    tier: str  # "pro" | "premium"


class DebateRequest(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    include_kronos: bool = True


class StrategyRequest(BaseModel):
    prompt: str
    symbol: str = "BTCUSDT"
    interval: str = "1h"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "trading-copilot",
        "version": "0.2.0",
        "spend_today_usd": spend_guard.spent_today,
        "spend_cap_usd": spend_guard.cap,
    }


@app.get("/me")
def me(user_id: str = Depends(current_user_id)):
    """Current user's entitlement + today's usage — powers the UI's tier badge/quota."""
    tier_name = user_store.get_tier(user_id)
    tier = tier_config(tier_name)
    used = user_store.copilot_calls_today(user_id)
    quota = tier.daily_copilot_quota
    return {
        "user_id": user_id,
        "tier": tier_name,
        "daily_copilot_quota": quota,
        "copilot_calls_today": used,
        "copilot_calls_remaining": (None if quota < 0 else max(quota - used, 0)),
        "scan_max_symbols": tier.scan_max_symbols,
        "features": sorted(tier.features),
    }


@app.post("/copilot")
def copilot(req: CopilotRequest, request: Request,
            user_id: str = Depends(current_user_id)):
    """Run the AI Market Copilot for a symbol and return the structured analysis.

    Guarded: cache (free repeats) -> per-user daily tier quota -> per-IP rate
    limit -> global daily spend cap.
    """
    sym = req.symbol.upper()
    cache_key = f"{sym}:{req.interval}:{int(req.include_kronos)}"

    # 1. Cache — repeat requests within TTL are free (no LLM call, no quota burn).
    cached = copilot_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    # 2. Per-user daily tier quota — the monetization gate. Skipped in open mode
    #    (Clerk unconfigured): there's no per-user identity to meter, so the global
    #    daily spend cap (step 4) is the sole cost guard, matching the pre-auth app.
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and tier.daily_copilot_quota >= 0:
        used = user_store.copilot_calls_today(user_id)
        if used >= tier.daily_copilot_quota:
            return JSONResponse(
                status_code=402,  # Payment Required — upgrade to continue
                content={
                    "detail": (
                        f"Daily limit reached for the {tier.name} plan "
                        f"({tier.daily_copilot_quota}/day). Upgrade for more."
                    ),
                    "tier": tier.name,
                    "quota": tier.daily_copilot_quota,
                    "upgrade": True,
                },
            )

    # 3. Per-IP rate limit (cheap anti-hammer, independent of tier).
    allowed, retry = copilot_limiter.allow(client_key(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
        )

    # 4. Global daily spend ceiling — hard stop against runaway bills.
    if not spend_guard.check():
        return JSONResponse(
            status_code=429,
            content={"detail": "Daily analysis budget reached. Resets at 00:00 UTC."},
        )

    from backend.signals.copilot import analyze_symbol
    try:
        result = analyze_symbol(sym, req.interval, include_kronos=req.include_kronos)
    except RuntimeError as e:
        logger.exception("copilot config error")
        raise HTTPException(status_code=503, detail=f"Copilot unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("copilot failed")
        raise HTTPException(status_code=500, detail=f"Copilot error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    # Count the paid call against the user's daily quota (only on a real LLM call).
    user_store.incr_copilot_call(user_id)
    copilot_cache.set(cache_key, result)
    return {**result, "cached": False}


@app.post("/scan")
def scan(req: ScanRequest, user_id: str = Depends(current_user_id)):
    """Fast rule-based screen of a watchlist (no LLM). Symbol count capped by tier.

    In open mode (Clerk unconfigured) the tier cap is not applied — anonymous
    users get the full watchlist, matching the pre-auth behaviour.
    """
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED:
        symbols = [s.upper() for s in req.symbols][: tier.scan_max_symbols]
    else:
        symbols = [s.upper() for s in req.symbols]
    key = f"{','.join(sorted(symbols))}:{req.interval}"
    cached = scan_cache.get(key)
    if cached is not None:
        return {"results": cached, "cached": True, "scan_max_symbols": tier.scan_max_symbols}
    from backend.signals.scanner import scan_watchlist
    results = scan_watchlist(symbols, req.interval)
    scan_cache.set(key, results)
    return {"results": results, "cached": False, "scan_max_symbols": tier.scan_max_symbols}


# --- Billing -----------------------------------------------------------------

@app.post("/billing/checkout")
def billing_checkout(body: CheckoutRequest, user_id: str = Depends(current_user_id)):
    """Create a Stripe Checkout Session for the requested tier; return its URL."""
    tier = (body.tier or "").lower()
    if tier not in (PRO, PREMIUM):
        raise HTTPException(status_code=400, detail="tier must be 'pro' or 'premium'.")
    from backend.billing import stripe_billing
    try:
        url = stripe_billing.create_checkout_session(user_id, tier)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"Billing unavailable: {e}") from e
    return {"url": url}


@app.post("/billing/portal")
def billing_portal(user_id: str = Depends(current_user_id)):
    """Return a Stripe billing-portal URL so the user can manage/cancel."""
    from backend.billing import stripe_billing
    try:
        url = stripe_billing.create_billing_portal_session(user_id)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"Billing unavailable: {e}") from e
    return {"url": url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request,
                          stripe_signature: str | None = Header(default=None)):
    """Stripe subscription events. Signature-verified; NOT behind user auth."""
    payload = await request.body()
    from backend.billing import stripe_billing
    try:
        event = stripe_billing.verify_and_parse_event(payload, stripe_signature or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        summary = stripe_billing.handle_event(event)
    except Exception:  # noqa: BLE001 — never 500 to Stripe or it retries forever
        logger.exception("stripe webhook handling failed")
        return {"received": True, "handled": False}
    return {"received": True, **summary}


# --- Trade Journal -----------------------------------------------------------
# Scoped to the authenticated user id.

class JournalCreate(BaseModel):
    symbol: str | None = None
    interval: str | None = None
    analysis: dict | None = None          # full Copilot result snapshot
    status: str = "idea"                  # idea | open | closed | cancelled
    direction: str = "none"               # long | short | none
    entry_price: float | None = None
    exit_price: float | None = None
    size: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    outcome: str | None = None            # win | loss | breakeven
    pnl: float | None = None
    notes: str | None = None


class JournalUpdate(BaseModel):
    status: str | None = None
    direction: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    size: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    outcome: str | None = None
    pnl: float | None = None
    notes: str | None = None


@app.post("/journal", status_code=201)
def journal_create(body: JournalCreate, user_id: str = Depends(current_user_id)):
    try:
        return journal_store.create_entry(user_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/journal")
def journal_list(status: str | None = None, user_id: str = Depends(current_user_id)):
    return {"entries": journal_store.list_entries(user_id, status=status)}


@app.get("/journal/stats")
def journal_stats(user_id: str = Depends(current_user_id)):
    return journal_store.stats(user_id)


# Behavioral coaching runs an LLM call, so cache per-user to avoid re-billing on
# repeat views of the same (slow-changing) closed-trade set. Short TTL keeps it
# fresh after a user closes new trades.
_COACHING_CACHE_TTL = int(os.environ.get("COACHING_CACHE_TTL", "600"))  # 10 min
coaching_cache = TTLCache(_COACHING_CACHE_TTL)


@app.get("/journal/coaching")
def journal_coaching(request: Request, user_id: str = Depends(current_user_id)):
    """AI behavioral coaching over the user's own closed-trade history.

    Journal + coaching is a paid perk (F_JOURNAL). Guarded like the copilot:
    per-user cache -> per-IP rate limit -> global daily spend cap. Returns
    honest 'not enough data' (no LLM call) below the trade threshold.
    """
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_JOURNAL not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "AI trade-journal coaching is a Pro feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    closed = journal_store.list_entries(user_id, status="closed")

    cache_key = f"coach:{user_id}:{len(closed)}"
    cached = coaching_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    from backend.journal.coaching import coach, MIN_TRADES_FOR_COACHING

    # Cheap pre-check: only enforce rate/spend guards when we'll actually call the LLM.
    decided = sum(1 for e in closed if e.get("outcome") in ("win", "loss"))
    if decided >= MIN_TRADES_FOR_COACHING:
        allowed, retry = copilot_limiter.allow(client_key(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
            )
        if not spend_guard.check():
            return JSONResponse(
                status_code=429,
                content={"detail": "Daily analysis budget reached. Resets at 00:00 UTC."},
            )

    try:
        result = coach(closed)
    except RuntimeError as e:
        logger.exception("coaching config error")
        raise HTTPException(status_code=503, detail=f"Coaching unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("coaching failed")
        raise HTTPException(status_code=500, detail=f"Coaching error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    coaching_cache.set(cache_key, result)
    return {**result, "cached": False}


# --- Portfolio Copilot -------------------------------------------------------
# Portfolio-level risk read over the user's OPEN journal positions. Same guards
# and Pro gate as coaching; the LLM read is grounded on a deterministic profile.
_PORTFOLIO_CACHE_TTL = int(os.environ.get("PORTFOLIO_CACHE_TTL", "180"))  # 3 min
portfolio_cache = TTLCache(_PORTFOLIO_CACHE_TTL)


@app.get("/portfolio")
def portfolio(request: Request, user_id: str = Depends(current_user_id)):
    """AI risk read over the user's open positions (from the journal).

    Pro perk (F_JOURNAL). Guards: per-user cache -> per-IP rate limit -> global
    daily spend cap (only when positions exist and the LLM actually runs).
    Returns an honest 'no open positions' with no LLM call when the book is empty.
    """
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_JOURNAL not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "Portfolio Copilot is a Pro feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    open_entries = journal_store.list_entries(user_id, status="open")

    # Cache key varies with the open set size + their ids so re-marks refresh
    # when the book changes, but repeat views within the TTL are free.
    ids_sig = ",".join(sorted(e.get("id", "") for e in open_entries))
    cache_key = f"port:{user_id}:{len(open_entries)}:{hash(ids_sig)}"
    cached = portfolio_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    from backend.signals.portfolio import portfolio_copilot

    if open_entries:
        allowed, retry = copilot_limiter.allow(client_key(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
            )
        if not spend_guard.check():
            return JSONResponse(
                status_code=429,
                content={"detail": "Daily analysis budget reached. Resets at 00:00 UTC."},
            )

    try:
        result = portfolio_copilot(open_entries)
    except RuntimeError as e:
        logger.exception("portfolio config error")
        raise HTTPException(status_code=503, detail=f"Portfolio Copilot unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("portfolio failed")
        raise HTTPException(status_code=500, detail=f"Portfolio error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    portfolio_cache.set(cache_key, result)
    return {**result, "cached": False}


# --- Multi-Agent Debate Engine (Premium flagship) ----------------------------
# The most expensive endpoint (a panel of LLM calls + a judge). Premium-gated,
# with its own cache and the same rate + spend guards as the copilot.
_DEBATE_CACHE_TTL = int(os.environ.get("DEBATE_CACHE_TTL", "600"))  # 10 min
debate_cache = TTLCache(_DEBATE_CACHE_TTL)


@app.post("/debate")
def debate(req: DebateRequest, request: Request,
           user_id: str = Depends(current_user_id)):
    """Run the multi-agent debate panel + judge for a symbol. Premium only.

    Guards: Premium gate -> cache -> per-IP rate limit -> global daily spend cap.
    This fires several LLM calls, so the spend cap is the critical backstop.
    """
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_DEBATE not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "The Multi-Agent Debate Engine is a Premium feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    sym = req.symbol.upper()
    cache_key = f"debate:{sym}:{req.interval}:{int(req.include_kronos)}"
    cached = debate_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    allowed, retry = copilot_limiter.allow(client_key(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
        )
    if not spend_guard.check():
        return JSONResponse(
            status_code=429,
            content={"detail": "Daily analysis budget reached. Resets at 00:00 UTC."},
        )

    from backend.signals.debate import debate as run_debate
    try:
        result = run_debate(sym, req.interval, include_kronos=req.include_kronos)
    except RuntimeError as e:
        logger.exception("debate config error")
        raise HTTPException(status_code=503, detail=f"Debate unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("debate failed")
        raise HTTPException(status_code=500, detail=f"Debate error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    debate_cache.set(cache_key, result)
    return {**result, "cached": False}


# --- Institutional Flow Dashboard (Premium) ----------------------------------
# Derivatives positioning/flow intelligence. Data fetch is free; one cheap LLM
# call narrates it. Premium-gated with its own cache + the shared guards.
_FLOW_CACHE_TTL = int(os.environ.get("FLOW_CACHE_TTL", "300"))  # 5 min
flow_cache = TTLCache(_FLOW_CACHE_TTL)


@app.get("/flow")
def flow(request: Request, symbol: str = "BTCUSDT", period: str = "1h",
         user_id: str = Depends(current_user_id)):
    """Institutional flow dashboard (funding/OI/L-S ratio/taker flow). Premium only."""
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_FLOW not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "The Institutional Flow Dashboard is a Premium feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    sym = symbol.upper()
    cache_key = f"flow:{sym}:{period}"
    cached = flow_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    allowed, retry = copilot_limiter.allow(client_key(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
        )
    # Narrate only if under the spend cap; otherwise still return the (free) data.
    want_narrative = spend_guard.check()

    from backend.signals.flow import institutional_flow
    try:
        result = institutional_flow(sym, period=period, narrative=want_narrative)
    except Exception as e:  # noqa: BLE001
        logger.exception("flow failed")
        raise HTTPException(status_code=500, detail=f"Flow error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    flow_cache.set(cache_key, result)
    return {**result, "cached": False}


# --- AI Strategy Builder (Premium) -------------------------------------------
# NL -> validated rule-spec (LLM) -> deterministic backtest (pure code). The
# backtest is never LLM-produced. Premium-gated with cache + guards.
_STRATEGY_CACHE_TTL = int(os.environ.get("STRATEGY_CACHE_TTL", "600"))  # 10 min
strategy_cache = TTLCache(_STRATEGY_CACHE_TTL)


@app.post("/strategy")
def strategy(req: StrategyRequest, request: Request,
             user_id: str = Depends(current_user_id)):
    """Turn a plain-English strategy idea into a validated rule-spec + real backtest."""
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_STRATEGY not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "The AI Strategy Builder is a Premium feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    if len(prompt) > 1000:
        raise HTTPException(status_code=422, detail="prompt too long (max 1000 chars)")

    sym = req.symbol.upper()
    cache_key = f"strategy:{sym}:{req.interval}:{prompt.lower()}"
    cached = strategy_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    allowed, retry = copilot_limiter.allow(client_key(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
        )
    if not spend_guard.check():
        return JSONResponse(
            status_code=429,
            content={"detail": "Daily analysis budget reached. Resets at 00:00 UTC."},
        )

    from backend.signals.strategy import build_strategy, SpecError
    try:
        result = build_strategy(prompt, sym, req.interval)
    except SpecError as e:
        # The model produced an invalid/unsupported spec — user-facing, not a 500.
        raise HTTPException(
            status_code=422,
            detail=f"Could not build a valid strategy from that description: {e}",
        ) from e
    except RuntimeError as e:
        logger.exception("strategy config error")
        raise HTTPException(status_code=503, detail=f"Strategy builder unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("strategy failed")
        raise HTTPException(status_code=500, detail=f"Strategy error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    strategy_cache.set(cache_key, result)
    return {**result, "cached": False}


@app.get("/journal/{entry_id}")
def journal_get(entry_id: str, user_id: str = Depends(current_user_id)):
    entry = journal_store.get_entry(user_id, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.patch("/journal/{entry_id}")
def journal_update(entry_id: str, body: JournalUpdate,
                   user_id: str = Depends(current_user_id)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        entry = journal_store.update_entry(user_id, entry_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.delete("/journal/{entry_id}", status_code=204)
def journal_delete(entry_id: str, user_id: str = Depends(current_user_id)):
    if not journal_store.delete_entry(user_id, entry_id):
        raise HTTPException(status_code=404, detail="Entry not found.")
    return JSONResponse(status_code=204, content=None)
