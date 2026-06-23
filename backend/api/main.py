"""FastAPI app exposing the AI Market Copilot.

Endpoints:
    GET  /health           -> liveness + today's LLM spend
    POST /copilot          -> {symbol, interval, include_kronos} -> analysis JSON
    POST /scan             -> {symbols, interval} -> rule-based watchlist screen
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request
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

logger = logging.getLogger("copilot.api")

app = FastAPI(title="AI Trading Copilot", version="0.1.0")

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
