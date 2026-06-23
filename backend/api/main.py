"""FastAPI app exposing the AI Market Copilot.

Endpoints:
    GET  /health           -> liveness + today's LLM spend
    POST /copilot          -> {symbol, interval, include_kronos} -> analysis JSON
    POST /scan             -> {symbols, interval} -> rule-based watchlist screen
    POST   /journal        -> save an analysis / trade idea
    GET    /journal        -> list this owner's entries (optional ?status=)
    GET    /journal/stats  -> performance summary across closed trades
    GET    /journal/{id}   -> fetch one entry
    PATCH  /journal/{id}   -> update trade fields (status, entry/exit, notes, outcome)
    DELETE /journal/{id}   -> remove an entry

The journal is scoped by an X-Owner-Id header (a client-generated UUID) — no auth
yet, but the model upgrades cleanly to authenticated user ids later.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.api.guards import (
    client_key,
    copilot_cache,
    copilot_limiter,
    scan_cache,
    spend_guard,
)
from backend.journal import store as journal_store

logger = logging.getLogger("copilot.api")

app = FastAPI(title="AI Trading Copilot", version="0.1.0")

# Initialize the journal DB once at import (idempotent — safe on every worker boot).
journal_store.init_db()

# CORS — set FRONTEND_ORIGIN in prod (e.g. https://yourapp.vercel.app).
# Defaults to "*" for easy demo; tighten before real launch.
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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "trading-copilot",
        "version": "0.1.0",
        "spend_today_usd": spend_guard.spent_today,
        "spend_cap_usd": spend_guard.cap,
    }


@app.post("/copilot")
def copilot(req: CopilotRequest, request: Request):
    """Run the AI Market Copilot for a symbol and return the structured analysis.

    Guarded: cache (free repeats) -> per-IP rate limit -> daily spend cap.
    """
    sym = req.symbol.upper()
    cache_key = f"{sym}:{req.interval}:{int(req.include_kronos)}"

    # 1. Cache — repeat requests within TTL are free (no LLM call).
    cached = copilot_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    # 2. Per-IP rate limit (only counts when we'd actually spend).
    allowed, retry = copilot_limiter.allow(client_key(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": f"Rate limit reached. Try again in ~{retry // 60 + 1} min."},
        )

    # 3. Global daily spend ceiling — hard stop against runaway bills.
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
    copilot_cache.set(cache_key, result)
    return {**result, "cached": False}


@app.post("/scan")
def scan(req: ScanRequest):
    """Fast rule-based screen of a watchlist (no LLM). Ranked by conviction. Lightly cached."""
    key = f"{','.join(sorted(s.upper() for s in req.symbols))}:{req.interval}"
    cached = scan_cache.get(key)
    if cached is not None:
        return {"results": cached, "cached": True}
    from backend.signals.scanner import scan_watchlist
    results = scan_watchlist(req.symbols, req.interval)
    scan_cache.set(key, results)
    return {"results": results, "cached": False}


# --- Trade Journal -----------------------------------------------------------
# Scoped by X-Owner-Id (client UUID). No auth yet; upgrades to user ids later.

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


def _owner(x_owner_id: str | None) -> str:
    """Resolve and validate the owner id from the header."""
    oid = (x_owner_id or "").strip()
    if not oid or len(oid) > 128:
        raise HTTPException(status_code=400, detail="Missing or invalid X-Owner-Id header.")
    return oid


@app.post("/journal", status_code=201)
def journal_create(body: JournalCreate, x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    try:
        return journal_store.create_entry(owner, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/journal")
def journal_list(status: str | None = None, x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    return {"entries": journal_store.list_entries(owner, status=status)}


@app.get("/journal/stats")
def journal_stats(x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    return journal_store.stats(owner)


@app.get("/journal/{entry_id}")
def journal_get(entry_id: str, x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    entry = journal_store.get_entry(owner, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.patch("/journal/{entry_id}")
def journal_update(entry_id: str, body: JournalUpdate,
                   x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        entry = journal_store.update_entry(owner, entry_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return entry


@app.delete("/journal/{entry_id}", status_code=204)
def journal_delete(entry_id: str, x_owner_id: str | None = Header(default=None)):
    owner = _owner(x_owner_id)
    if not journal_store.delete_entry(owner, entry_id):
        raise HTTPException(status_code=404, detail="Entry not found.")
    return JSONResponse(status_code=204, content=None)
