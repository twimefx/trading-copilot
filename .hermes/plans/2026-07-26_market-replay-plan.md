# Market Replay Mode — Implementation Plan (Phase 4)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Let a Premium user pick a symbol + historical date/time and run the AI Copilot (and Debate) as it would have answered *at that moment*, then show what actually happened next — honest, timestamped, great for trust + demos.

**Architecture:** Reuse everything we already have. The Copilot and Debate engines already accept a `MarketContext`; the only reason they always use "now" is that `build_market_context()` fetches live klines and computes indicators over the tail of the series. We add an `as_of` cutoff: fetch historical klines ending at `as_of`, compute the same indicator snapshot on that truncated frame, and pass the resulting context into the unchanged `analyze()` / `debate()` functions. A second deterministic pass fetches the candles *after* `as_of` to score "what happened" against the call — no LLM, pure math, mirroring the track-record scorer.

**Tech Stack:** FastAPI (backend), pandas (data), Next.js + Tailwind (frontend tab), existing AIRouter / MarketContext / indicators / history modules.

**Why this design (honesty-first):** the LLM never sees future candles — the context is truncated at `as_of`, so it genuinely "answers as of then." The outcome panel is computed by code, not narrated by the model. This matches the project's core positioning ("intelligence, explainability, not guaranteed signals") and makes Replay a trust builder, not a hindsight toy.

---

## Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Tier | **Premium** (`F_REPLAY` in Premium features) | Expensive (Copilot or 7-agent Debate per replay); Premium flagship to drive upgrades |
| Modes | `copilot` (1 call) and `debate` (8 calls), both via one endpoint | Copilot is the cheap default; Debate is the showpiece |
| Historical data | New `fetch_klines_range(start, end)` on Binance + Oanda | Current `fetch_klines` only returns the latest N candles; replay needs a window |
| Positioning data (funding/OI) | Mark `available: False` for replays | Funding/OI history endpoints are geo-blocked on Railway and coarse; honest degradation beats fabricated history. The context already handles `available: False` everywhere |
| Kronos | Optional (same `include_kronos` flag) | The Kronos service forecasts from supplied OHLCV, so it works on a truncated frame unchanged |
| Outcome window | `+24` periods of the chosen interval (matches `DEFAULT_HORIZON_PERIODS`) | Consistent with the track record's scoring horizon |
| Cost guards | Same as /debate: tier gate → rate limit → daily spend cap | Replays can burn Opus; the cap is the backstop |
| Cache | TTL cache keyed `replay:{mode}:{sym}:{interval}:{as_of}:{kronos}` | Same request repeated = free; historical data doesn't change, so a long TTL (1h) is safe |

---

## Task 1: Add `fetch_klines_range` to the Binance provider

**Objective:** Fetch OHLCV candles for an arbitrary historical window from Binance.

**Files:**
- Modify: `backend/data/binance.py` (add function after `fetch_klines`, ~line 80)
- Test: `backend/tests/test_replay.py` (new file, created in Task 4 — write the provider test here first)

**Step 1: Write failing test** (put in `backend/tests/test_replay.py`)

```python
def test_binance_fetch_klines_range_shape(monkeypatch):
    import pandas as pd
    from backend.data import binance

    # Fabricate 3 klines payloads the "API" returns in one page.
    fake = [
        [1704067200000, "100", "110", "90", "105", "1000", 0, "105000"],  # 2024-01-01
        [1704070800000, "105", "115", "95", "110", "1000", 0, "110000"],
        [1704074400000, "110", "120", "100", "115", "1000", 0, "115000"],
    ]

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(fake).encode()

    monkeypatch.setattr(binance.urllib.request, "urlopen", lambda *a, **k: _Resp())
    df = binance.fetch_klines_range(
        "BTCUSDT", "1h",
        start_ms=1704067200000, end_ms=1704074400000,
    )
    assert list(df["close"]) == [105.0, 110.0, 115.0]
    assert list(df.columns) == ["timestamps", "open", "high", "low", "close", "volume", "amount"]
```

Add `import json` at the top of the test file.

**Step 2: Run test to verify failure**
Run: `cd /opt/data/projects/trading-copilot && source .venv/bin/activate && pytest backend/tests/test_replay.py -v`
Expected: FAIL — `AttributeError: module 'backend.data.binance' has no attribute 'fetch_klines_range'`

**Step 3: Implement** (append to `backend/data/binance.py`)

```python
def fetch_klines_range(symbol: str, interval: str,
                       start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch historical OHLCV candles for [start_ms, end_ms] (epoch milliseconds).

    Pages through Binance's startTime/endTime klines (max 1000/request) until the
    window is covered. Same columns as fetch_klines. Raises UnknownSymbolError on
    a bad symbol (400), matching fetch_klines.
    """
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{BINANCE_BASE}?symbol={symbol}&interval={interval}"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        req = urllib.request.Request(url, headers={"User-Agent": "trading-copilot/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                raise UnknownSymbolError(symbol, "Binance") from e
            raise
        if not raw:
            break
        for k in raw:
            rows.append({
                "timestamps": pd.to_datetime(k[0], unit="ms"),
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]), "amount": float(k[7]),
            })
        last_open = raw[-1][0]
        if len(raw) < 1000:
            break
        cursor = last_open + 1
    df = pd.DataFrame(rows, columns=["timestamps", *_OHLCV_COLS])
    return df.drop_duplicates(subset="timestamps").sort_values("timestamps").reset_index(drop=True)
```

**Step 4: Run test to verify pass**
Run: `pytest backend/tests/test_replay.py::test_binance_fetch_klines_range_shape -v`
Expected: PASS

**Step 5: Commit**
```bash
git add backend/data/binance.py backend/tests/test_replay.py
git commit -m "feat(replay): binance fetch_klines_range for historical windows"
```

---

## Task 2: Add `fetch_klines_range` to the Oanda provider

**Objective:** Same historical-window capability for forex symbols.

**Files:**
- Modify: `backend/data/oanda.py` (add after `fetch_klines`, ~line 80)
- Test: `backend/tests/test_replay.py`

**Step 1: Write failing test**

```python
def test_oanda_fetch_klines_range_shape(monkeypatch):
    from backend.data import oanda

    fake = {"candles": [
        {"time": "2024-01-01T00:00:00.000000000Z", "complete": True,
         "mid": {"o": "1.10", "h": "1.11", "l": "1.09", "c": "1.105"}, "volume": 100},
        {"time": "2024-01-01T01:00:00.000000000Z", "complete": True,
         "mid": {"o": "1.105", "h": "1.115", "l": "1.095", "c": "1.110"}, "volume": 120},
    ]}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(fake).encode()

    monkeypatch.setattr(oanda.urllib.request, "urlopen", lambda *a, **k: _Resp())
    df = oanda.fetch_klines_range("EUR_USD", "1h", start_ms=0, end_ms=9999999999999)
    assert list(df["close"]) == [1.105, 1.110]
```

**Step 2: Run to verify failure** — `pytest backend/tests/test_replay.py -v` → `AttributeError`.

**Step 3: Implement** (append to `backend/data/oanda.py`). Oanda's v3 candles accept RFC3339 `from`/`to` with `count` ignored when both are set:

```python
def fetch_klines_range(symbol: str, interval: str,
                       start_ms: int, end_ms: int) -> pd.DataFrame:
    """Historical OHLCV for [start_ms, end_ms] (epoch ms) via Oanda's from/to.

    Same return shape as binance.fetch_klines_range. Skips incomplete candles.
    """
    instrument = normalize_instrument(symbol)
    gran = _GRAN.get(interval, "H1")
    fmt = "%Y-%m-%dT%H:%M:%S.000000000Z"
    frm = pd.to_datetime(start_ms, unit="ms").strftime(fmt)
    to = pd.to_datetime(end_ms, unit="ms").strftime(fmt)
    url = (f"{_base_url()}/v3/instruments/{instrument}/candles"
           f"?from={frm}&to={to}&granularity={gran}&price=M")
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise UnknownSymbolError(symbol, "Oanda") from e
        raise

    rows = []
    for c in data.get("candles", []):
        if not c.get("complete", True):
            continue
        mid = c["mid"]
        vol = float(c.get("volume", 0))
        rows.append({
            "timestamps": pd.to_datetime(c["time"]).tz_localize(None),
            "open": float(mid["o"]), "high": float(mid["h"]),
            "low": float(mid["l"]), "close": float(mid["c"]),
            "volume": vol, "amount": vol,
        })
    return pd.DataFrame(rows, columns=["timestamps", "open", "high", "low", "close", "volume", "amount"])
```

**Step 4: Run to verify pass** — `pytest backend/tests/test_replay.py -v` → 2 passed.

**Step 5: Commit**
```bash
git add backend/data/oanda.py backend/tests/test_replay.py
git commit -m "feat(replay): oanda fetch_klines_range for historical windows"
```

---

## Task 3: `build_replay_context` — the truncated MarketContext

**Objective:** Assemble a `MarketContext` whose indicators are computed only from candles at or before `as_of` (no lookahead), with positioning honestly marked unavailable.

**Files:**
- Create: `backend/signals/replay.py`
- Test: `backend/tests/test_replay.py`

**Step 1: Write failing tests**

```python
import time
import pandas as pd
import pytest
from backend.signals.context import MarketContext
from backend.signals import replay

def _df(closes, start="2024-01-01", freq="h"):
    ts = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame({
        "timestamps": ts, "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * len(closes), "amount": [1000.0] * len(closes),
    })

def test_build_replay_context_truncates_at_as_of(monkeypatch):
    # 100 hourly closes; as_of at candle 50. The provider must be asked for a
    # window ending at as_of (plus forward candles for the outcome, fetched
    # separately by the caller — here we only test context building).
    df = _df(list(range(1, 101)))
    as_of = int(df["timestamps"].iloc[50].timestamp())

    monkeypatch.setattr(replay, "_fetch_window",
                        lambda sym, iv, a, b: df)
    ctx = replay.build_replay_context("BTCUSDT", "1h", as_of, include_kronos=False)
    assert ctx.symbol == "BTCUSDT"
    # last_close must be the close AT as_of, not the latest close.
    assert ctx.indicators["last_close"] == pytest.approx(df["close"].iloc[50])
    # Positioning honestly unavailable in replay.
    assert ctx.funding["available"] is False
    assert ctx.open_interest["available"] is False
```

Note: `_fetch_window` is the module-internal helper (defined below) that routes to the right provider — we monkeypatch it so the test never hits the network.

**Step 2: Run to verify failure** — `pytest backend/tests/test_replay.py -v` → `ImportError`/`AttributeError` (no `replay` module).

**Step 3: Implement** `backend/signals/replay.py`

```python
"""Market Replay — run the Copilot/Debate as of a historical moment.

The LLM never sees candles after `as_of`: the indicator snapshot is computed on
the truncated frame, and positioning (funding/OI) is honestly marked unavailable
(no reliable free history from cloud IPs). The outcome pass is pure pandas math
on the forward window — no model narration, no hindsight bias.
"""
from __future__ import annotations

from backend.data.providers import get_provider, asset_class
from backend.data.indicators import snapshot, price_structure
from backend.signals.context import MarketContext

_INTERVAL_MS = {"15m": 15 * 60_000, "1h": 3_600_000, "4h": 4 * 3_600_000, "1d": 86_400_000}
# Candles of context the indicators need before as_of (matches live's 400).
_LOOKBACK = 400
OUTCOME_PERIODS = 24


def interval_ms(interval: str) -> int:
    return _INTERVAL_MS.get(interval, 3_600_000)


def _fetch_window(symbol: str, interval: str, start_ms: int, end_ms: int):
    provider = get_provider(symbol)
    return provider.fetch_klines_range(symbol, interval, start_ms, end_ms)


def build_replay_context(symbol: str, interval: str, as_of_s: int,
                         include_kronos: bool = True) -> MarketContext:
    """MarketContext as it would have looked at `as_of_s` (epoch seconds)."""
    step = interval_ms(interval)
    end_ms = as_of_s * 1000
    start_ms = end_ms - _LOOKBACK * step
    df = _fetch_window(symbol, interval, start_ms, end_ms)
    df = df[df["timestamps"] <= __import__("pandas").to_datetime(end_ms, unit="ms")]
    if len(df) < 60:
        raise ValueError(f"Not enough history before {as_of_s} for {symbol} ({len(df)} candles).")

    ctx = MarketContext(
        symbol=symbol,
        interval=interval,
        asset_class=asset_class(symbol),
        indicators=snapshot(df),
        funding={"available": False, "note": "unavailable for historical replay"},
        open_interest={"available": False, "note": "unavailable for historical replay"},
        structure=price_structure(df),
    )
    if include_kronos:
        from backend.signals.context import _fetch_kronos_range
        ctx.kronos_range = _fetch_kronos_range(df)
    return ctx


def fetch_outcome(symbol: str, interval: str, as_of_s: int,
                  periods: int = OUTCOME_PERIODS):
    """Candles AFTER as_of (the honest 'what happened next' window)."""
    step = interval_ms(interval)
    start_ms = as_of_s * 1000 + step  # strictly after as_of
    end_ms = start_ms + periods * step
    return _fetch_window(symbol, interval, start_ms, end_ms)


def score_outcome(entry_price: float, lean: str | None, outcome_df) -> dict:
    """Deterministic replay verdict, mirroring history.resolve_pending logic."""
    if outcome_df is None or len(outcome_df) == 0 or entry_price is None:
        return {"available": False, "note": "outcome window not yet elapsed"}
    final = float(outcome_df["close"].iloc[-1])
    hi = float(outcome_df["high"].max())
    lo = float(outcome_df["low"].min())
    move_pct = round((final - entry_price) / entry_price * 100, 2)
    if lean == "bullish":
        verdict = "correct" if final > entry_price else "incorrect"
    elif lean == "bearish":
        verdict = "correct" if final < entry_price else "incorrect"
    else:
        verdict = "flat"
    return {
        "available": True,
        "entry_price": entry_price,
        "final_close": final,
        "move_pct": move_pct,
        "max_excursion_up_pct": round((hi - entry_price) / entry_price * 100, 2),
        "max_excursion_down_pct": round((lo - entry_price) / entry_price * 100, 2),
        "verdict": verdict,
        "periods": len(outcome_df),
    }
```

(Replace the `__import__("pandas")` with a normal `import pandas as pd` at module top — shown inline only to keep the snippet self-contained.)

**Step 4: Run to verify pass** — `pytest backend/tests/test_replay.py -v` → all pass.

**Step 5: Commit**
```bash
git add backend/signals/replay.py backend/tests/test_replay.py
git commit -m "feat(replay): build_replay_context + deterministic outcome scorer"
```

---

## Task 4: `F_REPLAY` feature flag on the Premium tier

**Objective:** Gate replay behind Premium, same pattern as the other premium features.

**Files:**
- Modify: `backend/billing/__init__.py` (feature keys ~line 51, Premium features ~line 73)
- Test: `backend/tests/test_replay.py`

**Step 1: Write failing test**

```python
def test_replay_is_premium_only():
    from backend.billing import has_feature, F_REPLAY, FREE, PRO, PREMIUM
    assert has_feature(PREMIUM, F_REPLAY) is True
    assert has_feature(PRO, F_REPLAY) is False
    assert has_feature(FREE, F_REPLAY) is False
```

**Step 2: Run to verify failure** — `ImportError: cannot import name 'F_REPLAY'`.

**Step 3: Implement** — in `backend/billing/__init__.py` add after the `F_STRATEGY` line:

```python
F_REPLAY = "replay"        # market replay mode — Premium
```

and add `F_REPLAY` to the PREMIUM `features=frozenset({...})` set.

**Step 4: Run to verify pass** — `pytest backend/tests/test_replay.py::test_replay_is_premium_only -v` → PASS.

**Step 5: Commit**
```bash
git add backend/billing/__init__.py backend/tests/test_replay.py
git commit -m "feat(replay): F_REPLAY feature flag, Premium-only"
```

---

## Task 5: `POST /replay` endpoint

**Objective:** One endpoint that runs a copilot-mode or debate-mode replay and returns the call + deterministic outcome.

**Files:**
- Modify: `backend/api/main.py` (import F_REPLAY at line 42; add endpoint after `/debate`, ~line 553)
- Test: `backend/tests/test_replay.py`

**Step 1: Write failing test** (mock the router through the signals modules, mirroring `test_debate.py`'s `_ScriptedRouter` pattern; full code below in Step 3's test section)

```python
def test_replay_endpoint_premium_gate(client_user_free):
    r = client_user_free.post("/replay", json={
        "symbol": "BTCUSDT", "interval": "1h",
        "as_of": 1704070800, "mode": "copilot", "include_kronos": False,
    })
    assert r.status_code == 402
```

The `client_user_free` fixture: copy the pattern from `backend/tests/test_debate.py`'s API section (it builds a TestClient with `current_user_id` overridden and `get_tier` monkeypatched to return "free"). If that file exposes a reusable fixture, import it; otherwise define it locally in `test_replay.py`.

**Step 2: Run to verify failure** — 404 (route doesn't exist).

**Step 3: Implement** — in `backend/api/main.py`:

- Add `F_REPLAY` to the import on line 42.
- Add a module-level cache near the others: `replay_cache = TTLCache(int(os.environ.get("REPLAY_CACHE_TTL", "3600")))`.
- Add the endpoint after the `/debate` block (~line 553):

```python
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
        "note": "Replay answers as of the chosen moment — the model saw no future candles. Outcome is computed deterministically.",
    }
    replay_cache.set(cache_key, payload)
    return {**payload, "cached": False}
```

Check the top of main.py: `time` and `os` are already imported (used by other endpoints); `BaseModel`, `JSONResponse`, `Depends`, `Request`, `HTTPException`, `copilot_limiter`, `client_key`, `spend_guard`, `TTLCache`, `logger` are all already in scope there.

Then the passing test (mock both providers' `_fetch_window` via monkeypatch on the replay module, and use a scripted router via monkeypatching `backend.signals.copilot.AIRouter`):

```python
def test_replay_endpoint_copilot_happy_path(client_user_premium, monkeypatch):
    from backend.signals import replay as replay_mod
    closes = list(range(1, 101)) + list(range(100, 130))  # rises after as_of
    df = _df(closes)
    as_of = int(df["timestamps"].iloc[99].timestamp())
    monkeypatch.setattr(replay_mod, "_fetch_window", lambda *a, **k: df)
    # Stub the LLM: deterministic bullish call.
    class _R:
        class cost_log: total_usd = 0.0
        def complete(self, *a, **k):
            return json.dumps({"lean": "bullish", "conviction": 70, "summary": "s",
                               "drivers": [], "risks": [], "suggested_invalidation": "x"})
    monkeypatch.setattr("backend.signals.copilot.AIRouter", lambda *a, **k: _R())
    r = client_user_premium.post("/replay", json={
        "symbol": "BTCUSDT", "interval": "1h", "as_of": as_of,
        "mode": "copilot", "include_kronos": False})
    assert r.status_code == 200
    body = r.json()
    assert body["analysis"]["lean"] == "bullish"
    assert body["outcome"]["verdict"] == "correct"   # price rose after as_of
    assert body["outcome"]["move_pct"] > 0
```

**Step 4: Run to verify pass** — `pytest backend/tests/test_replay.py -v` → all pass (gate 402 + happy path 200).

**Step 5: Commit**
```bash
git add backend/api/main.py backend/tests/test_replay.py
git commit -m "feat(replay): POST /replay endpoint (copilot+debate modes, premium, cached)"
```

---

## Task 6: Frontend API client

**Objective:** Add a typed `replay()` call to the API hook.

**Files:**
- Modify: `frontend/lib/api.ts` (add after the `strategy` entry, ~line 99)

**Step 1: Implement** — add inside the `useMemo` object in `useApi()`:

```ts
replay: (body: { symbol: string; interval: string; as_of: number; mode?: string; include_kronos?: boolean }) =>
  request("/replay", { method: "POST", body: JSON.stringify(body) }),
```

**Step 2: Verify** — `cd frontend && npx tsc --noEmit` → clean.

**Step 3: Commit**
```bash
git add frontend/lib/api.ts
git commit -m "feat(replay): api client replay() call"
```

---

## Task 7: `Replay.tsx` component + tab

**Objective:** A Replay tab: pick symbol/interval/date-time/mode → run → see the historical call + what actually happened.

**Files:**
- Create: `frontend/components/Replay.tsx`
- Modify: `frontend/app/page.tsx` (tab list ~line 238 and render block ~line 260)

**Step 1: Component** (`frontend/components/Replay.tsx`) — follow the visual conventions of `TrackRecord.tsx` (same badges/colors: `text-bull`, `text-bear`, `bg-panel`, `border-white/10`):

Core structure (complete component; keep styling consistent with TrackRecord.tsx):

```tsx
"use client";

import { useState } from "react";
import type { useApi } from "@/lib/api";

type Api = ReturnType<typeof useApi>;

export default function Replay({ api, symbol, interval }: { api: Api; symbol: string; interval: string }) {
  const [asOf, setAsOf] = useState<string>("");       // datetime-local value
  const [mode, setMode] = useState<"copilot" | "debate">("copilot");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any | null>(null);

  async function run() {
    setError(null);
    setResult(null);
    if (!asOf) { setError("Pick a date/time to replay from."); return; }
    const epoch = Math.floor(new Date(asOf).getTime() / 1000);
    setLoading(true);
    try {
      const r = await api.replay({ symbol, interval, as_of: epoch, mode, include_kronos: false });
      setResult(r);
    } catch (e: any) {
      setError(e?.message || "Replay failed");
    } finally {
      setLoading(false);
    }
  }

  // Render: controls card (datetime-local input, mode toggle copilot/debate,
  // run button), then when result exists two stacked cards:
  //   1. "The call as of <ts>" — lean badge + conviction + summary (+ consensus
  //      block when mode === "debate").
  //   2. "What actually happened" — outcome.move_pct colored bull/bear,
  //      verdict badge, entry -> final close, max excursions.
  // Show a 402 upgrade CTA when error is the Premium gate (e.status === 402).
  // ...
}
```

Fill in the JSX by copying TrackRecord.tsx's card patterns (it has the same "stat cards + badges" layout). The component is presentational — all logic lives in the endpoint.

**Step 2: Wire the tab** in `frontend/app/page.tsx`:

- Add `"replay"` to the tabs array: `(["copilot", "scanner", "journal", "portfolio", "debate", "flow", "strategy", "alerts", "track", "replay"] as const)`.
- Extend the label ternary chain with `: t === "replay" ? "Replay" : "Track Record"` (adjust the final else accordingly).
- Add the render line near the others: `{view === "replay" && <Replay api={api} symbol={symbol} interval={interval} />}`
- Import it at top: `import Replay from "@/components/Replay";`

**Step 3: Verify** — `cd frontend && npx tsc --noEmit && npm run build` → clean.

**Step 4: Commit**
```bash
git add frontend/components/Replay.tsx frontend/app/page.tsx
git commit -m "feat(replay): Replay tab — historical call vs actual outcome"
```

---

## Task 8: Docs + final verification

**Files:**
- Modify: `README.md` (status table — add the Replay row)
- Modify: `DEPLOY.md` (no new env vars needed, but note `REPLAY_CACHE_TTL`)

**Step 1:** Add to README's status table:

```
| Market Replay (historical Copilot/Debate + honest outcome) | ✅ live (Premium) |
```

**Step 2:** Run the full verification suite:
```bash
cd /opt/data/projects/trading-copilot
source .venv/bin/activate && pytest backend/tests -q        # expect 183 + new (~190) passing
cd frontend && npx tsc --noEmit && npm run build            # clean
```

**Step 3:** Push and smoke-test prod:
```bash
git push origin master    # auto-deploys Railway
# Then with a Premium user's JWT: POST /replay on BTCUSDT 7 days ago, mode=copilot
# Expect: analysis.lean + outcome.verdict fields present.
```

**Step 4: Commit**
```bash
git add README.md DEPLOY.md
git commit -m "docs(replay): README status + deploy notes"
```

---

## Files changed (summary)

| File | Change |
|---|---|
| `backend/data/binance.py` | `fetch_klines_range()` |
| `backend/data/oanda.py` | `fetch_klines_range()` |
| `backend/signals/replay.py` | NEW — context builder + outcome scorer |
| `backend/billing/__init__.py` | `F_REPLAY`, Premium feature set |
| `backend/api/main.py` | `POST /replay` + cache |
| `backend/tests/test_replay.py` | NEW — ~8 tests |
| `frontend/lib/api.ts` | `replay()` client call |
| `frontend/components/Replay.tsx` | NEW — Replay tab |
| `frontend/app/page.tsx` | tab wiring |
| `README.md` / `DEPLOY.md` | docs |

## Risks, tradeoffs, open questions

1. **Positioning data in replays.** Funding/OI history is unavailable from Railway IPs (geo-blocked) and Oanda has no historical position book. Replay contexts mark them `available: False`, so replayed calls rely on technicals (+Kronos) only. This is honest, but a replayed call can differ from what the live copilot said at that time (which may have had funding/OI). The UI should state this in the note. *Mitigation:* the response already includes a `note` field explaining exactly this.
2. **LLM training-data leakage.** The model may "know" what happened to BTC on a famous date from training. This is inherent to any LLM replay; we mitigate by emphasizing the deterministic outcome panel (the trust artifact is the scoring, not the call) and by the `note` field. Worth one line in the UI.
3. **Oanda `from` window size limits.** Oanda caps responses at 5000 candles — 400 lookback + 24 outcome is far under; fine.
4. **90-day limit.** Chosen to keep Binance paging to 1-2 requests for 1h (90d * 24h = 2160 candles → 3 pages). Raising it is a one-line change if you want deeper history later.
5. **Open question — replay + track record:** should replayed calls be logged to `signal_history` (they'd be instantly scorable)? Recommendation: **no** — keep the track record purely live calls, so its accuracy stat can't be gamed by cherry-picked replay dates. Replay verdicts stand on their own.

## Estimated effort

Tasks 1-5 (backend): ~1.5h. Tasks 6-7 (frontend): ~1h. Task 8: 20min. Total ~3h with tests green at every step.
