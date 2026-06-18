# Cloud deployment

Deploy the Announcement Intelligence Engine as three components: **Postgres**, **API + worker**, **frontend**.

## Architecture

```
Vercel (Next.js)  ──►  FastAPI (API)     ──►  PostgreSQL
                              ▲
Worker (poll + analyze)  ─────┘
  ├─ BSE announcements (direct)
  ├─ NSE announcements (optional proxy)
  ├─ LLM analysis
  └─ Yahoo prices / event study
```

## 1. Prerequisites

Run the pre-deploy audit locally and review gaps:

```bash
cd backend && source .venv/bin/activate
python -m app.scripts.audit_pre_deploy
```

See [pre-deploy-audit.md](./pre-deploy-audit.md) for universe, market-cap, and ingest gap lists.

## 2. Database

- **Local dev:** `docker compose up -d` (Postgres on host port `5433`)
- **Production:** Managed Postgres (Render, RDS, Azure Database, etc.)
- Set `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname`

### Bootstrap (fresh DB)

```bash
python -m app.scripts.load_universe --mode merged
python -m app.scripts.run_ingest_backfill --days 90   # optional; 7 for quick start
```

Or restore a `pg_dump` from a verified local database.

### Local full stack (API + worker + Postgres)

```bash
docker compose up -d    # db + api:8000 + worker
```

Requires `.env` with `LLM_PROVIDER`, `LLM_API_KEY`, and `CORS_ORIGINS=http://localhost:3000`.

### Render (production)

1. Push repo to GitHub and connect at [Render Blueprints](https://dashboard.render.com/blueprints).
2. Apply `render.yaml` (creates Postgres, `aie-api`, `aie-worker`).
3. Set in dashboard: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `CORS_ORIGINS` (Vercel URL).
4. If NSE fails from cloud: `NSE_PROXY_URL` or `NSE_INGEST_ENABLED=false`.
5. SSH/shell on worker once: `python -m app.scripts.load_universe --mode merged` and `run_ingest_backfill --days 90` (or restore `pg_dump`).

### Vercel (frontend)

```env
NEXT_PUBLIC_API_URL=https://your-api.onrender.com
```

Set root directory to `frontend` in Vercel project settings. `vercel.json` included.

## 3. Backend services

Run as **two processes** (or two containers):

| Service | Command | Public? |
|---------|---------|---------|
| API | `uvicorn app.api.main:app --host 0.0.0.0 --port $PORT` | Yes (HTTPS) |
| Worker | `INGEST_ON_STARTUP=true python -m app.run_worker` | No |

### Required environment

```env
DATABASE_URL=...
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=...
CORS_ORIGINS=https://your-app.vercel.app
INGEST_ON_STARTUP=true
PURGE_ENABLED=false
NSE_INGEST_ENABLED=true
BACKFILL_DAYS=90
POLL_INTERVAL_SECONDS=60
```

### Optional

```env
NSE_PROXY_URL=http://user:pass@host:port   # if NSE 403 from cloud
MARKET_INDEX=^NSEI
```

## 4. Frontend (Vercel)

```env
NEXT_PUBLIC_API_URL=https://your-api.example.com
```

Build: `npm run build` (standard Next.js 15 app).

## 5. Post-deploy smoke tests

```bash
curl https://your-api/health
curl https://your-api/api/stats
python -m app.scripts.test_nse_source --days 1    # from worker container
```

## 6. Known limitations at deploy

| Area | Status |
|------|--------|
| Universe coverage | 100% of BSE active + full NSE EQ (see audit) |
| Market cap | ~96% — 205 BSE-only gaps (mostly ETFs / illiquid `.BO`) |
| NSE-only announcements | 48/103 names with no DB history — need working NSE from cloud |
| NSE market cap API | Not used (Akamai); BSE + Yahoo only |
| Ranked feed | Fills as worker analyzes backlog |
| Backtesting / alerts | Post-v1 ([backtesting.md](./backtesting.md)) |

## 7. Operations

| Job | Schedule | Notes |
|-----|----------|-------|
| Poll BSE + NSE | Every 60s | Worker |
| Analyze pending | Every 20s | Batch 50 |
| Market cap gaps | Daily 18:30 IST | `only_missing=True` |
| Retention purge | Daily 02:30 IST | Off until `PURGE_ENABLED=true` |

Re-run audit after major universe or ingest changes:

```bash
python -m app.scripts.audit_pre_deploy --output-dir data/pre_deploy_audit
```
