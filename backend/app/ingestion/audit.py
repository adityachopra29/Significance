"""Persist per-filing ingest decisions for backfill and poll runs."""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from contextlib import contextmanager

from app.db.base import session_scope
from app.db.models import IngestDecision, IngestRun
from app.sources.base import RawAnnouncementDTO

logger = logging.getLogger(__name__)

_current_run_id: str | None = None


@contextmanager
def ingest_run(kind: str, *, days: int | None = None):
    """Attach ingest decision logging to a backfill or poll run."""
    global _current_run_id
    run_id = uuid.uuid4().hex
    with session_scope() as session:
        session.add(
            IngestRun(
                id=run_id,
                kind=kind,
                days=days,
                started_at=dt.datetime.now(dt.timezone.utc),
            )
        )
    _current_run_id = run_id
    logger.info("Ingest audit run started: %s (%s, days=%s)", run_id, kind, days)
    try:
        yield run_id
    finally:
        _current_run_id = None


def finish_run(run_id: str, stats: dict) -> None:
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is not None:
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            run.stats_json = stats


def log_decision(
    decision: str,
    dto: RawAnnouncementDTO,
    *,
    company_id: int | None = None,
    announcement_id: int | None = None,
    triage_passed: bool | None = None,
    session=None,
) -> None:
    if _current_run_id is None:
        return
    row = IngestDecision(
        run_id=_current_run_id,
        source=dto.source,
        external_id=dto.external_id,
        headline=(dto.headline or "")[:500],
        bse_scrip_code=dto.bse_scrip_code,
        nse_symbol=dto.nse_symbol,
        company_id=company_id,
        announcement_id=announcement_id,
        decision=decision,
        triage_passed=triage_passed,
        announced_at=dto.announced_at,
    )
    if session is not None:
        session.add(row)
        return
    with session_scope() as session:
        session.add(row)


def summarize_run(run_id: str) -> dict:
    from sqlalchemy import func, select

    with session_scope() as session:
        rows = session.execute(
            select(IngestDecision.decision, func.count())
            .where(IngestDecision.run_id == run_id)
            .group_by(IngestDecision.decision)
        ).all()
        by_source = session.execute(
            select(IngestDecision.source, IngestDecision.decision, func.count())
            .where(IngestDecision.run_id == run_id)
            .group_by(IngestDecision.source, IngestDecision.decision)
        ).all()
    counts = {decision: count for decision, count in rows}
    source_counts: dict[str, dict[str, int]] = {}
    for source, decision, count in by_source:
        source_counts.setdefault(source, {})[decision] = count
    total = sum(counts.values())
    return {"run_id": run_id, "total_seen": total, "by_decision": counts, "by_source": source_counts}
