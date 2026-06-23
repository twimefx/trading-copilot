# Kronos Range Service

Standalone microservice that runs the Kronos forecasting model and returns a 24h
price **range** (volatility band) — never a direction call (Kronos has ~35%
directional accuracy but strong price-level accuracy, MAPE ~2%).

Kept separate from the main backend so that backend stays lean/torch-free and cheap.

## Endpoint
`POST /forecast`
```json
{ "ohlcv": [ {"timestamps": "...", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1}, ... ],
  "pred_len": 24, "sample_count": 5 }
```
returns
```json
{ "low": 62894.58, "high": 63585.78, "expected_close": 63075.43,
  "band_width_pct": 1.09, "horizon_periods": 24, "source": "Kronos" }
```
`GET /health` → `{status, model_loaded}`.

## Run locally (dev box with torch)
```bash
source .venv/bin/activate
uvicorn services.kronos.main:app --port 8012
# then point the main backend at it:
export KRONOS_SERVICE_URL=http://127.0.0.1:8012
```

## Deploy
See the repo `DEPLOY.md` → "Kronos Range Service". Build context is the repo ROOT
(the Dockerfile clones the gitignored `vendor/Kronos` and copies shared `backend/`
modules). CPU inference is slow (~30–90s); host on a GPU box for heavier use.
