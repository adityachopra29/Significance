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

from app.analysis.triage import TriageAction, triage
from app.api import events as feed_events
from app.api.feed_helpers import feed_item_from_row
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
    return [BSEAnnouncementsSource(max_pages=3)]


def _since(session) -> dt.datetime:
    latest = session.scalar(select(func.max(RawAnnouncement.announced_at)))
    if latest is not None:
        return latest - dt.timedelta(hours=6)
    return dt.datetime.now() - dt.timedelta(days=settings.backfill_days)


def _company_index(session) -> dict[str, int]:
    """Map ingested universe BSE scrip codes -> company id."""
    rows = session.execute(
        select(Company.bse_scrip_code, Company.id).where(Company.ingest_enabled.is_(True))
    ).all()
    return {str(code): cid for code, cid in rows if code}


def _apply_triage(dto: RawAnnouncementDTO) -> dict:
    result = triage(
        dto.headline,
        dto.body,
        category=dto.category,
        subcategory=dto.subcategory,
    )
    if result.action == TriageAction.skip:
        return {
            "analysis_status": AnalysisStatus.skipped,
            "triage_passed": False,
            "triage_event_type": result.triage_event_type,
            "triage_tier": result.triage_tier,
            "triage_priority": result.triage_priority,
            "category_rank": result.category_rank,
            "skip_reason": result.skip_reason,
            "triage_reason": result.triage_reason,
        }
    return {
        "analysis_status": AnalysisStatus.pending,
        "triage_passed": True,
        "triage_event_type": result.triage_event_type,
        "triage_tier": result.triage_tier,
        "triage_priority": result.triage_priority,
        "category_rank": result.category_rank,
        "skip_reason": None,
        "triage_reason": result.triage_reason,
    }


def run_ingestion(sources: list[Source] | None = None) -> dict:
    sources = sources or default_sources()
    inserted = 0
    seen = 0
    new_ids: list[int] = []

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
                company_id = company_idx.get(str(dto.bse_scrip_code)) if dto.bse_scrip_code else None
                if universe_only and company_id is None:
                    continue
                ann_id = _insert_if_new(session, dto, company_id)
                if ann_id:
                    inserted += 1
                    new_ids.append(ann_id)

    for ann_id in new_ids:
        _emit_triaged(ann_id)

    logger.info("Ingestion: %d seen, %d inserted", seen, inserted)
    return {"seen": seen, "inserted": inserted}


def _emit_triaged(ann_id: int) -> None:
    try:
        with session_scope() as session:
            row = session.execute(
                select(RawAnnouncement, Company)
                .outerjoin(Company, Company.id == RawAnnouncement.company_id)
                .where(RawAnnouncement.id == ann_id)
            ).one_or_none()
            if row is None:
                return
            ann, company = row
            if not ann.triage_passed:
                return
            feed_events.publish("announcement_triaged", feed_item_from_row(ann, None, company).model_dump(mode="json"))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to emit triaged event for %d", ann_id)


def backfill_universe(days: int | None = None) -> dict:
    days = days or settings.backfill_days

    with session_scope() as session:
        companies = session.execute(
            select(Company.id, Company.bse_scrip_code).where(Company.ingest_enabled.is_(True))
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
            if idx % 100 == 0:
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
    days = days or settings.backfill_days
    src = BSEAnnouncementsSource()

    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None or not company.bse_scrip_code:
            return BackfillResult()
        scrip = company.bse_scrip_code

    return _backfill_one_scrip(src, company_id, str(scrip), days)


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
    new_ids: list[int] = []
    with session_scope() as session:
        for dto in dtos:
            ann_id = _insert_if_new(session, dto, company_id)
            if ann_id:
                result.inserted += 1
                new_ids.append(ann_id)
            else:
                result.skipped += 1

    for ann_id in new_ids:
        _emit_triaged(ann_id)
    return result


def _backfill_since(session, company_id: int, scrip: str, days: int) -> dt.datetime:
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


def _insert_if_new(session, dto: RawAnnouncementDTO, company_id: int | None) -> int | None:
    chash = dto.content_hash()
    existing = session.scalar(
        select(RawAnnouncement).where(RawAnnouncement.content_hash == chash)
    )
    if existing is not None:
        if company_id and existing.company_id != company_id:
            existing.company_id = company_id
        if dto.bse_scrip_code and not existing.bse_scrip_code:
            existing.bse_scrip_code = dto.bse_scrip_code
        return None

    triage_fields = _apply_triage(dto)
    ann = RawAnnouncement(
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
        raw_json=dto.raw_json,
        **triage_fields,
    )
    session.add(ann)
    session.flush()
    return ann.id
