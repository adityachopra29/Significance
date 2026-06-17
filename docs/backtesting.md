# Backtesting plan

Diagnostic and weight-calibration loop for the composite relevance score. Not implemented yet — reference for when we wire this in.

## Goals

1. **Diagnostic backtest** — Does `composite_score` predict forward abnormal returns? Break down by event type, score decile, and direction.
2. **Weight calibration** — Tune additive weights in `scoring.py` (`W_EVENT`, `W_MATERIALITY`, etc.) to improve rank correlation or a simple long-short spread on a holdout window.

## What we already store

| Table / column | Use |
|----------------|-----|
| `announcement_analysis.factor_*` | Replay scoring with different weights without re-calling the LLM |
| `materiality_hint`, `surprise_hint`, `llm_confidence` | Replay when factors were derived from hints |
| `event_study_results.car_t1/t5/t20` | Outcome labels (see leakage note below) |
| `raw_announcements.announced_at` | Event time alignment |
| `companies.yahoo_symbol` | Forward CAR recomputation |
| `price_daily` | Price series for batch event studies |

## Proposed module layout

```
backend/app/backtest/
  dataset.py      # SQL → labeled rows (features + outcomes)
  replay.py       # re-score from stored factors with a weight vector
  metrics.py      # IC, bucket CAR, hit rate, directional accuracy
  optimize.py     # grid/random search over weights (optional)
  export.py       # parquet/CSV snapshot before retention purge

backend/app/scripts/run_backtest.py
  --from DATE --to DATE
  --horizon 5|20
  --export /path/backtest.parquet
  --optimize-weights
```

## Phases

### Phase 1 — Diagnostic only (start here)

1. Export join: `raw_announcements` + `announcement_analysis` + `event_study_results` + `companies` for `analysis_status=done` in `[from, to]`.
2. Metrics:
   - Spearman IC: `composite_score` vs signed `car_t5` (sign by `direction`)
   - Decile buckets: mean/median CAR per score decile
   - By `event_type`: signal vs noise categories
   - Coverage: % rows with valid `car_t5`, % with `yahoo_symbol`
3. Output: CLI tables + optional Parquet for notebooks.

No schema changes. Run before enabling `PURGE_ENABLED=true`.

### Phase 2 — Weight replay

Refactor `scoring.py` to support replay from stored factors:

```python
@dataclass
class ScoreWeights:
    w_event: float = 0.28
    ...

def score_from_factors(factors: FactorRow, weights: ScoreWeights) -> float: ...
```

Optimize on a **train** window; validate on **holdout**. No LLM calls.

### Phase 3 — Label hygiene (important)

`event_study_results` at analysis time partly overlap with `factor_price_reaction` → circularity risk.

For honest backtest:

- **Label:** forward CAR from prices after `announced_at`, α/β fit only on pre-event window.
- **Feature:** factors at announcement time; either exclude `factor_price_reaction` from optimization or treat `ar_day0` as same-day-only.

Option: `backtest_labels` table or export column `label_car_t5_forward` with frozen methodology.

### Phase 4 — Product integration (optional)

- `GET /api/backtest/summary` — latest calibration run
- `SCORE_WEIGHTS_JSON` in `.env` for production weights without code deploy
- Research dashboard: score decile vs historical CAR

## Known risks

| Risk | Mitigation |
|------|------------|
| 15-day retention | Export during 90-day bootstrap; `PURGE_ENABLED=false` until export |
| Universe drift | Filter by `ingest_enabled` at event date |
| Missing Yahoo prices | Report coverage; exclude small caps separately |
| Announcement vs market close | Document event-day assumption; sensitivity ±1 day |
| LLM / schema changes | Stratify by `analysis_schema_version` |
| Triage selection bias | Backtest triage-passed only; report skipped rate separately |
| Multiple announcements/day | Cluster or top-score per (symbol, day) |
| Circular price factor | Exclude from optimization or disjoint windows |
| Low N per event_type | Pool tiers A/B/C or shrink to global mean |

## Baseline findings (pre-v2, BSE sample)

Run on local DB before schema v2 re-queue (for context only — not production truth):

- n ≈ 1,092 analyzed rows with `car_t5`
- IC(score, signed CAR T+5) ≈ **−0.57** (inverted ranking)
- Top decile underperformed bottom decile by ~3.7 pp on T+5 CAR
- `factor_price_reaction` (under-reaction) drove much of the inversion
- 0 rows on schema v2; 100% flagged stale by `audit_analysis`
- ~97% neutral direction calls

**Implication:** re-queue v2 analysis and revisit `factor_price_reaction` before weight optimization.

## Open decisions

1. **Objective metric** — Rank IC, top-decile long-short, or directional hit rate?
2. **Horizon** — T+1, T+5, or T+20 as product truth?
3. **Entry** — Close on announcement day vs next open (after-hours filings)?
4. **Skipped rows** — Separate triage calibration backtest?
5. **Re-run LLM** — Required after prompt/schema changes, or freeze to one schema version?
6. **Export destination** — Parquet, S3, or ClickHouse?
7. **Weight deployment** — `scoring.py`, env JSON, or DB table with version pinning?

## Score semantics (decide before calibration)

- **A) Actionable / under-reaction** — high score when price has not moved (current `factor_price_reaction` framing)
- **B) Magnitude** — high score when large move is likely (`|CAR|`)

Empirical baseline favored revisiting (A) or shifting toward (B).

## Recommended sequence

1. Re-queue analyses (`python -m app.scripts.audit_analysis --requeue`)
2. Let worker complete v2 + triage-filtered queue
3. Implement Phase 1 `run_backtest.py` + export
4. Decide price-reaction factor fate
5. Phase 2 weight search only if Phase 1 shows non-inverted LLM-only signal
