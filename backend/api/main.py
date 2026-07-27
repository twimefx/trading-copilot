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
import time

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
from backend.billing import PRO, PREMIUM, F_JOURNAL, F_DEBATE, F_FLOW, F_STRATEGY, F_REPLAY, get_tier as tier_config
from backend.billing import users as user_store
from backend.journal import store as journal_store
from backend import alerts as alert_store
from backend.signals import history as signal_history

logger = logging.getLogger("copilot.api")

app = FastAPI(title="AI Trading Copilot", version="0.4.0")

# Initialize DBs once at import (idempotent — safe on every worker boot).
journal_store.init_db()
user_store.init_db()
alert_store.init_db()
signal_history.init_db()

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


def require_journal_user(user_id: str = Depends(current_user_id)) -> str:
    """Dependency: authenticated user who has the Journal (Pro) feature.

    The trade journal — including basic CRUD — is a Pro perk (F_JOURNAL), matching
    the tier config and the /journal/coaching gate. Free users get a 402 upgrade
    prompt instead of silent access. When auth is disabled (anon/open mode) there's
    no tiering, so the gate is a no-op and we don't touch the users table.
    """
    if not auth_mod.AUTH_ENABLED:
        return user_id
    tier = tier_config(user_store.get_tier(user_id))
    if F_JOURNAL not in tier.features:
        raise HTTPException(
            status_code=402,
            detail="The trade journal is a Pro feature. Upgrade to unlock it.",
        )
    return user_id


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


@app.get("/", include_in_schema=False)
def root():
    """Root route — the API is meant to be consumed via the frontend/proxy,
    so point browsers somewhere useful instead of a bare 404."""
    return {
        "service": "trading-copilot",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "trading-copilot",
        "version": "0.4.0",
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
    from backend.data.errors import UnknownSymbolError
    try:
        result = analyze_symbol(sym, req.interval, include_kronos=req.include_kronos)
    except UnknownSymbolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("copilot config error")
        raise HTTPException(status_code=503, detail=f"Copilot unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("copilot failed")
        raise HTTPException(status_code=500, detail=f"Copilot error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    # Count the paid call against the user's daily quota (only on a real LLM call).
    user_store.incr_copilot_call(user_id)
    # Persist spend (survives restarts) + log the directional call to the track record.
    cost = float(result.get("cost_usd") or 0.0)
    try:
        user_store.record_spend(user_id, "copilot", cost)
        from backend.data.providers import asset_class as _asset_class
        entry_price = result.get("entry_price")
        if not isinstance(entry_price, (int, float)):
            entry_price = None
        signal_history.log_signal(
            symbol=sym, interval=req.interval, asset_class=_asset_class(sym),
            lean=result.get("lean"), conviction=result.get("conviction"),
            entry_price=entry_price,
        )
    except Exception:  # noqa: BLE001 — logging must never break the paid call
        logger.exception("post-copilot logging failed")
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
def journal_create(body: JournalCreate, user_id: str = Depends(require_journal_user)):
    try:
        return journal_store.create_entry(user_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/journal")
def journal_list(status: str | None = None, user_id: str = Depends(require_journal_user)):
    return {"entries": journal_store.list_entries(user_id, status=status)}


@app.get("/journal/stats")
def journal_stats(user_id: str = Depends(require_journal_user)):
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
    from backend.data.errors import UnknownSymbolError
    try:
        result = run_debate(sym, req.interval, include_kronos=req.include_kronos)
    except UnknownSymbolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("debate config error")
        raise HTTPException(status_code=503, detail=f"Debate unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("debate failed")
        raise HTTPException(status_code=500, detail=f"Debate error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    debate_cache.set(cache_key, result)
    return {**result, "cached": False}


# --- Market Replay (Premium) ---------------------------------------------------
# Copilot/Debate run against a context truncated at a historical `as_of` — the
# model never sees future candles; the outcome is deterministic pandas math.
_REPLAY_CACHE_TTL = int(os.environ.get("REPLAY_CACHE_TTL", "3600"))  # 1h — history doesn't change
replay_cache = TTLCache(_REPLAY_CACHE_TTL)


class ReplayRequest(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    as_of: int                    # epoch seconds — the historical moment
    mode: str = "copilot"         # "copilot" | "debate"
    include_kronos: bool = True


@app.post("/replay")
def replay_endpoint(req: ReplayRequest, request: Request,
                    user_id: str = Depends(current_user_id)):
    """Market Replay — Copilot/Debate as of a past moment + honest outcome. Premium only."""
    tier = tier_config(user_store.get_tier(user_id))
    if auth_mod.AUTH_ENABLED and F_REPLAY not in tier.features:
        return JSONResponse(
            status_code=402,
            content={
                "detail": "Market Replay is a Premium feature. Upgrade to unlock it.",
                "tier": tier.name,
                "upgrade": True,
            },
        )

    if req.mode not in ("copilot", "debate"):
        raise HTTPException(status_code=422, detail="mode must be 'copilot' or 'debate'.")
    now = int(time.time())
    if req.as_of > now - 3600:
        raise HTTPException(status_code=422, detail="as_of must be at least 1 hour in the past.")
    if req.as_of < now - 90 * 86400:
        raise HTTPException(status_code=422, detail="as_of is limited to the last 90 days.")

    sym = req.symbol.upper()
    cache_key = f"replay:{req.mode}:{sym}:{req.interval}:{req.as_of}:{int(req.include_kronos)}"
    cached = replay_cache.get(cache_key)
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

    from backend.signals import replay as replay_mod
    from backend.data.errors import UnknownSymbolError
    try:
        ctx = replay_mod.build_replay_context(sym, req.interval, req.as_of,
                                              include_kronos=req.include_kronos)
        if req.mode == "debate":
            from backend.signals.debate import debate as run_debate
            result = run_debate(ctx=ctx)
            lean = result["consensus"]["lean"]
        else:
            from backend.signals.copilot import analyze
            result = analyze(ctx)
            lean = result.get("lean")
        outcome_df = replay_mod.fetch_outcome(sym, req.interval, req.as_of)
        entry = ctx.indicators.get("last_close")
        outcome = replay_mod.score_outcome(entry, lean, outcome_df)
    except UnknownSymbolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("replay failed")
        raise HTTPException(status_code=500, detail=f"Replay error: {type(e).__name__}: {e}") from e

    spend_guard.add(float(result.get("cost_usd") or 0.0))
    payload = {
        "symbol": sym,
        "interval": req.interval,
        "mode": req.mode,
        "as_of": req.as_of,
        "analysis": result,
        "outcome": outcome,
        "replay": True,
        "note": ("Replay answers as of the chosen moment — the model saw no future "
                 "candles. Positioning (funding/OI) is unavailable for historical "
                 "replays, so a replayed call relies on technicals only and may "
                 "differ from the live call made at that time. Outcome is computed "
                 "deterministically."),
    }
    replay_cache.set(cache_key, payload)
    return {**payload, "cached": False}


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
    from backend.data.errors import UnknownSymbolError
    try:
        result = build_strategy(prompt, sym, req.interval)
    except UnknownSymbolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
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
def journal_get(entry_id: str, user_id: str = Depends(require_journal_user)):
    entry = journal_store.get_entry(user_id, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.patch("/journal/{entry_id}")
def journal_update(entry_id: str, body: JournalUpdate,
                   user_id: str = Depends(require_journal_user)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        entry = journal_store.update_entry(user_id, entry_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.delete("/journal/{entry_id}", status_code=204)
def journal_delete(entry_id: str, user_id: str = Depends(require_journal_user)):
    if not journal_store.delete_entry(user_id, entry_id):
        raise HTTPException(status_code=404, detail="Entry not found.")
    return JSONResponse(status_code=204, content=None)


# --- Watchlists ---------------------------------------------------------------

class WatchlistPut(BaseModel):
    symbols: list[str]


@app.get("/watchlist")
def watchlist_get(user_id: str = Depends(current_user_id)):
    return {"symbols": user_store.get_watchlist(user_id)}


@app.put("/watchlist")
def watchlist_put(body: WatchlistPut, user_id: str = Depends(current_user_id)):
    try:
        symbols = user_store.set_watchlist(user_id, body.symbols)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"symbols": symbols}


# --- Alerts -------------------------------------------------------------------

class AlertRuleCreate(BaseModel):
    kind: str                      # price_above | price_below | scanner_lean
    config: dict
    cooldown_s: int = 3600


class AlertRuleUpdate(BaseModel):
    active: bool | None = None
    config: dict | None = None
    cooldown_s: int | None = None


class AlertCheckRequest(BaseModel):
    scheduler_key: str | None = None


@app.get("/alerts")
def alerts_list(user_id: str = Depends(current_user_id)):
    return {"rules": alert_store.list_rules(user_id)}


@app.post("/alerts", status_code=201)
def alerts_create(body: AlertRuleCreate, user_id: str = Depends(current_user_id)):
    try:
        return alert_store.create_rule(
            user_id, body.kind.strip().lower(), body.config, body.cooldown_s
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.patch("/alerts/{rule_id}")
def alerts_update(rule_id: str, body: AlertRuleUpdate,
                  user_id: str = Depends(current_user_id)):
    try:
        rule = alert_store.update_rule(
            user_id, rule_id, active=body.active,
            config=body.config, cooldown_s=body.cooldown_s,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return rule


@app.delete("/alerts/{rule_id}", status_code=204)
def alerts_delete(rule_id: str, user_id: str = Depends(current_user_id)):
    if not alert_store.delete_rule(user_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found.")
    return JSONResponse(status_code=204, content=None)


@app.get("/alerts/events")
def alerts_events(user_id: str = Depends(current_user_id)):
    return {"events": alert_store.list_events(user_id)}


@app.post("/alerts/{rule_id}/test")
def alerts_test(rule_id: str, user_id: str = Depends(current_user_id)):
    """Send a test notification for one rule (ignores cooldown, honest condition)."""
    if alert_store.get_rule(user_id, rule_id) is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return alert_store.evaluate_rules(trigger_test_rule_id=rule_id)


@app.post("/alerts/check")
def alerts_check(body: AlertCheckRequest):
    """Scheduler entry point — evaluates every active rule once.

    Guarded by ALERT_SCHEDULER_KEY when set (Hermes cron / any external
    scheduler passes it in the body). When unset (dev), the endpoint is open —
    same trust model as the rest of the app in open mode.
    """
    expected = os.environ.get("ALERT_SCHEDULER_KEY", "").strip()
    if expected and body.scheduler_key != expected:
        raise HTTPException(status_code=403, detail="Invalid scheduler key.")
    return alert_store.evaluate_rules()


# --- Signal track record --------------------------------------------------------

@app.get("/signals/history")
def signals_history(symbol: str | None = None, limit: int = 100):
    """Public track record — every logged Copilot call and its scored outcome."""
    signal_history.resolve_pending()
    return {"signals": signal_history.list_signals(symbol, limit)}


@app.get("/signals/stats")
def signals_stats():
    signal_history.resolve_pending()
    return signal_history.stats()


# --- Cost digest (scheduler) -----------------------------------------------------

class CostDigestRequest(BaseModel):
    scheduler_key: str | None = None


@app.post("/admin/cost-digest")
def cost_digest(body: CostDigestRequest):
    """Weekly LLM-spend digest. Delivers via the same alert channels; safe to
    call any time (the scheduler calls it weekly)."""
    expected = os.environ.get("ALERT_SCHEDULER_KEY", "").strip()
    if expected and body.scheduler_key != expected:
        raise HTTPException(status_code=403, detail="Invalid scheduler key.")
    days = user_store.spend_by_day(7)
    total = round(sum(d["usd"] for d in days), 4)
    lines = [f"LLM spend digest — last 7 days (total ${total:.2f} of "
             f"${spend_guard.cap:.0f}/day cap):"]
    for d in days:
        lines.append(f"  {d['day']}: ${d['usd']:.2f} across {d['calls']} calls")
    if not days:
        lines.append("  (no recorded spend yet)")
    message = "\n".join(lines)
    delivered = []
    import os as _os
    chat_id = _os.environ.get("ALERT_TELEGRAM_DEFAULT_CHAT_ID", "").strip()
    if chat_id and alert_store._notify_telegram(chat_id, message):
        delivered.append("telegram")
    email = _os.environ.get("ALERT_DIGEST_EMAIL", "").strip()
    if email and alert_store._notify_email(email, "Trading Copilot weekly cost digest", message):
        delivered.append("email")
    return {"days": days, "total_usd": total, "delivered": delivered}
