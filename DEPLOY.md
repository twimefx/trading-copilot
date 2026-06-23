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

## Notes
- **Kronos in prod:** if you later want forecasting live, deploy `spikes`/Kronos as a
  separate worker on a GPU/large-CPU host and have the backend call it over HTTP.
- **Secrets:** never commit `.env`. Set everything via the host dashboards.
- **Cost:** Anthropic usage is the main variable cost (~$0.07/Copilot analysis with Opus).
  Consider caching + a Sonnet tier for a cheaper plan as traffic grows.
