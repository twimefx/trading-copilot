"""FastAPI app exposing the AI Market Copilot.

Endpoints:
    GET  /health           -> liveness
    POST /copilot          -> {symbol, interval, include_kronos} -> analysis JSON
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    return {"status": "ok", "service": "trading-copilot", "version": "0.1.0"}


@app.post("/copilot")
def copilot(req: CopilotRequest):
    """Run the AI Market Copilot for a symbol and return the structured analysis."""
    from backend.signals.copilot import analyze_symbol
    try:
        return analyze_symbol(req.symbol, req.interval, include_kronos=req.include_kronos)
    except RuntimeError as e:
        # Config problems (e.g. ANTHROPIC_API_KEY not set) — surface clearly as 503.
        logger.exception("copilot config error")
        raise HTTPException(status_code=503, detail=f"Copilot unavailable: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("copilot failed")
        raise HTTPException(status_code=500, detail=f"Copilot error: {type(e).__name__}: {e}") from e


@app.post("/scan")
def scan(req: ScanRequest):
    """Fast rule-based screen of a watchlist (no LLM). Ranked by conviction."""
    from backend.signals.scanner import scan_watchlist
    return {"results": scan_watchlist(req.symbols, req.interval)}
