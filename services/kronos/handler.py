"""RunPod serverless handler for the Kronos range service.

RunPod serverless workers call `handler(event)` per request. We reuse the SAME
forecast logic as the FastAPI service (services/kronos/main.py) by calling
backend.signals.kronos_range.forecast_range directly, so the GPU worker and the
CPU service return identical shapes. The only difference: this runs on CUDA.

Request shape (mirrors the HTTP service's /forecast):
    event["input"] = {
        "ohlcv": [ {timestamps, open, high, low, close, volume, amount}, ... ],
        "pred_len": 24,          # optional
        "sample_count": 5,       # optional
    }

Response:
    {"low", "high", "expected_close", "band_width_pct", "source": "Kronos", "device"}
  or {"error": "..."} on failure (RunPod marks the job failed; the backend's
  graceful-degradation path then falls back to ATR).
"""
from __future__ import annotations

import os
import sys

# Make the vendored Kronos model importable before kronos_range imports it.
# Prefer the image's /app/vendor path; fall back to repo-relative for local dev.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ("/app/vendor/Kronos",
           os.path.join(_HERE, "..", "..", "vendor", "Kronos")):
    if os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import pandas as pd  # noqa: E402
import runpod  # noqa: E402

from backend.signals.kronos_range import forecast_range  # noqa: E402

# Warm the model singleton in a background thread so the worker reports READY
# immediately (RunPod marks slow-init workers unhealthy). First request may block
# briefly on the lock while the model finishes loading on CUDA.
import threading  # noqa: E402

_WARM = os.environ.get("KRONOS_WARM", "1") == "1"


def _warm():
    try:
        from backend.signals.kronos_range import _get_predictor
        _get_predictor()
        print("[kronos] model warm", flush=True)
    except Exception as e:  # noqa: BLE001
        # Don't crash the worker on warm failure; the request path reports errors.
        print(f"[kronos] warm load failed: {e}", file=sys.stderr, flush=True)


def handler(event):
    try:
        inp = event.get("input") or {}
        ohlcv = inp.get("ohlcv")
        if not ohlcv:
            return {"error": "missing 'ohlcv' in input"}
        df = pd.DataFrame(ohlcv)
        pred_len = int(inp.get("pred_len", 24))
        sample_count = int(inp.get("sample_count", 5))

        raw = forecast_range(df, pred_len=pred_len, sample_count=sample_count)
        return {
            "low": raw["expected_band_low"],
            "high": raw["expected_band_high"],
            "expected_close": raw["expected_close"],
            "band_width_pct": raw["band_width_pct"],
            "source": "Kronos",
            "device": os.environ.get("KRONOS_DEVICE", "cpu"),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


if _WARM:
    threading.Thread(target=_warm, daemon=True).start()

runpod.serverless.start({"handler": handler})
