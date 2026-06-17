"""Backfill exchange_dedup_hash and remove duplicate cross-exchange announcements.

Keeps the row with analysis (or earliest ingest) and deletes the duplicate.

Usage:
    python -m app.scripts.dedupe_announcements --dry-run
    python -m app.scripts.dedupe_announcements
"""
from __future__ import annotations

import argparse

from sqlalchemy import delete, select

from app.db.base import init_db, session_scope
from app.db.models import AnalysisStatus, AnnouncementAnalysis, EventStudyResult, RawAnnouncement
from app.ingestion.dedup import exchange_dedup_hash
from app.ingestion.ingest import _find_exchange_duplicate


def _score_keep(row: RawAnnouncement) -> tuple[int, int]:
    """Higher = prefer keeping this row."""
    has_analysis = 1 if row.analysis_status == AnalysisStatus.done else 0
    body_len = len(row.body or "")
    return has_analysis, body_len


def dedupe(*, dry_run: bool) -> dict:
    init_db()
    updated_hash = 0
    removed = 0

    with session_scope() as session:
        rows = session.scalars(select(RawAnnouncement).order_by(RawAnnouncement.id)).all()
        for row in rows:
            ex_hash = exchange_dedup_hash(
                row.company_id, row.announced_at, row.headline, subcategory=row.subcategory
            )
            if ex_hash and row.exchange_dedup_hash != ex_hash:
                row.exchange_dedup_hash = ex_hash
                updated_hash += 1

        by_hash: dict[str, list[RawAnnouncement]] = {}
        for row in rows:
            if not row.exchange_dedup_hash:
                continue
            by_hash.setdefault(row.exchange_dedup_hash, []).append(row)

        for group in by_hash.values():
            if len(group) < 2:
                continue
            group.sort(key=_score_keep, reverse=True)
            keep, drop = group[0], group[1:]
            for dup in drop:
                if dup.source == keep.source:
                    continue
                removed += 1
                if not dry_run:
                    _delete_announcement(session, dup.id)
                dup._marked_drop = True  # type: ignore[attr-defined]

        # Time-window / headline pass for rows not already clustered
        remaining = [r for r in rows if not getattr(r, "_marked_drop", False)]
        seen_drop: set[int] = set()
        for row in remaining:
            if row.id in seen_drop:
                continue
            dup = _find_exchange_duplicate(
                session,
                row.company_id,
                row.announced_at,
                row.headline,
                subcategory=row.subcategory,
                exclude_source=row.source,
            )
            if dup is None or dup.id == row.id or dup.id in seen_drop:
                continue
            keep, drop = sorted([row, dup], key=_score_keep, reverse=True)
            removed += 1
            seen_drop.add(drop.id)
            if not dry_run:
                _delete_announcement(session, drop.id)

        if dry_run:
            session.rollback()

    return {"hashes_updated": updated_hash, "duplicates_removed": removed, "dry_run": dry_run}


def _delete_announcement(session, ann_id: int) -> None:
    session.execute(delete(EventStudyResult).where(EventStudyResult.announcement_id == ann_id))
    session.execute(delete(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id == ann_id))
    session.execute(delete(RawAnnouncement).where(RawAnnouncement.id == ann_id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = dedupe(dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
