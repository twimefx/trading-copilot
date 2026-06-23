"""Kronos forecasting microservice — runs SEPARATELY from the lean main backend.

The main API stays torch-free and cheap. This service carries torch + the Kronos
model and exposes ONE endpoint: POST /forecast {ohlcv, interval} -> a 24h price
RANGE (volatility band), never a direction call (Kronos has no directional edge).

Deploy anywhere (Railway 2nd service, RunPod/Lambda GPU box, etc.) and point the
main backend at it via KRONOS_SERVICE_URL. If this service is down/slow, the main
backend degrades gracefully to an ATR estimate — so it's optional, never a blocker.
"""
from __future__ import annotations

import logging

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("kronos.service")

app = FastAPI(title="Kronos Range Service", version="0.1.0")


class Candle(BaseModel):
    timestamps: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None


class ForecastRequest(BaseModel):
    ohlcv: list[Candle]
    pred_len: int = 24
    sample_count: int = 5


@app.get("/health")
def health():
    # Report whether the model is loaded so the caller can probe readiness.
    from backend.signals import kronos_range
    return {
        "status": "ok",
        "service": "kronos-range",
        "version": "0.1.0",
        "model_loaded": kronos_range._predictor is not None,
    }


@app.post("/forecast")
def forecast(req: ForecastRequest):
    """Forecast a 24h price RANGE from supplied OHLCV. Returns a normalized band."""
    if len(req.ohlcv) < 50:
        raise HTTPException(status_code=422, detail="Need >=50 candles for a forecast.")
    try:
        from backend.signals.kronos_range import forecast_range
        df = pd.DataFrame([c.model_dump() for c in req.ohlcv])
        df["timestamps"] = pd.to_datetime(df["timestamps"])
        needs_amount = "amount" not in df.columns or bool(df["amount"].isna().all())
        if needs_amount:
            df["amount"] = df["volume"] * df["close"]
        raw = forecast_range(df, pred_len=req.pred_len, sample_count=req.sample_count)
        # Normalize to the shape the main backend's _compute_range expects.
        return {
            "low": raw["expected_band_low"],
            "high": raw["expected_band_high"],
            "expected_close": raw["expected_close"],
            "band_width_pct": raw["band_width_pct"],
            "horizon_periods": raw["horizon_periods"],
            "source": "Kronos",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("kronos forecast failed")
        raise HTTPException(status_code=500, detail=f"Forecast error: {type(e).__name__}: {e}") from e
