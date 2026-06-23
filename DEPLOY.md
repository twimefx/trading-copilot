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
| `JOURNAL_DB_PATH` | `/data/journal.db` (path on a Railway volume — see Trade Journal below) |
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
entry/exit, P&L, notes) with a running win-rate. It's stored in **SQLite** —
no extra service to provision. Entries are scoped by an `X-Owner-Id` header (a
per-browser UUID); there's no auth yet, so each browser sees its own journal.

**Make storage durable on Railway** (otherwise the DB resets on every redeploy):

1. Railway → backend service → **Settings → Volumes → New Volume**.
2. Mount path: `/data` (any persistent path is fine).
3. Set the env var `JOURNAL_DB_PATH=/data/journal.db` so the DB lives on the volume.

If `JOURNAL_DB_PATH` is unset it defaults to `journal.db` in the working dir —
fine for local dev, but **ephemeral** on Railway without a volume.

Endpoints (all require the `X-Owner-Id` header; the frontend sends it automatically):
`POST /journal`, `GET /journal[?status=]`, `GET /journal/stats`,
`GET|PATCH|DELETE /journal/{id}`.

Verify after deploy:
```bash
curl -s -X POST <backend-url>/journal -H 'Content-Type: application/json' \
  -H 'X-Owner-Id: smoke-test' -d '{"symbol":"BTCUSDT","status":"idea"}'
curl -s <backend-url>/journal -H 'X-Owner-Id: smoke-test'   # should list it
```

> **Scaling note:** SQLite suits the single-instance MVP. When you scale to
> multiple backend instances, swap the store for Postgres (Railway add-on) —
> the `backend/journal/store.py` interface is small and deliberately portable.

---

## Notes
- **Secrets:** never commit `.env`. Set everything via the host dashboards.
- **Cost:** Anthropic usage is the main variable cost (~$0.06/Copilot analysis with Opus).
  The backend already has a TTL cache, per-IP rate limit, and a daily spend cap
  (env-overridable: `COPILOT_CACHE_TTL`, `COPILOT_RATE_PER_HOUR`, `DAILY_SPEND_CAP_USD`).
