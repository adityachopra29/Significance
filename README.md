# Announcement Intelligence Engine

Turns the firehose of Indian corporate disclosures into a **ranked, explainable, frequently-updated signal feed** that highlights announcements with genuine material impact on stock price — to help find the right stocks to trade.

> For research and analysis only. **Not investment advice.**

## What it does

1. **Ingests** BSE corporate announcements for the BSE 500 universe (direct from `api.bseindia.com` — BSE is lenient to cloud IPs, so no NSE anti-bot/proxy headaches in v1).
2. **Enriches** each announcement with a provider-agnostic LLM (event type, sentiment, materiality, plain-English summary) — with a no-API-key heuristic fallback so it runs out of the box.
3. **Runs an event study** on Yahoo `.NS` prices (market-model α/β → Abnormal Return, CAR, abnormal volume) to detect **under-reaction / already-priced-in**.
4. **Scores** each item on a transparent 8-factor composite (0–100) and **ranks** them.
5. **Serves a dashboard** (Next.js) with the ranked feed, full score breakdown, and event-study stats.

## Architecture

```
BSE feeds ─► ingest (dedup) ─► Postgres ─► analysis worker ─► Postgres ─► FastAPI ─► Next.js dashboard
                                               ├─ LLM (OpenAI/Anthropic/Gemini/heuristic)
                                               └─ event study (Yahoo .NS prices)
```

The 8 scoring factors: event-type weight, materiality-vs-size, surprise/novelty, sentiment, price-reaction (event study), liquidity, confidence, time-decay. See `backend/app/analysis/scoring.py`.

## Quick start

### 1. Database

```bash
docker compose up -d            # Postgres on localhost:5432
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env       # defaults work for local dev (LLM_PROVIDER=heuristic)

# Load the BSE 500 company master (seed of ~50 large caps included)
python -m app.scripts.load_companies                 # add --enrich for market caps via Yahoo

# Start the API
uvicorn app.api.main:app --reload --port 8000

# In a second terminal: start the poller + analysis worker
python -m app.run_worker
```

### 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev                       # http://localhost:3000
```

## Configuration (`.env`)

| Key | Default | Notes |
|-----|---------|-------|
| `DATABASE_URL` | `postgresql+psycopg2://aie:aie@127.0.0.1:5433/aie` | container maps host `5433` -> container `5432` to avoid clashing with a local Postgres |
| `LLM_PROVIDER` | `heuristic` | `heuristic` \| `openai` \| `anthropic` \| `gemini` |
| `LLM_MODEL` | (provider default) | e.g. `gpt-4o-mini` |
| `LLM_API_KEY` | empty | required for non-heuristic providers |
| `MARKET_INDEX` | `^NSEI` | Yahoo symbol used as market in the event study (Nifty 50) |
| `POLL_INTERVAL_SECONDS` | `60` | BSE poll cadence |
| `BACKFILL_DAYS` | `3` | initial history pull |
| `ESTIMATION_WINDOW_DAYS` | `120` | event-study estimation window |

To use a real LLM: `pip install openai` (or `anthropic` / `google-generativeai`), then set `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`.

## Deploying (Azure)

- **Backend + worker**: two Azure Container Apps (API runs `uvicorn`, worker runs `python -m app.run_worker`).
- **Database**: Azure Database for PostgreSQL.
- **Frontend**: Vercel (set `NEXT_PUBLIC_API_URL` to the API URL).

## Roadmap (post-v1)

- Alerts (Telegram/email) above a score threshold.
- NSE feeds via residential proxy → cross-exchange corroboration, bulk/block deals, insider/PIT.
- Backtesting/calibration loop: forward CAR by category & score bucket to tune the weights.
- ClickHouse for large-scale price/backtest analytics.
- Cheaper FinBERT tier + news/social sources + portfolio personalization.

See the full plan in `.cursor/plans/`.
