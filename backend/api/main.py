"""FastAPI app exposing the AI Market Copilot.

Endpoints:
    GET  /health           -> liveness
    POST /copilot          -> {symbol, interval, include_kronos} -> analysis JSON
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="AI Trading Copilot", version="0.1.0")


class CopilotRequest(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    include_kronos: bool = True


@app.get("/health")
def health():
    return {"status": "ok", "service": "trading-copilot", "version": "0.1.0"}


@app.post("/copilot")
def copilot(req: CopilotRequest):
    """Run the AI Market Copilot for a symbol and return the structured analysis."""
    from backend.signals.copilot import analyze_symbol
    result = analyze_symbol(req.symbol, req.interval, include_kronos=req.include_kronos)
    return result
