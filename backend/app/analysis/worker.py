"""Analysis worker: enrich pending announcements with LLM + event study + score."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import exists, func, select

from app.analysis import pdf_extract
from app.analysis.event_study import EventStudyOutput, compute_event_study
from app.analysis.llm.factory import get_provider
from app.analysis.scoring import score
from app.config import settings
from app.db.base import session_scope
from app.db.models import (
    AnalysisStatus,
    AnnouncementAnalysis,
    Company,
    EventStudyResult,
    PriceDaily,
    RawAnnouncement,
)
from app.api import events as feed_events
from app.api.feed_helpers import feed_item_from_row
from app.prices import yahoo

logger = logging.getLogger(__name__)

_provider = None


def provider():
    global _provider
    if _provider is None:
        _provider = get_provider()
        logger.info("LLM provider: %s (model=%s)", _provider.name, _provider.model or "default")
    return _provider


def process_pending(limit: int | None = None) -> int:
    _sync_done_status()
    _recover_stuck_processing()
    limit = limit or settings.analyze_batch_size

    with session_scope() as session:
        ids = list(
            session.scalars(
                select(RawAnnouncement.id)
                .where(RawAnnouncement.analysis_status == AnalysisStatus.pending)
                .where(RawAnnouncement.triage_passed.is_(True))
                .order_by(
                    RawAnnouncement.triage_priority.asc().nullslast(),
                    RawAnnouncement.announced_at.desc().nullslast(),
                )
                .limit(limit)
            )
        )
    for aid in ids:
        try:
            process_one(aid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Analysis failed for announcement %d: %s", aid, exc)
            _mark_status(aid, AnalysisStatus.error)
    return len(ids)


def _sync_done_status() -> None:
    """Mark announcements done when analysis row already exists (cache hit)."""
    with session_scope() as session:
        stale = session.scalars(
            select(RawAnnouncement.id)
            .where(RawAnnouncement.analysis_status != AnalysisStatus.done)
            .where(
                exists(
                    select(AnnouncementAnalysis.id).where(
                        AnnouncementAnalysis.announcement_id == RawAnnouncement.id,
                        AnnouncementAnalysis.composite_score.isnot(None),
                    )
                )
            )
        ).all()
        for aid in stale:
            ann = session.get(RawAnnouncement, aid)
            if ann is not None:
                ann.analysis_status = AnalysisStatus.done
        if stale:
            logger.info("Synced %d announcements to done (cached analysis)", len(stale))


def _recover_stuck_processing() -> None:
    """Re-queue rows left in processing after a worker crash (no analysis saved)."""
    with session_scope() as session:
        stuck = session.scalars(
            select(RawAnnouncement.id)
            .where(RawAnnouncement.analysis_status == AnalysisStatus.processing)
            .where(
                ~exists(
                    select(AnnouncementAnalysis.id).where(
                        AnnouncementAnalysis.announcement_id == RawAnnouncement.id
                    )
                )
            )
        ).all()
        for aid in stuck:
            ann = session.get(RawAnnouncement, aid)
            if ann is not None:
                ann.analysis_status = AnalysisStatus.pending


def process_one(aid: int) -> None:
    # Phase 1: snapshot + mark processing.
    with session_scope() as session:
        ann = session.get(RawAnnouncement, aid)
        if ann is None:
            return
        if ann.analysis_status == AnalysisStatus.done:
            return
        existing = session.scalar(
            select(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id == aid)
        )
        if existing is not None and existing.composite_score is not None:
            ann.analysis_status = AnalysisStatus.done
            return
        ann.analysis_status = AnalysisStatus.processing
        session.flush()
        feed_events.publish("analysis_started", {"id": aid})
        company = session.get(Company, ann.company_id) if ann.company_id else None
        snap = {
            "headline": ann.headline,
            "body": ann.body,
            "company_name": company.name if company else None,
            "yahoo_symbol": company.yahoo_symbol if company else None,
            "market_cap_cr": company.market_cap_cr if company else None,
            "adv_cr": company.adv_cr if company else None,
            "company_matched": company is not None,
            "announced_at": ann.announced_at,
            "attachment_url": ann.attachment_url,
            "attachment_text": ann.attachment_text,
            "attachment_fetched": ann.attachment_fetched,
        }

    # Phase 1.5: fetch + cache the PDF attachment text (network, no open txn).
    attachment_text = snap["attachment_text"]
    if snap["attachment_url"] and (not snap["attachment_fetched"] or not attachment_text):
        attachment_text = pdf_extract.fetch_pdf_text(snap["attachment_url"])
        with session_scope() as session:
            ann = session.get(RawAnnouncement, aid)
            if ann is not None:
                ann.attachment_text = attachment_text
                ann.attachment_fetched = True

    # Phase 2: compute (no open transaction during network calls).
    llm = provider().analyze(
        snap["headline"],
        snap["body"],
        snap["company_name"],
        attachment_text,
        market_cap_cr=snap["market_cap_cr"],
        adv_cr=snap["adv_cr"],
        announced_at=snap["announced_at"],
    )
    es = _event_study_for(snap["yahoo_symbol"], snap["announced_at"])
    result = score(
        llm=llm,
        market_cap_cr=snap["market_cap_cr"],
        adv_cr=snap["adv_cr"],
        company_matched=snap["company_matched"],
        event_study=es,
        announced_at=snap["announced_at"],
    )

    # Phase 3: persist.
    with session_scope() as session:
        ann = session.get(RawAnnouncement, aid)
        if ann is None:
            return
        analysis = session.scalar(
            select(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id == aid)
        )
        if analysis is None:
            analysis = AnnouncementAnalysis(announcement_id=aid)
            session.add(analysis)
        analysis.company_id = ann.company_id
        analysis.event_type = result.event_type
        analysis.direction = result.direction
        analysis.sentiment = result.sentiment
        analysis.summary = llm.summary
        analysis.extracted = llm.extracted
        analysis.materiality_hint = llm.materiality_hint
        analysis.surprise_hint = llm.surprise_hint
        analysis.llm_confidence = llm.confidence
        analysis.is_routine = llm.is_routine
        analysis.analysis_schema_version = llm.schema_version
        analysis.factor_event_type = result.factor_event_type
        analysis.factor_materiality = result.factor_materiality
        analysis.factor_surprise = result.factor_surprise
        analysis.factor_sentiment = result.factor_sentiment
        analysis.factor_price_reaction = result.factor_price_reaction
        analysis.factor_liquidity = result.factor_liquidity
        analysis.factor_confidence = result.factor_confidence
        analysis.factor_time_decay = result.factor_time_decay
        analysis.composite_score = result.composite_score
        analysis.model_provider = llm.provider
        analysis.model_name = llm.model
        session.flush()

        if es is not None and es.alpha is not None:
            esr = session.scalar(
                select(EventStudyResult).where(EventStudyResult.announcement_id == aid)
            )
            if esr is None:
                esr = EventStudyResult(announcement_id=aid)
                session.add(esr)
            esr.analysis_id = analysis.id
            esr.alpha = es.alpha
            esr.beta = es.beta
            esr.ar_day0 = es.ar_day0
            esr.car_t1 = es.car_t1
            esr.car_t5 = es.car_t5
            esr.car_t20 = es.car_t20
            esr.abnormal_volume = es.abnormal_volume
            esr.t_stat = es.t_stat

        ann.analysis_status = AnalysisStatus.done

    _emit_analysis_done(aid)


def _emit_analysis_done(aid: int) -> None:
    try:
        with session_scope() as session:
            row = session.execute(
                select(RawAnnouncement, AnnouncementAnalysis, Company)
                .join(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
                .outerjoin(Company, Company.id == RawAnnouncement.company_id)
                .where(RawAnnouncement.id == aid)
            ).one_or_none()
            if row is None:
                return
            ann, analysis, company = row
            feed_events.publish(
                "analysis_done",
                feed_item_from_row(ann, analysis, company).model_dump(mode="json"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to emit analysis_done for %d", aid)


def _event_study_for(yahoo_symbol: str | None, announced_at: dt.datetime | None) -> EventStudyOutput | None:
    if not yahoo_symbol or announced_at is None:
        return None
    try:
        _ensure_prices(yahoo_symbol)
        _ensure_prices(settings.market_index)
        event_date = announced_at.date()
        lookback = int(settings.estimation_window_days * 1.7) + 60
        start = event_date - dt.timedelta(days=lookback)
        end = event_date + dt.timedelta(days=40)
        stock_df = yahoo.get_series(yahoo_symbol, start, end)
        market_df = yahoo.get_series(settings.market_index, start, end)
        return compute_event_study(stock_df, market_df, event_date, settings.estimation_window_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Event study failed for %s: %s", yahoo_symbol, exc)
        return None


def _ensure_prices(symbol: str) -> None:
    with session_scope() as session:
        latest = session.scalar(
            select(func.max(PriceDaily.date)).where(PriceDaily.yahoo_symbol == symbol)
        )
    if latest is None or latest < dt.date.today() - dt.timedelta(days=2):
        lookback = int(settings.estimation_window_days * 1.7) + 60
        yahoo.update_symbol(symbol, lookback_days=lookback)


def _mark_status(aid: int, status: AnalysisStatus) -> None:
    try:
        with session_scope() as session:
            ann = session.get(RawAnnouncement, aid)
            if ann is not None:
                ann.analysis_status = status
    except Exception:  # noqa: BLE001
        logger.exception("Failed to set status for %d", aid)
