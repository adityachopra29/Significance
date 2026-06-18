# NSE corporate announcement ingest

NSE filings are ingested alongside BSE via `NSEAnnouncementsSource` (`backend/app/sources/nse.py`).

## How it works

| Piece | Behavior |
|-------|----------|
| **Poll** | Worker calls `run_ingestion()` every `POLL_INTERVAL_SECONDS`; NSE included when `NSE_INGEST_ENABLED=true` |
| **API** | `GET https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` |
| **Mapping** | `nse_symbol` on each row → `companies.nse_symbol` where `ingest_enabled=true` |
| **Dedup** | Per-source `content_hash` (`nse:{seq_id}`); NSE skipped if BSE row exists for same company ±20 min |
| **Backfill** | `backfill_nse_days()` — one API call per calendar day (fast); BSE still per-scrip |
| **Body text** | NSE often includes `attchmntText` in JSON (less PDF fetching needed) |

## Configuration

```env
NSE_INGEST_ENABLED=true
# Optional — required on many cloud hosts:
NSE_PROXY_URL=http://user:pass@proxy.example.com:8080
```

## Cloud deployment (AWS / Azure)

### What works out of the box

| Component | AWS/Azure | Notes |
|-----------|-----------|-------|
| **BSE ingest** | ✅ Usually works | Datacenter IPs tolerated; polite rate limits |
| **Postgres** | ✅ | RDS / Azure Database for PostgreSQL |
| **FastAPI + worker** | ✅ | Container Apps, ECS, App Runner, etc. |
| **Yahoo prices** | ✅ | Standard HTTPS egress |
| **LLM APIs** | ✅ | OpenAI/Anthropic from cloud |

### What often breaks

| Component | Risk | Mitigation |
|-----------|------|------------|
| **NSE JSON API** | **High** — 403 from AWS/Azure NAT IPs | `NSE_PROXY_URL` via residential/ISP proxy |
| **NSE cookie warm** | Homepage may 403 even when API works | Session module tries filings page; proxy helps |
| **NSE archives PDFs** | `nsearchives.nseindia.com` — same IP rules | Proxy for PDF fetch if needed later |

### Recommended cloud architecture

```
                    ┌─────────────────┐
  Vercel ──────────►│  FastAPI (API)  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   PostgreSQL    │
                    └────────▲────────┘
                             │
┌──────────────┐     ┌────────┴────────┐
│ BSE API      │────►│  Worker         │
│ (direct)     │     │  poll + analyze │
└──────────────┘     └────────┬────────┘
                              │
┌──────────────┐              │
│ NSE API      │◄─────────────┘  via NSE_PROXY_URL (if 403)
│ (proxy opt.) │
└──────────────┘
```

### Proxy options (if NSE returns 403)

1. **Residential proxy provider** (Bright Data, Oxylabs, Smartproxy) — set `NSE_PROXY_URL`
2. **Small VPS with residential ISP** (not AWS) running only the poll job — overkill for v1
3. **Disable NSE on cloud** — `NSE_INGEST_ENABLED=false`; rely on BSE for dual-listed names until proxy is ready

### Infra checklist

- [ ] Postgres (managed)
- [ ] API container + worker container (separate processes)
- [ ] Secrets: `DATABASE_URL`, `LLM_API_KEY`, optional `NSE_PROXY_URL`
- [ ] Outbound HTTPS allowed (443)
- [ ] `CORS_ORIGINS` → frontend URL
- [ ] `PURGE_ENABLED=false` during bootstrap backfill
- [ ] Run `load_universe --mode merged` before worker backfill
- [ ] Test NSE from cloud: `python -m app.scripts.test_nse_source --days 1`

### Azure (from README)

- Container Apps: API (`uvicorn`) + worker (`python -m app.run_worker`)
- Azure Database for PostgreSQL
- Frontend on Vercel

### AWS equivalent

- ECS Fargate or App Runner (API + worker tasks)
- RDS PostgreSQL
- Secrets Manager for env vars
- Optional: NAT Gateway egress — **may trigger NSE blocks**; test early

## Local verification

```bash
cd backend && source .venv/bin/activate
python -m app.scripts.test_nse_source --days 1
python -m app.scripts.test_nse_source --symbol RELIANCE --days 7

# Full ingest (BSE + NSE):
python -c "from app.ingestion.ingest import run_ingestion; print(run_ingestion())"
```

## Dedup (BSE + NSE)

Dual-listed filings are deduplicated before insert:

1. **`exchange_dedup_hash`** — `company_id` + calendar day + normalized headline
2. **Time-window fallback** — same company ±30 minutes on the other exchange
3. **Merge** — if duplicate found, enrich existing row (attachment URL, body text, symbols) instead of re-analyzing

Backfill hashes and remove historical duplicates:

```bash
python -m app.scripts.dedupe_announcements --dry-run
python -m app.scripts.dedupe_announcements
```

## Universe: NSE-only listings

`load_universe --mode merged` loads:

1. All BSE equities (with NSE symbol via ISIN when available)
2. NSE EQUITY_L names whose ISIN is **not** on BSE (`bse_scrip_code` null, `nse_symbol` set)

```bash
python -m app.scripts.load_universe --mode merged --enrich
```

## Not in scope yet

- NSE-only listings without ISIN in EQUITY_L edge cases
- SME / debt / REIT announcement indices
- Fuzzy cross-exchange headline matching (time-window dedup only)
- Bulk/block deals, insider (PIT) feeds

## Pre-deploy gap report

Run `python -m app.scripts.audit_pre_deploy` and see [pre-deploy-audit.md](./pre-deploy-audit.md) for NSE-only ingest risk and universe gaps.
