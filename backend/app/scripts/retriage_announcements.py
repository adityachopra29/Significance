"""Re-run triage on existing raw_announcements rows (e.g. after triage rule changes).

Usage:
    python -m app.scripts.retriage_announcements
    python -m app.scripts.retriage_announcements --limit 5000
    python -m app.scripts.retriage_announcements --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.analysis.triage import TriageAction, triage
from app.db.base import init_db, session_scope
from app.db.models import AnalysisStatus, RawAnnouncement


def retriage(*, limit: int | None, dry_run: bool) -> None:
    init_db()
    updated = 0
    with session_scope() as session:
        stmt = select(RawAnnouncement).order_by(RawAnnouncement.id.desc())
        if limit:
            stmt = stmt.limit(limit)
        rows = session.scalars(stmt).all()
        for ann in rows:
            result = triage(
                ann.headline or "",
                ann.body,
                category=ann.category,
                subcategory=ann.subcategory,
            )
            ann.triage_event_type = result.triage_event_type
            ann.triage_tier = result.triage_tier
            ann.triage_priority = result.triage_priority
            ann.category_rank = result.category_rank
            ann.triage_reason = result.triage_reason
            ann.skip_reason = result.skip_reason

            if result.action == TriageAction.skip:
                ann.triage_passed = False
                if ann.analysis_status in (AnalysisStatus.pending, AnalysisStatus.skipped):
                    ann.analysis_status = AnalysisStatus.skipped
            else:
                ann.triage_passed = True
                if ann.analysis_status == AnalysisStatus.skipped:
                    ann.analysis_status = AnalysisStatus.pending
            updated += 1
        if dry_run:
            session.rollback()
            print(f"Dry run: would update {updated} announcements")
        else:
            print(f"Retriaged {updated} announcements")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    retriage(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
