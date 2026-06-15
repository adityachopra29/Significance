"""Ingestion: pull from sources, dedup, map to companies, persist as raw_announcements.

Announcements are deduplicated by content_hash (source + BSE NEWSID). Once analyzed,
rows stay in the DB with analysis_status=done — backfills and re-adding companies
only insert genuinely new filings; existing ones are skipped (no re-analysis).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select

from app.config import settings
from app.db.base import session_scope
from app.db.models import AnalysisStatus, Company, RawAnnouncement
from app.sources.base import RawAnnouncementDTO, Source
from app.sources.bse import BSEAnnouncementsSource

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    inserted: int = 0
    skipped: int = 0  # already in DB (analysis preserved)

    @property
    def fetched(self) -> int:
        return self.inserted + self.skipped


def default_sources() -> list[Source]:
    # Lightweight real-time poll: only the newest pages of today's all-feed.
    return [BSEAnnouncementsSource(max_pages=3)]


def _since(session) -> dt.datetime:
    latest = session.scalar(select(func.max(RawAnnouncement.announced_at)))
    if latest is not None:
        return latest - dt.timedelta(hours=6)  # small overlap to avoid gaps
    return dt.datetime.now() - dt.timedelta(days=settings.backfill_days)


def _company_index(session) -> dict[str, int]:
    """Map active companies' BSE scrip code -> company id."""
    rows = session.execute(
        select(Company.bse_scrip_code, Company.id).where(Company.active.is_(True))
    ).all()
    return {str(code): cid for code, cid in rows if code}


def run_ingestion(sources: list[Source] | None = None) -> dict:
    sources = sources or default_sources()
    inserted = 0
    seen = 0

    with session_scope() as session:
        since = _since(session)
        company_idx = _company_index(session)
        universe_only = bool(company_idx)

        for src in sources:
            try:
                dtos = src.fetch(since=since)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Source %s failed: %s", src.name, exc)
                continue

            for dto in dtos:
                seen += 1
                # Restrict to our universe (BSE 500) once the master is loaded.
                company_id = company_idx.get(str(dto.bse_scrip_code)) if dto.bse_scrip_code else None
                if universe_only and company_id is None:
                    continue
                if _insert_if_new(session, dto, company_id):
                    inserted += 1

    logger.info("Ingestion: %d seen, %d inserted", seen, inserted)
    return {"seen": seen, "inserted": inserted}


def backfill_universe(days: int | None = None) -> dict:
    """Complete per-scrip backfill for every active company over `days`.

    Skips announcements already stored (by content_hash); only new rows are
    inserted with analysis_status=pending.
    """
    days = days or settings.backfill_days

    with session_scope() as session:
        companies = session.execute(
            select(Company.id, Company.bse_scrip_code).where(Company.active.is_(True))
        ).all()

    total = BackfillResult()
    src = BSEAnnouncementsSource()
    client = src.warmed_client()
    try:
        for idx, (cid, scrip) in enumerate(companies, start=1):
            if not scrip:
                continue
            try:
                result = _backfill_one_scrip(src, cid, str(scrip), days, client=client)
                total.inserted += result.inserted
                total.skipped += result.skipped
            except Exception as exc:  # noqa: BLE001
                logger.warning("Backfill failed for scrip %s: %s", scrip, exc)
                continue
            if idx % 50 == 0:
                logger.info(
                    "Backfill progress: %d/%d companies, %d new, %d cached",
                    idx,
                    len(companies),
                    total.inserted,
                    total.skipped,
                )
    finally:
        client.close()
    logger.info(
        "Backfill universe: %d companies, %d new, %d cached",
        len(companies),
        total.inserted,
        total.skipped,
    )
    return {
        "companies": len(companies),
        "inserted": total.inserted,
        "skipped": total.skipped,
    }


def backfill_company(company_id: int, days: int | None = None) -> BackfillResult:
    """Per-scrip backfill for one company. Re-adding a stock reuses cached analysis."""
    days = days or settings.backfill_days
    src = BSEAnnouncementsSource()

    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None or not company.bse_scrip_code:
            return BackfillResult()
        scrip = company.bse_scrip_code

    result = _backfill_one_scrip(src, company_id, str(scrip), days)
    logger.info(
        "Backfill company %s: %d new, %d cached",
        scrip,
        result.inserted,
        result.skipped,
    )
    return result


def _backfill_one_scrip(
    src: BSEAnnouncementsSource,
    company_id: int,
    scrip: str,
    days: int,
    client=None,
) -> BackfillResult:
    with session_scope() as session:
        since = _backfill_since(session, company_id, scrip, days)

    dtos = src.fetch_scrip(str(scrip), since=since, client=client)
    result = BackfillResult()
    with session_scope() as session:
        for dto in dtos:
            if _insert_if_new(session, dto, company_id):
                result.inserted += 1
            else:
                result.skipped += 1
    return result


def _backfill_since(session, company_id: int, scrip: str, days: int) -> dt.datetime:
    """Earliest date to fetch for a company.

    - No stored filings → full window (now - days).
    - Stored window doesn't reach back far enough → extend backward only.
    - Window already covered → incremental poll from latest filing minus overlap.
    """
    now = dt.datetime.now(dt.timezone.utc)
    earliest_needed = now - dt.timedelta(days=days)

    oldest, latest = session.execute(
        select(func.min(RawAnnouncement.announced_at), func.max(RawAnnouncement.announced_at)).where(
            or_(
                RawAnnouncement.company_id == company_id,
                RawAnnouncement.bse_scrip_code == scrip,
            )
        )
    ).one()

    if oldest is None:
        return earliest_needed.replace(tzinfo=None)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.timezone.utc)
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)

    if oldest > earliest_needed:
        return earliest_needed.replace(tzinfo=None)
    if latest is not None:
        return (latest - dt.timedelta(hours=6)).replace(tzinfo=None)
    return earliest_needed.replace(tzinfo=None)


def _insert_if_new(session, dto: RawAnnouncementDTO, company_id: int | None) -> bool:
    chash = dto.content_hash()
    existing = session.scalar(
        select(RawAnnouncement).where(RawAnnouncement.content_hash == chash)
    )
    if existing is not None:
        # Re-link if company was removed and re-added (same filing, same analysis cache).
        if company_id and existing.company_id != company_id:
            existing.company_id = company_id
        if dto.bse_scrip_code and not existing.bse_scrip_code:
            existing.bse_scrip_code = dto.bse_scrip_code
        return False
    session.add(
        RawAnnouncement(
            source=dto.source,
            external_id=dto.external_id,
            content_hash=chash,
            bse_scrip_code=dto.bse_scrip_code,
            company_id=company_id,
            headline=dto.headline,
            body=dto.body,
            category=dto.category,
            subcategory=dto.subcategory,
            attachment_url=dto.attachment_url,
            announced_at=dto.announced_at,
            analysis_status=AnalysisStatus.pending,
            raw_json=dto.raw_json,
        )
    )
    return True
