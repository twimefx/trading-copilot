"""MarketContext — assembles all signals into one structured object for the LLM.

This is the single source of truth the reasoning layer reasons over. Combining:
  - live price + technical indicators (momentum/trend)   [direction inputs]
  - funding rate + open interest (positioning/sentiment) [direction inputs]
  - Kronos range forecast (volatility/level)             [risk/range input]
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict, dataclass, field

from backend.data.providers import get_provider, asset_class
from backend.data.indicators import snapshot, price_structure


@dataclass
class MarketContext:
    symbol: str
    interval: str
    asset_class: str = "crypto"
    indicators: dict = field(default_factory=dict)
    funding: dict = field(default_factory=dict)
    open_interest: dict = field(default_factory=dict)
    kronos_range: dict | None = None
    structure: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


def build_market_context(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    candles: int = 400,
    include_kronos: bool = True,
) -> MarketContext:
    """Fetch all live inputs and assemble a MarketContext.

    Provider (Binance/Oanda) is chosen automatically from the symbol.
    Kronos is optional (it's the slow CPU part) — callers can skip it for speed.
    """
    provider = get_provider(symbol)
    df = provider.fetch_klines(symbol, interval, candles)
    ctx = MarketContext(
        symbol=symbol,
        interval=interval,
        asset_class=asset_class(symbol),
        indicators=snapshot(df),
        funding=provider.fetch_funding_rate(symbol),
        open_interest=provider.fetch_open_interest(symbol),
        structure=price_structure(df),
    )
    if include_kronos:
        ctx.kronos_range = _fetch_kronos_range(df)
    return ctx


def _runpod_forecast(url: str, df) -> dict:
    """Call a RunPod serverless endpoint (runsync) and unwrap the job envelope.

    RunPod returns {"status": ..., "output": {...}}; the handler's forecast dict
    (or {"error": ...}) lives under "output". Activated by KRONOS_RUNPOD=1 with
    KRONOS_SERVICE_URL = the endpoint's /runsync URL and KRONOS_API_KEY set.
    """
    payload = {"input": {
        "ohlcv": json.loads(df.to_json(orient="records", date_format="iso")),
        "pred_len": 24,
        "sample_count": int(os.environ.get("KRONOS_SAMPLE_COUNT", "5")),
    }}
    req = urllib.request.Request(
        url.rstrip("/"), data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('KRONOS_API_KEY', '')}"},
    )
    timeout = float(os.environ.get("KRONOS_TIMEOUT", "120"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    output = body.get("output")
    if body.get("status") != "COMPLETED" or not isinstance(output, dict):
        return {"available": False, "note": f"RunPod job status: {body.get('status')}"}
    if "error" in output:
        return {"available": False, "note": f"Kronos error: {str(output['error'])[:80]}"}
    return output


def _fetch_kronos_range(df) -> dict:
    """Get a Kronos range, preferring the remote service, then local torch, then degrade.

    The lean production backend has NO torch, so the normal path is the HTTP service
    at KRONOS_SERVICE_URL (plain FastAPI, or a RunPod serverless endpoint when
    KRONOS_RUNPOD=1). If that's unset or unreachable, we degrade gracefully and
    the copilot's _compute_range falls back to an honest ATR estimate.
    """
    url = os.environ.get("KRONOS_SERVICE_URL")
    if url:
        try:
            if os.environ.get("KRONOS_RUNPOD") == "1":
                return _runpod_forecast(url, df)

            payload = {
                "ohlcv": json.loads(df.to_json(orient="records", date_format="iso")),
                "pred_len": 24,
                "sample_count": int(os.environ.get("KRONOS_SAMPLE_COUNT", "5")),
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url.rstrip("/") + "/forecast",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            timeout = float(os.environ.get("KRONOS_TIMEOUT", "120"))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001
            return {"available": False, "note": f"Kronos service error: {str(e)[:80]}"}

    # No remote service configured — try in-process (dev box with torch installed).
    try:
        from backend.signals.kronos_range import forecast_range
        raw = forecast_range(df, pred_len=24, sample_count=3)
        return {
            "low": raw["expected_band_low"],
            "high": raw["expected_band_high"],
            "expected_close": raw["expected_close"],
            "band_width_pct": raw["band_width_pct"],
            "source": "Kronos",
        }
    except Exception as e:  # noqa: BLE001
        # torch absent in lean build, or model load failed — degrade gracefully.
        return {"available": False, "note": f"Kronos unavailable: {str(e)[:80]}"}


if __name__ == "__main__":
    ctx = build_market_context("BTCUSDT", include_kronos=False)
    print(ctx.to_prompt_json())
