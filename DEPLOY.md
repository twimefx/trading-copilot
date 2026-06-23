# Deployment Guide — AI Trading Copilot

Two pieces deploy separately:
- **Frontend** (Next.js) → **Vercel**
- **Backend** (FastAPI) → **Railway** (or Fly.io)

Kronos forecasting is **excluded from the production backend** (keeps it lean/cheap).
The Copilot + Scanner work fully without it; the "Kronos range" toggle degrades gracefully.

**Order of operations:** push to GitHub → deploy backend to Railway (generate domain)
→ deploy frontend to Vercel (`BACKEND_URL` = Railway URL) → set Railway `FRONTEND_ORIGIN`
= Vercel URL (closes the CORS loop) → open the Vercel URL and verify.

> **Security:** never paste live tokens into chat/logs. The flows below use browser
> login (no tokens). If a token ever leaks, rotate it immediately at the provider's
> token settings page.

---

## 0. Push to GitHub

The repo is committed locally (branch `master`). First-time push, browser login (no token):

```bash
# Install gh: macOS `brew install gh` · Linux `sudo apt install gh` (see cli.github.com)
gh auth login                  # GitHub.com → HTTPS → Authenticate with browser
cd /opt/data/projects/trading-copilot
gh repo create trading-copilot --private --source=. --remote=origin --push
```

This creates `github.com/twimefx/trading-copilot` (private), wires `origin`, and pushes.

Without `gh`: create an empty private repo in the web UI, then:
```bash
git remote add origin https://github.com/twimefx/trading-copilot.git
git push -u origin master      # prompts for username + a PAT as the password
```

---

## 1. Backend → Railway

```bash
npm i -g @railway/cli      # or: brew install railway
railway login              # opens browser
cd /opt/data/projects/trading-copilot
railway init               # create a new project
railway up                 # builds Dockerfile, deploys
```

Then set environment variables in the Railway dashboard (Variables tab):

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your sk-ant-… key |
| `OANDA_API_TOKEN` | your Oanda token |
| `OANDA_ENV` | `live` |
| `DATABASE_URL` | auto-injected by Railway when you add a Postgres database (see Trade Journal below) |
| `FRONTEND_ORIGIN` | your Vercel URL (after step 2), e.g. `https://trading-copilot.vercel.app` |

Railway gives you a public backend URL like `https://xxx.up.railway.app`.
If you don't see a domain: **Settings → Networking → Generate Domain**. Copy it.

Verify: `curl https://xxx.up.railway.app/health` → `{"status":"ok",...}`

---

## 2. Frontend → Vercel

```bash
npm i -g vercel
cd /opt/data/projects/trading-copilot/frontend
vercel                     # login + link project, accept defaults
```

Set environment variable in Vercel dashboard (or during `vercel` prompts):

| Variable | Value |
|---|---|
| `BACKEND_URL` | the Railway backend URL from step 1 |

Then deploy to production:
```bash
vercel --prod
```

Vercel gives you `https://trading-copilot.vercel.app`. Put that back into Railway's
`FRONTEND_ORIGIN` so CORS is locked to your domain.

---

## 3. Verify the live demo
1. Open the Vercel URL
2. Click BTCUSDT → Analyze → see the AI verdict
3. Try EUR/USD (forex), and the Scanner tab

---

## 4. Kronos Range Service (optional — real volatility forecast)

The Copilot works without this (it falls back to an honest ATR-based band). To enable
the real Kronos model range, deploy the separate service in `services/kronos/` and point
the backend at it. It's heavy (torch + model weights), so it runs as its OWN service.

Deploy (Railway 2nd service, or any Docker host):
```bash
# Build context MUST be the repo root (the Dockerfile clones the gitignored Kronos vendor):
docker build -f services/kronos/Dockerfile -t kronos-range .
# On Railway: New Service in the SAME project → Deploy from repo →
#   set Dockerfile path = services/kronos/Dockerfile, root = repo root.
# Generate a domain (or use the private service URL).
```
Then on the MAIN backend service, set:

| Variable | Value |
|---|---|
| `KRONOS_SERVICE_URL` | the Kronos service URL, e.g. `https://kronos-xxx.up.railway.app` |
| `KRONOS_TIMEOUT` | (optional) seconds to wait, default 120 — CPU inference is slow |
| `KRONOS_SAMPLE_COUNT` | (optional) forecast paths, default 5 |

Verify: `curl <kronos-url>/health` → `{"status":"ok",...}`. Then a Copilot call with the
Kronos toggle on returns `range_24h.source == "Kronos"` (instead of `"ATR estimate"`).

If `KRONOS_SERVICE_URL` is unset or the service is unreachable, the backend degrades
gracefully to the ATR estimate — Kronos is never a hard dependency.

> **Cost note:** CPU inference is ~30–90s/call and the service idles 24/7 on Railway.
> For heavier use, host it on a cheap on-demand GPU box (RunPod/Lambda) and point
> `KRONOS_SERVICE_URL` at it — no code change needed.

---

## Trade Journal (persistence)

The journal lets users save Copilot analyses and track trade outcomes (status,
entry/exit, P&L, notes) with a running win-rate. Entries are scoped by an
`X-Owner-Id` header (a per-browser UUID); there's no auth yet, so each browser
sees its own journal.

Storage auto-selects at runtime:

- **`DATABASE_URL` set → Postgres** (durable, survives redeploys, multi-instance
  safe). This is the prod path.
- **`DATABASE_URL` unset → SQLite** file at `JOURNAL_DB_PATH` (default
  `journal.db`). Zero-setup for local dev — but **ephemeral on Railway**, so
  don't rely on it in prod.

**Turn on durable storage — one click in Railway:**

1. In your Railway project, click **+ New → Database → Add PostgreSQL**.
2. That's it. Railway provisions the DB and **auto-injects `DATABASE_URL`** into
   the project. The backend reads it on boot, switches to Postgres, and creates
   the `journal_entries` table automatically (idempotent `init_db()`).
3. Redeploy the backend if it doesn't pick up the new var automatically.

No volume, no migration tool, no manual schema step. The `postgres`-vs-
`postgresql` scheme difference Railway sometimes emits is normalized in code.

Endpoints (all require the `X-Owner-Id` header; the frontend sends it automatically):
`POST /journal`, `GET /journal[?status=]`, `GET /journal/stats`,
`GET|PATCH|DELETE /journal/{id}`.

Verify after adding Postgres:
```bash
curl -s -X POST <backend-url>/journal -H 'Content-Type: application/json' \
  -H 'X-Owner-Id: smoke-test' -d '{"symbol":"BTCUSDT","status":"idea"}'
curl -s <backend-url>/journal -H 'X-Owner-Id: smoke-test'   # should list it
# Redeploy the backend, then list again — the entry SURVIVES (proves durability).
```

> **Driver note:** prod uses `psycopg[binary]` (prebuilt wheel — no system libpq
> or compiler needed, installs clean on `python:3.12-slim`). The store interface
> in `backend/journal/store.py` is small and dialect-agnostic; both the SQLite
> and Postgres paths are covered by `backend/tests/test_journal.py` (the PG tests
> spin up a real embedded Postgres via `pgserver` and skip if it's absent).

---

## Notes
- **Secrets:** never commit `.env`. Set everything via the host dashboards.
- **Cost:** Anthropic usage is the main variable cost (~$0.06/Copilot analysis with Opus).
  The backend already has a TTL cache, per-IP rate limit, and a daily spend cap
  (env-overridable: `COPILOT_CACHE_TTL`, `COPILOT_RATE_PER_HOUR`, `DAILY_SPEND_CAP_USD`).
