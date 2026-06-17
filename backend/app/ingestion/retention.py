"""Data retention: purge old announcements per configured TTL."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import delete, select, update

from app.config import settings
from app.db.base import session_scope
from app.db.models import (
    AnalysisStatus,
    AnnouncementAnalysis,
    EventStudyResult,
    RawAnnouncement,
)

logger = logging.getLogger(__name__)


def run_retention() -> dict:
    if not settings.purge_enabled:
        return {"purged": 0, "attachments_cleared": 0, "skipped": "purge_disabled"}

    now = dt.datetime.now(dt.timezone.utc)
    retention_cutoff = now - dt.timedelta(days=settings.retention_days)
    attach_cutoff = now - dt.timedelta(days=settings.attachment_retention_days)

    attachments_cleared = 0
    purged = 0

    with session_scope() as session:
        # Strip heavy PDF text first.
        attach_rows = session.scalars(
            select(RawAnnouncement.id).where(
                RawAnnouncement.announced_at < attach_cutoff,
                RawAnnouncement.attachment_text.isnot(None),
            )
        ).all()
        if attach_rows:
            session.execute(
                update(RawAnnouncement)
                .where(RawAnnouncement.id.in_(attach_rows))
                .values(attachment_text=None)
            )
            attachments_cleared = len(attach_rows)

        # Delete old rows (never touch in-flight analysis).
        stale_ids = list(
            session.scalars(
                select(RawAnnouncement.id).where(
                    RawAnnouncement.announced_at < retention_cutoff,
                    RawAnnouncement.analysis_status.in_(
                        (AnalysisStatus.done, AnalysisStatus.skipped, AnalysisStatus.error)
                    ),
                )
            )
        )
        if stale_ids:
            session.execute(delete(EventStudyResult).where(EventStudyResult.announcement_id.in_(stale_ids)))
            session.execute(
                delete(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id.in_(stale_ids))
            )
            session.execute(delete(RawAnnouncement).where(RawAnnouncement.id.in_(stale_ids)))
            purged = len(stale_ids)

    logger.info(
        "Retention: purged=%d attachments_cleared=%d cutoff_days=%d",
        purged,
        attachments_cleared,
        settings.retention_days,
    )
    return {"purged": purged, "attachments_cleared": attachments_cleared}
