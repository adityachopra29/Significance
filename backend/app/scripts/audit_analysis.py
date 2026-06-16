"""Audit analyzed announcements for stale or low-quality LLM output.

Usage:
    python -m app.scripts.audit_analysis
    python -m app.scripts.audit_analysis --requeue   # delete stale analysis + set pending
    python -m app.scripts.audit_analysis --limit 50
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import delete, select

from app.analysis.llm.base import ANALYSIS_SCHEMA_VERSION, VALID_EVENT_TYPES
from app.db.base import init_db, session_scope
from app.db.models import AnalysisStatus, AnnouncementAnalysis, EventStudyResult, RawAnnouncement

_NUMERIC_EXTRACTED_KEYS = (
    "amount_cr",
    "amount",
    "revenue_cr",
    "pat_cr",
    "ebitda_cr",
    "eps_rs",
    "yoy_pct",
    "qoq_pct",
    "pct_change",
    "stake_pct",
)


def _has_numeric_extracted(extracted: dict | None) -> bool:
    if not isinstance(extracted, dict):
        return False
    for key in _NUMERIC_EXTRACTED_KEYS:
        val = extracted.get(key)
        if val not in (None, ""):
            return True
    return False


def _stale_reasons(ann: RawAnnouncement, analysis: AnnouncementAnalysis) -> list[str]:
    reasons: list[str] = []
    if analysis.analysis_schema_version != ANALYSIS_SCHEMA_VERSION:
        reasons.append(
            f"schema={analysis.analysis_schema_version or 'v1/missing'} (current {ANALYSIS_SCHEMA_VERSION})"
        )
    if ann.attachment_url and not (ann.attachment_text and ann.attachment_text.strip()):
        if ann.attachment_fetched:
            reasons.append("pdf_fetch_empty")
        else:
            reasons.append("pdf_not_fetched")
    if not _has_numeric_extracted(analysis.extracted) and analysis.materiality_hint is None:
        reasons.append("no_extracted_numbers_and_no_persisted_hint")
    if analysis.event_type and analysis.event_type not in VALID_EVENT_TYPES:
        reasons.append(f"invalid_event_type={analysis.event_type}")
    if (
        analysis.materiality_hint is None
        or analysis.surprise_hint is None
        or analysis.llm_confidence is None
    ):
        reasons.append("missing_persisted_llm_hints")
    return reasons


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit announcement analyses for stale LLM output")
    parser.add_argument("--limit", type=int, default=None, help="max rows to scan")
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="delete stale analysis rows and set announcement status to pending",
    )
    args = parser.parse_args()

    init_db()
    stale_rows: list[tuple[int, str, list[str]]] = []
    scanned = 0

    with session_scope() as session:
        stmt = (
            select(RawAnnouncement, AnnouncementAnalysis)
            .join(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
            .where(AnnouncementAnalysis.composite_score.isnot(None))
            .order_by(RawAnnouncement.announced_at.desc().nullslast())
        )
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = session.execute(stmt).all()
        scanned = len(rows)

        for ann, analysis in rows:
            reasons = _stale_reasons(ann, analysis)
            if reasons:
                stale_rows.append((ann.id, ann.headline[:80], reasons))

        if args.requeue and stale_rows:
            ids = [row[0] for row in stale_rows]
            session.execute(delete(EventStudyResult).where(EventStudyResult.announcement_id.in_(ids)))
            session.execute(delete(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id.in_(ids)))
            for aid in ids:
                ann = session.get(RawAnnouncement, aid)
                if ann is not None:
                    ann.analysis_status = AnalysisStatus.pending

    summary = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "scanned": scanned,
        "stale_count": len(stale_rows),
        "requeued": len(stale_rows) if args.requeue else 0,
        "stale": [
            {"id": aid, "headline": headline, "reasons": reasons}
            for aid, headline, reasons in stale_rows[:100]
        ],
    }
    print(json.dumps(summary, indent=2))
    if len(stale_rows) > 100:
        print(f"... and {len(stale_rows) - 100} more stale rows")


if __name__ == "__main__":
    main()
