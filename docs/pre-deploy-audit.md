# Pre-deploy audit

Generated snapshot: **2026-06-18** (re-run anytime with the command below).

This document summarizes universe gaps, market-cap coverage, and announcement ingest coverage before cloud deployment. Full machine-readable lists live under `backend/data/pre_deploy_audit/`.

## Regenerate

```bash
cd backend && source .venv/bin/activate
python -m app.scripts.audit_pre_deploy --output-dir data/pre_deploy_audit
```

| Output file | Contents |
|-------------|----------|
| `summary.json` | All section summaries |
| `missing_from_exchanges.json` | On BSE/NSE master but not in DB |
| `missing_market_cap.json` | Ingest-enabled companies with no `market_cap_cr` |
| `zero_announcements.json` | Ingest-enabled companies with **no rows** in `raw_announcements` |

---

## 1. Not loaded in universe (exchange → DB gaps)

Stocks **listed on BSE/NSE** but **missing from our `companies` table**.

| Gap | Count | Severity |
|-----|------:|----------|
| BSE active equities missing from DB | **0** | — |
| NSE EQ symbols missing from DB | **0** | — |
| NSE-only ISINs missing from DB | **0** | — |
| Stale NSE symbol on DB row | **0** | — |

Universe is complete after `load_universe --mode merged` (4,986 ingest-enabled companies as of last audit).

---

## 2. Market cap gaps (scoring impact)

Market cap feeds **materiality** and **liquidity** in `scoring.py`. Analysis still runs without it; scores are less precise (liquidity defaults to 0.7×).

| Metric | Value |
|--------|------:|
| Universe (ingest-enabled) | 4,986 |
| With market cap | 4,781 (**95.9%**) |
| **Missing market cap** | **204** |

### Breakdown

| Category | Count | Notes |
|----------|------:|-------|
| BSE-only, no cap | **204** | All gaps are BSE-only |
| Dual-listed, no cap | 0 | BSE API covers dual-listed names |
| NSE-only, no cap | 0 | — |
| Likely ETF / index funds | ~111 | Yahoo `.BO` often has no data; low priority for equity research |
| Other BSE-only equities | ~93 | Thin `.BO` symbols; BSE API sometimes empty, Yahoo fails |

**Resolution order in code:** BSE StockTrading API → NSE quote-equity (blocked by Akamai) → Yahoo `.BO` / `.NS`.

**Deploy impact:** Safe to deploy. ~93 small BSE-only names get degraded scoring; ETFs can be excluded from universe later if desired.

Full list: `backend/data/pre_deploy_audit/missing_market_cap.json`

---

## 3. Announcement ingest coverage

### Important: zero announcements ≠ broken ingest

Backfill was run for **7 days**. Most of the universe had **no Reg-30 filing** in that window. Zero rows in `raw_announcements` usually means **quiet stock**, not a poll failure.

| Metric | Value |
|--------|------:|
| Ingest-enabled companies | 4,985 |
| With ≥1 announcement in DB | 1,957 |
| **Zero announcements in DB** | **3,028** |
| Unmappable (no BSE scrip, no NSE symbol) | 0 |

### Zero announcements by listing type

| Listing | Zero ann | Likely cause |
|---------|--------:|--------------|
| BSE-only | 2,254 | No filing in 7-day backfill; BSE poll OK for new filings |
| Dual-listed | 726 | No filing in window; BSE poll covers going forward |
| **NSE-only** | **48** | **Depend on NSE ingest** — see below |

### High-risk ingest gaps (action before / during deploy)

These **103 NSE-only** names have no BSE scrip. **48** have never received an announcement in DB — they rely entirely on NSE poll/backfill.

| NSE symbol | Company |
|------------|---------|
| ABMINTLLTD | ABM International Limited |
| AAKASH | Aakash Exploration Services Limited |
| AARON | Aaron Industries Limited |
| ACEINTEG | Ace Integrated Solutions Limited |
| AHLADA | Ahlada Engineers Limited |
| AIROLAM | Airo Lam limited |
| AJOONI | Ajooni Biotech Limited |
| AKASH | Akash Infra-Projects Limited |
| AKG | Akg Exim Limited |
| ANTGRAPHIC | Antarctica Limited |
| ARTNIRMAN | Art Nirman Limited |
| BEARDSELL | Beardsell Limited |
| BOHRAIND | Bohra Industries Limited |
| CONSOFINVT | Consolidated Finvest & Holdings Limited |
| CROWN | Crown Lifters Limited |
| DANGEE | Dangee Dums Limited |
| GANGAFORGE | Ganga Forging Limited |
| GLOBAL | Global Education Limited |
| ICEMAKE | Ice Make Refrigeration Limited |
| KOTHARIPET | Kothari Petrochemicals Limited |
| KOTARISUG | Kothari Sugars And Chemicals Limited |
| KRITIKA | Kritika Wires Limited |
| LAGNAM | Lagnam Spintex Limited |
| LEMERITE | Le Merite Exports Limited |
| LIBAS | Libas Consumer Products Limited |
| MCL | M TEK COPPER LIMITED |
| MITTAL | Mittal Life Style Limited |
| MOKSH | Moksh Ornaments Limited |
| NBIFIN | N. B. I. Industrial Finance Company Limited |
| NITIRAJ | Nitiraj Engineers Limited |
| PANACHE | Panache Digilife Limited |
| PANSARI | Pansari Developers Limited |
| PAR | Par Drugs And Chemicals Limited |
| PKTEA | The Peria Karamalai Tea & Produce Company Limited |
| SAGARDEEP | Sagardeep Alloys Limited |
| SERVOTECH | Servotech Renewable Power System Limited |
| SHANTI | Shanti Overseas (India) Limited |
| OSWALSEEDS | ShreeOswal Seeds And Chemicals Limited |
| SIKKO | Sikko Industries Limited |
| SILLYMONKS | Silly Monks Entertainment Limited |
| SINTERCOM | Sintercom India Limited |
| SONAMLTD | SONAM LIMITED |
| TSFINV | TSF INVESTMENTS LIMITED |
| UNITEDTEA | The United Nilgiri Tea Estates Company Limited |
| WIPL | The Western India Plywoods Limited |
| UNIVASTU | Univastu India Limited |
| VARDHACRLC | Vardhman Acrylics Limited |
| VCL | Vaxtex Cotfab Limited |

**Cloud actions:**

1. Test from deploy region: `python -m app.scripts.test_nse_source --days 7`
2. If 403 → set `NSE_PROXY_URL` (home Pi / residential proxy) **or** `NSE_INGEST_ENABLED=false` and accept BSE-only coverage for dual-listed names
3. Optional: `python -m app.scripts.run_ingest_backfill --days 90` to widen history (increases NSE-only coverage if NSE works)

Full list: `backend/data/pre_deploy_audit/zero_announcements.json`

### What works without NSE

| Source | Coverage |
|--------|----------|
| BSE poll + per-scrip backfill | All **4,882** companies with `bse_scrip_code` |
| NSE poll | **103** NSE-only + faster date-range backfill |
| Cross-exchange dedup | Dual-listed filing on BSE → NSE duplicate skipped |

---

## 4. Deploy checklist (see also `docs/deployment.md`)

- [ ] Run `audit_pre_deploy` after final `load_universe`
- [ ] Universe complete (`audit_pre_deploy` shows 0 exchange gaps)
- [ ] Decide NSE strategy for 48 NSE-only names
- [ ] Accept 204 market-cap gaps or exclude ETFs from universe
- [ ] `PURGE_ENABLED=false` until backtest export (`docs/backtesting.md`)
- [ ] Migrate or bootstrap Postgres; worker with `INGEST_ON_STARTUP=true`

---

## Related docs

- [deployment.md](./deployment.md) — cloud setup
- [nse-ingest.md](./nse-ingest.md) — NSE proxy and cloud notes
- [backtesting.md](./backtesting.md) — post-deploy calibration (not blocking)
