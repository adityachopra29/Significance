"""Ingestion: pull from sources, dedup, map to companies, persist as raw_announcements.

Announcements are deduplicated by content_hash (source + external id). NSE rows
that match an existing BSE filing for the same company within a short time window
are skipped to avoid double analysis on dual-listed names.
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
from app.sources.nse import NSEAnnouncementsSource

from app.ingestion.audit import finish_run, ingest_run, log_decision, summarize_run
from app.ingestion.dedup import CROSS_EXCHANGE_TIME_WINDOW, exchange_dedup_hash

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    inserted: int = 0
    skipped: int = 0  # already in DB (analysis preserved)
    cross_skipped: int = 0  # NSE dup of existing BSE row

    @property
    def fetched(self) -> int:
        return self.inserted + self.skipped + self.cross_skipped


def default_sources() -> list[Source]:
    sources: list[Source] = [BSEAnnouncementsSource(max_pages=3)]
    if settings.nse_ingest_enabled:
        sources.append(NSEAnnouncementsSource())
    return sources


def _since(session) -> dt.datetime:
    latest = session.scalar(select(func.max(RawAnnouncement.announced_at)))
    if latest is not None:
        return latest - dt.timedelta(hours=6)
    return dt.datetime.now() - dt.timedelta(days=settings.backfill_days)


def _company_indexes(session) -> tuple[dict[str, int], dict[str, int]]:
    """Return (bse_scrip_code -> id, nse_symbol -> id) for ingest_enabled companies."""
    bse_idx: dict[str, int] = {}
    nse_idx: dict[str, int] = {}
    rows = session.execute(
        select(Company.bse_scrip_code, Company.nse_symbol, Company.id).where(
            Company.ingest_enabled.is_(True)
        )
    ).all()
    for bse_code, nse_sym, cid in rows:
        if bse_code:
            bse_idx[str(bse_code)] = cid
        if nse_sym:
            nse_idx[str(nse_sym).upper()] = cid
    return bse_idx, nse_idx


def _resolve_company_id(
    dto: RawAnnouncementDTO,
    bse_idx: dict[str, int],
    nse_idx: dict[str, int],
) -> int | None:
    if dto.bse_scrip_code:
        cid = bse_idx.get(str(dto.bse_scrip_code))
        if cid is not None:
            return cid
    if dto.nse_symbol:
        return nse_idx.get(str(dto.nse_symbol).upper())
    return None


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
    tallies: dict[str, int] = {}
    new_ids: list[int] = []

    with ingest_run("poll") as run_id:
        with session_scope() as session:
            since = _since(session)
            bse_idx, nse_idx = _company_indexes(session)
            universe_only = bool(bse_idx or nse_idx)

            for src in sources:
                try:
                    dtos = src.fetch(since=since)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Source %s failed: %s", src.name, exc)
                    continue

                for dto in dtos:
                    company_id = _resolve_company_id(dto, bse_idx, nse_idx)
                    ann_id, decision = _ingest_dto(
                        session, dto, company_id, universe_only=universe_only
                    )
                    _tally(decision, tallies)
                    if ann_id:
                        new_ids.append(ann_id)

        stats = summarize_run(run_id)
        finish_run(run_id, stats)

    for ann_id in new_ids:
        _emit_triaged(ann_id)

    logger.info("Ingestion poll: %s", stats)
    return stats


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
    return run_backfill(days=days)


def run_backfill(days: int | None = None) -> dict:
    """Backfill BSE (all-market by day) + NSE (by day) with ingest audit logging."""
    days = days or settings.backfill_days
    new_ids: list[int] = []

    with ingest_run("backfill", days=days) as run_id:
        if settings.nse_ingest_enabled:
            new_ids.extend(_backfill_nse_days_inner(days))
        new_ids.extend(_backfill_bse_days_inner(days))
        stats = summarize_run(run_id)
        finish_run(run_id, stats)

    for ann_id in new_ids:
        _emit_triaged(ann_id)

    logger.info("Backfill %d days complete: %s", days, stats)
    return stats


def _backfill_nse_days_inner(days: int) -> list[int]:
    src = NSEAnnouncementsSource()
    today = dt.date.today()
    from_date = today - dt.timedelta(days=days)
    new_ids: list[int] = []
    client = src.warmed_client()
    try:
        with session_scope() as session:
            bse_idx, nse_idx = _company_indexes(session)
        dtos = src.fetch_range(from_date, today, client=client)
        with session_scope() as session:
            for dto in dtos:
                company_id = _resolve_company_id(dto, bse_idx, nse_idx)
                ann_id, _ = _ingest_dto(session, dto, company_id, universe_only=True)
                if ann_id:
                    new_ids.append(ann_id)
    finally:
        client.close()
    return new_ids


def _backfill_bse_days_inner(days: int, *, max_pages: int = 25) -> list[int]:
    """BSE all-market fetch by day (faster than per-scrip for initial backfill)."""
    since = dt.datetime.now() - dt.timedelta(days=days)
    src = BSEAnnouncementsSource(max_pages=max_pages)
    dtos = src.fetch(since=since)
    new_ids: list[int] = []
    with session_scope() as session:
        bse_idx, nse_idx = _company_indexes(session)
        for dto in dtos:
            company_id = _resolve_company_id(dto, bse_idx, nse_idx)
            ann_id, _ = _ingest_dto(session, dto, company_id, universe_only=True)
            if ann_id:
                new_ids.append(ann_id)
    return new_ids


def backfill_nse_days(days: int | None = None) -> dict:
    """Backfill NSE only (legacy helper). Prefer run_backfill()."""
    days = days or settings.backfill_days
    with ingest_run("backfill_nse", days=days) as run_id:
        new_ids = _backfill_nse_days_inner(days)
        stats = summarize_run(run_id)
        finish_run(run_id, stats)
    for ann_id in new_ids:
        _emit_triaged(ann_id)
    return stats


def backfill_bse_universe(days: int | None = None) -> dict:
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
                logger.warning("BSE backfill failed for scrip %s: %s", scrip, exc)
            if idx % 100 == 0:
                logger.info(
                    "BSE backfill progress: %d/%d companies, %d new, %d cached",
                    idx,
                    len(companies),
                    total.inserted,
                    total.skipped,
                )
    finally:
        client.close()
    logger.info(
        "BSE backfill: %d companies, %d new, %d cached",
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
    total = BackfillResult()

    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            return total

    if company.bse_scrip_code:
        bse = _backfill_one_scrip(BSEAnnouncementsSource(), company_id, str(company.bse_scrip_code), days)
        total.inserted += bse.inserted
        total.skipped += bse.skipped

    if settings.nse_ingest_enabled and company.nse_symbol:
        nse_src = NSEAnnouncementsSource()
        since = dt.datetime.now() - dt.timedelta(days=days)
        dtos = nse_src.fetch_symbol(company.nse_symbol, since=since)
        new_ids: list[int] = []
        with session_scope() as session:
            for dto in dtos:
                ann_id, decision = _insert_if_new(session, dto, company_id)
                if decision == "rejected_cross_exchange":
                    total.cross_skipped += 1
                elif ann_id:
                    total.inserted += 1
                    new_ids.append(ann_id)
                elif decision == "rejected_duplicate":
                    total.skipped += 1
        for ann_id in new_ids:
            _emit_triaged(ann_id)

    return total


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
            ann_id, decision = _insert_if_new(session, dto, company_id)
            if decision == "rejected_cross_exchange":
                result.cross_skipped += 1
            elif ann_id:
                result.inserted += 1
                new_ids.append(ann_id)
            elif decision == "rejected_duplicate":
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


def _find_exchange_duplicate(
    session,
    company_id: int | None,
    announced_at: dt.datetime | None,
    headline: str | None,
    *,
    subcategory: str | None = None,
    exclude_source: str | None = None,
) -> RawAnnouncement | None:
    """Find an existing row for the same filing on the other exchange."""
    if company_id is None:
        return None

    ex_hash = exchange_dedup_hash(company_id, announced_at, headline, subcategory=subcategory)
    if ex_hash:
        row = session.scalar(
            select(RawAnnouncement).where(RawAnnouncement.exchange_dedup_hash == ex_hash).limit(1)
        )
        if row is not None and row.source != exclude_source:
            return row

    if announced_at is None:
        return None
    at = announced_at.replace(tzinfo=None) if announced_at.tzinfo else announced_at
    lo = at - CROSS_EXCHANGE_TIME_WINDOW
    hi = at + CROSS_EXCHANGE_TIME_WINDOW
    stmt = (
        select(RawAnnouncement)
        .where(
            RawAnnouncement.company_id == company_id,
            RawAnnouncement.announced_at >= lo,
            RawAnnouncement.announced_at <= hi,
        )
        .limit(1)
    )
    if exclude_source:
        stmt = stmt.where(RawAnnouncement.source != exclude_source)
    return session.scalar(stmt)


def _merge_announcement_fields(existing: RawAnnouncement, dto: RawAnnouncementDTO) -> None:
    """Enrich an existing row with fields from the other exchange."""
    if dto.bse_scrip_code and not existing.bse_scrip_code:
        existing.bse_scrip_code = dto.bse_scrip_code
    if dto.nse_symbol and not existing.nse_symbol:
        existing.nse_symbol = dto.nse_symbol
    if dto.attachment_url and not existing.attachment_url:
        existing.attachment_url = dto.attachment_url
    if dto.body and (not existing.body or len(dto.body) > len(existing.body or "")):
        existing.body = dto.body
    if dto.external_id and not existing.external_id:
        existing.external_id = dto.external_id


def _insert_if_new(
    session,
    dto: RawAnnouncementDTO,
    company_id: int | None,
) -> tuple[int | None, str]:
    """Insert if new. Returns (announcement_id, decision_code)."""
    duplicate = _find_exchange_duplicate(
        session,
        company_id,
        dto.announced_at,
        dto.headline,
        subcategory=dto.subcategory,
        exclude_source=dto.source,
    )
    if duplicate is not None:
        _merge_announcement_fields(duplicate, dto)
        return None, "rejected_cross_exchange"

    chash = dto.content_hash()
    existing = session.scalar(
        select(RawAnnouncement).where(RawAnnouncement.content_hash == chash)
    )
    if existing is not None:
        if company_id and existing.company_id != company_id:
            existing.company_id = company_id
        _merge_announcement_fields(existing, dto)
        return None, "rejected_duplicate"

    ex_hash = exchange_dedup_hash(company_id, dto.announced_at, dto.headline, subcategory=dto.subcategory)
    triage_fields = _apply_triage(dto)
    ann = RawAnnouncement(
        source=dto.source,
        external_id=dto.external_id,
        content_hash=chash,
        exchange_dedup_hash=ex_hash,
        bse_scrip_code=dto.bse_scrip_code,
        nse_symbol=dto.nse_symbol,
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
    decision = "accepted_triage" if ann.triage_passed else "accepted_skipped_triage"
    return ann.id, decision


def _ingest_dto(
    session,
    dto: RawAnnouncementDTO,
    company_id: int | None,
    *,
    universe_only: bool,
) -> tuple[int | None, str]:
    if universe_only and company_id is None:
        log_decision("rejected_universe", dto, company_id=None)
        return None, "rejected_universe"

    ann_id, decision = _insert_if_new(session, dto, company_id)
    triage_passed = decision == "accepted_triage"
    log_decision(
        decision,
        dto,
        company_id=company_id,
        announcement_id=ann_id,
        triage_passed=triage_passed if ann_id else None,
    )
    return ann_id, decision


def _tally(decision: str, tallies: dict[str, int]) -> None:
    tallies[decision] = tallies.get(decision, 0) + 1
