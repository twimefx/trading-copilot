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

## 3. Auth + Billing (Clerk + Stripe)

Users sign in with **Clerk**; paid plans are billed via **Stripe**. The backend
verifies the Clerk session JWT on every request and gates features/quotas by the
user's tier. Everything is env-driven — no code change to go live.

### 3a. Clerk (auth)
1. Create a Clerk application at dashboard.clerk.com. Enable Email + any social logins.
2. Copy from Clerk → **API Keys**:
   - Publishable key (`pk_live_***`) → Vercel env `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
   - Secret key (`sk_live_***`) → Vercel env `CLERK_SECRET_KEY`
3. From Clerk → **API Keys → Show JWKS URL** (Frontend API), set on **Railway**:
   - `CLERK_JWKS_URL` = `https://<slug>.clerk.accounts.dev/.well-known/jwks.json`
   - `CLERK_ISSUER`   = `https://<slug>.clerk.accounts.dev`
   - `CLERK_AUTHORIZED_PARTIES` (optional) = your Vercel + custom domains, comma-separated
4. Leave `AUTH_DEV_ALLOW_HEADER` unset in prod (the legacy `X-Owner-Id` fallback is dev-only).

### 3b. Stripe (billing)
1. In Stripe → **Products**, create two recurring prices: Pro ($49/mo) and Premium ($199/mo).
   Copy each **Price ID** (`price_***`).
2. Set on **Railway**:
   - `STRIPE_SECRET_KEY` = `sk_live_***`
   - `STRIPE_PRICE_PRO` = the Pro price id
   - `STRIPE_PRICE_PREMIUM` = the Premium price id
   - `BILLING_SUCCESS_URL`, `BILLING_CANCEL_URL`, `BILLING_PORTAL_RETURN_URL` = your frontend URLs
3. In Stripe → **Developers → Webhooks**, add an endpoint:
   - URL: `https://<backend>.up.railway.app/billing/webhook`
   - Events: `checkout.session.completed`, `customer.subscription.updated`,
     `customer.subscription.created`, `customer.subscription.deleted`
   - Copy the **Signing secret** (`whsec_***`) → Railway `STRIPE_WEBHOOK_SECRET`

### 3c. Verify
```bash
# Unauthenticated call is rejected:
curl -s -o /dev/null -w "%{http_code}\n" -X POST <backend>/copilot \
  -H 'Content-Type: application/json' -d '{"symbol":"BTCUSDT"}'   # -> 401
```
Then in the browser: sign up (free tier) → run analyses until the daily cap →
the upgrade prompt appears → "Upgrade to Pro" opens Stripe Checkout → after paying,
the webhook flips your tier and the badge updates to PRO with a higher quota.

**Tier defaults** (all env-overridable in `backend/billing/__init__.py`):
Free = 3 Copilot analyses/day, scanner capped at 5 symbols.
Pro ($49) = 100/day, 25 symbols, journal + forex.
Premium ($199) = unlimited, 100 symbols.

---

## 4. Verify the live demo
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

## 5. Alerts + cost digest (scheduler)

Alerts are user-created rules (`POST /alerts`) evaluated on a schedule. Delivery is
Telegram and/or SMTP email; a rule with neither just logs an event in-app.

Backend env vars:

| Variable | Purpose |
|---|---|
| `ALERT_SCHEDULER_KEY` | shared secret the scheduler POSTs to `/alerts/check` + `/admin/cost-digest` |
| `ALERT_TELEGRAM_BOT_TOKEN` | bot token for Telegram delivery |
| `ALERT_TELEGRAM_DEFAULT_CHAT_ID` | fallback chat for rules without their own |
| `ALERT_SMTP_URL` / `ALERT_EMAIL_FROM` | (optional) `smtp://user:pass@host:587` + sender |
| `ALERT_DIGEST_EMAIL` | (optional) weekly cost-digest recipient |

The evaluator runs every 15 min from a Hermes cron script (`copilot_scheduler.sh`),
which also triggers the Monday cost digest. The signal track record
(`GET /signals/history`, `/signals/stats`) is public and scores every Copilot call
24 periods after the fact — no cherry-picking.

---

## Trade Journal (persistence)

The journal lets users save Copilot analyses and track trade outcomes (status,
entry/exit, P&L, notes) with a running win-rate. Entries are scoped to the
**authenticated Clerk user id** (see section 3), so each signed-in user sees only
their own journal. The journal is a paid-tier feature (Pro/Premium).

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

Endpoints (all require a valid Clerk JWT via `Authorization: Bearer ***; the
frontend attaches it automatically):
`POST /journal`, `GET /journal[?status=]`, `GET /journal/stats`,
`GET|PATCH|DELETE /journal/{id}`.

Verify after adding Postgres (needs a real Clerk JWT — easiest to test via the
signed-in UI; the curl below shows the shape):
```bash
curl -s -X POST <backend-url>/journal -H 'Content-Type: application/json' \
  -H "Authorization: *** <clerk-jwt>" -d '{"symbol":"BTCUSDT","status":"idea"}'
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
