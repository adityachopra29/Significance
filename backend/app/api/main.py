"""FastAPI application: ranked announcement feed + detail + stats."""
from __future__ import annotations

import asyncio
import datetime as dt
import queue

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api import events as feed_events
from app.api.feed_helpers import chart_url, company_out, feed_item_from_row
from app.api.feed_service import query_feed
from app.api.schemas import (
    AddCompanyRequest,
    CompanyAdmin,
    CompanyOut,
    EventStudyOut,
    Factors,
    FeedItemDetail,
    FeedResponse,
    StatsResponse,
)
from app.config import settings
from app.db.base import get_db, init_db
from app.db.models import (
    AnalysisStatus,
    AnnouncementAnalysis,
    Company,
    EventStudyResult,
    RawAnnouncement,
)
from app.analysis.llm.factory import llm_status
from app.fundamentals import marketcap
from app.ingestion.ingest import backfill_company
from app.sources import bse_master

app = FastAPI(title="Announcement Intelligence Engine", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _company_out(c: Company | None) -> CompanyOut | None:
    return company_out(c)


def _chart_url(c: Company | None) -> str | None:
    return chart_url(c)


@app.get("/api/feed", response_model=FeedResponse)
def feed(
    db: Session = Depends(get_db),
    view: str = Query("live", pattern="^(live|ranked)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    min_score: float = 0.0,
    event_type: str | None = None,
    sector: str | None = None,
    direction: str | None = None,
    company_id: int | None = None,
    sort_by: str = Query("category", pattern="^(category|score|recency)$"),
    days: int = Query(7, ge=1, le=90),
) -> FeedResponse:
    if view == "live" and sort_by == "score":
        sort_by = "category"
    if view == "ranked" and sort_by == "category":
        sort_by = "score"
    return query_feed(
        db,
        view=view,
        sort_by=sort_by,
        days=days,
        limit=limit,
        offset=offset,
        min_score=min_score,
        event_type=event_type,
        direction=direction,
        sector=sector,
        company_id=company_id,
    )


@app.get("/api/feed/events")
async def stream_feed_events() -> StreamingResponse:
    """SSE stream: announcement_triaged, analysis_started, analysis_done."""

    async def stream():
        q = feed_events.register()
        try:
            while True:
                try:
                    msg = await asyncio.to_thread(q.get, True, 25)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            feed_events.unregister(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/announcements/{ann_id}", response_model=FeedItemDetail)
def announcement_detail(ann_id: int, db: Session = Depends(get_db)) -> FeedItemDetail:
    row = db.execute(
        select(RawAnnouncement, AnnouncementAnalysis, Company)
        .outerjoin(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
        .outerjoin(Company, Company.id == RawAnnouncement.company_id)
        .where(RawAnnouncement.id == ann_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    ann, analysis, company = row

    if not ann.triage_passed and ann.analysis_status == AnalysisStatus.skipped:
        raise HTTPException(status_code=404, detail="Announcement not in feed")

    esr = db.scalar(select(EventStudyResult).where(EventStudyResult.announcement_id == ann_id))
    event_study = None
    if esr is not None:
        event_study = EventStudyOut(
            alpha=esr.alpha, beta=esr.beta, ar_day0=esr.ar_day0,
            car_t1=esr.car_t1, car_t5=esr.car_t5, car_t20=esr.car_t20,
            abnormal_volume=esr.abnormal_volume, t_stat=esr.t_stat,
        )

    base = feed_item_from_row(ann, analysis, company)
    factors = None
    if analysis is not None and analysis.composite_score is not None:
        factors = Factors(
            event_type=analysis.factor_event_type,
            materiality=analysis.factor_materiality,
            surprise=analysis.factor_surprise,
            sentiment=analysis.factor_sentiment,
            price_reaction=analysis.factor_price_reaction,
            liquidity=analysis.factor_liquidity,
            confidence=analysis.factor_confidence,
            time_decay=analysis.factor_time_decay,
        )

    return FeedItemDetail(
        **base.model_dump(),
        factors=factors,
        extracted=analysis.extracted if analysis else None,
        event_study=event_study,
        analysis_schema_version=analysis.analysis_schema_version if analysis else None,
        materiality_hint=analysis.materiality_hint if analysis else None,
        surprise_hint=analysis.surprise_hint if analysis else None,
        llm_confidence=analysis.llm_confidence if analysis else None,
        is_routine=analysis.is_routine if analysis else False,
    )


@app.get("/api/companies", response_model=list[CompanyAdmin])
def list_companies(
    db: Session = Depends(get_db),
    active_only: bool = False,
    ingest_only: bool = True,
    q: str | None = None,
) -> list[CompanyAdmin]:
    stmt = select(Company)
    if active_only:
        stmt = stmt.where(Company.active.is_(True))
    elif ingest_only:
        stmt = stmt.where(Company.ingest_enabled.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Company.name).like(like)
            | func.lower(Company.nse_symbol).like(like)
            | Company.bse_scrip_code.like(like)
        )
    companies = list(db.scalars(stmt.order_by(Company.name)))

    total_counts = dict(
        db.execute(
            select(RawAnnouncement.company_id, func.count())
            .group_by(RawAnnouncement.company_id)
        ).all()
    )
    analyzed_counts = dict(
        db.execute(
            select(AnnouncementAnalysis.company_id, func.count())
            .group_by(AnnouncementAnalysis.company_id)
        ).all()
    )
    return [
        CompanyAdmin(
            id=c.id,
            name=c.name,
            bse_scrip_code=c.bse_scrip_code,
            nse_symbol=c.nse_symbol,
            sector=c.sector,
            market_cap_cr=c.market_cap_cr,
            adv_cr=c.adv_cr,
            active=c.active,
            announcement_count=int(total_counts.get(c.id, 0)),
            analyzed_count=int(analyzed_counts.get(c.id, 0)),
        )
        for c in companies
    ]


@app.post("/api/companies", response_model=CompanyAdmin)
def add_company(payload: AddCompanyRequest, db: Session = Depends(get_db)) -> CompanyAdmin:
    if not payload.scrip_code and not payload.nse_symbol:
        raise HTTPException(status_code=400, detail="Provide scrip_code or nse_symbol")

    rec = None
    if payload.scrip_code or payload.nse_symbol:
        try:
            rec = bse_master.lookup(scrip_code=payload.scrip_code, symbol=payload.nse_symbol)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"BSE master lookup failed: {exc}") from exc

    if rec is None and payload.nse_symbol:
        from app.scripts.load_universe import lookup_nse_symbol

        nse_rec = lookup_nse_symbol(payload.nse_symbol)
        if nse_rec is None:
            raise HTTPException(status_code=404, detail="Stock not found on NSE EQUITY_L")
        symbol = nse_rec["symbol"]
        company = db.scalar(select(Company).where(Company.nse_symbol == symbol))
        if company is None:
            company = Company(nse_symbol=symbol, name=nse_rec["name"])
            db.add(company)
        company.name = nse_rec["name"]
        company.isin = nse_rec.get("isin")
        company.nse_symbol = symbol
        company.yahoo_symbol = f"{symbol}.NS"
        company.ingest_enabled = True
        company.active = True
    elif rec is None:
        raise HTTPException(status_code=404, detail="Stock not found on BSE (check scrip code / symbol)")
    else:
        scrip = rec["scrip_code"]
        symbol = payload.nse_symbol or rec.get("symbol")
        company = db.scalar(select(Company).where(Company.bse_scrip_code == scrip))
        if company is None and symbol:
            company = db.scalar(select(Company).where(Company.nse_symbol == symbol))
        if company is None:
            company = Company(bse_scrip_code=scrip)
            db.add(company)
        company.name = rec["name"]
        company.bse_scrip_code = scrip
        company.isin = rec.get("isin")
        company.nse_symbol = symbol
        company.yahoo_symbol = f"{symbol}.NS" if symbol else None
        company.sector = company.sector or rec.get("industry")
        company.ingest_enabled = True
        company.active = True

    db.commit()
    db.refresh(company)

    # Per-scrip backfill: only inserts filings not already in DB (analysis cache preserved).
    try:
        backfill = backfill_company(company.id, days=payload.backfill_days)
    except Exception:  # noqa: BLE001
        backfill = None

    # Best-effort market-cap fetch so materiality/liquidity work immediately.
    try:
        marketcap.refresh_company(company.id)
        db.refresh(company)
    except Exception:  # noqa: BLE001
        pass

    ann_count = (
        db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.company_id == company.id)
        )
        or 0
    )
    analyzed_count = (
        db.scalar(
            select(func.count())
            .select_from(AnnouncementAnalysis)
            .where(AnnouncementAnalysis.company_id == company.id)
        )
        or 0
    )

    return CompanyAdmin(
        id=company.id,
        name=company.name,
        bse_scrip_code=company.bse_scrip_code,
        nse_symbol=company.nse_symbol,
        sector=company.sector,
        market_cap_cr=company.market_cap_cr,
        adv_cr=company.adv_cr,
        active=company.active,
        announcement_count=ann_count,
        analyzed_count=analyzed_count,
        backfill_new=backfill.inserted if backfill else 0,
        backfill_cached=backfill.skipped if backfill else 0,
    )


@app.delete("/api/companies/{company_id}")
def delete_company(
    company_id: int, db: Session = Depends(get_db), purge: bool = False
) -> dict:
    """Stop monitoring a company.

    Default (purge=false): soft-delete only — announcements and their analysis
    stay in the DB so re-adding the stock reuses cached results (no LLM cost).

    purge=true deletes stored filings/analysis (forces re-fetch and re-analysis).
    """
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    company.active = False
    if purge:
        ann_ids = list(
            db.scalars(select(RawAnnouncement.id).where(RawAnnouncement.company_id == company_id))
        )
        if ann_ids:
            db.execute(delete(EventStudyResult).where(EventStudyResult.announcement_id.in_(ann_ids)))
            db.execute(
                delete(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id.in_(ann_ids))
            )
            db.execute(delete(RawAnnouncement).where(RawAnnouncement.id.in_(ann_ids)))
    db.commit()
    return {"status": "ok", "company_id": company_id, "active": company.active, "purged": purge}


@app.get("/api/stats", response_model=StatsResponse)
def stats(db: Session = Depends(get_db)) -> StatsResponse:
    llm = llm_status()
    universe = db.scalar(
        select(func.count()).select_from(Company).where(Company.ingest_enabled.is_(True))
    ) or 0
    watchlist = db.scalar(
        select(func.count()).select_from(Company).where(Company.active.is_(True))
    ) or 0
    return StatsResponse(
        universe_companies=universe,
        watchlist_companies=watchlist,
        companies=universe,
        announcements_total=db.scalar(select(func.count()).select_from(RawAnnouncement)) or 0,
        triage_passed=db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.triage_passed.is_(True))
        )
        or 0,
        analyzed=db.scalar(select(func.count()).select_from(AnnouncementAnalysis)) or 0,
        pending=db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.analysis_status == AnalysisStatus.pending)
        )
        or 0,
        skipped=db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.analysis_status == AnalysisStatus.skipped)
        )
        or 0,
        errors=db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.analysis_status == AnalysisStatus.error)
        )
        or 0,
        llm_configured=bool(llm["configured"]),
        llm_provider=llm["provider"],
        llm_error=llm["error"],
        last_announcement_at=db.scalar(select(func.max(RawAnnouncement.announced_at))),
    )


@app.get("/api/event-types")
def event_types(
    db: Session = Depends(get_db),
    view: str = Query("ranked", pattern="^(live|ranked)$"),
) -> list[str]:
    if view == "live":
        rows = db.scalars(
            select(RawAnnouncement.triage_event_type)
            .distinct()
            .where(RawAnnouncement.triage_passed.is_(True))
            .where(RawAnnouncement.triage_event_type.isnot(None))
        ).all()
    else:
        rows = db.scalars(
            select(AnnouncementAnalysis.event_type)
            .distinct()
            .where(AnnouncementAnalysis.event_type.isnot(None))
        ).all()
    return sorted(r for r in rows if r)
