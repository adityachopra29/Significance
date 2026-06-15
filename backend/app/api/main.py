"""FastAPI application: ranked announcement feed + detail + stats."""
from __future__ import annotations

import datetime as dt

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AddCompanyRequest,
    CompanyAdmin,
    CompanyOut,
    EventStudyOut,
    Factors,
    FeedItem,
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
    if c is None:
        return None
    return CompanyOut(
        id=c.id,
        name=c.name,
        bse_scrip_code=c.bse_scrip_code,
        nse_symbol=c.nse_symbol,
        sector=c.sector,
        market_cap_cr=c.market_cap_cr,
    )


@app.get("/api/feed", response_model=FeedResponse)
def feed(
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = 0,
    min_score: float = 0.0,
    event_type: str | None = None,
    sector: str | None = None,
    direction: str | None = None,
    company_id: int | None = None,
    days: int = Query(7, ge=1, le=90),
) -> FeedResponse:
    since = dt.datetime.now() - dt.timedelta(days=days)

    base = (
        select(RawAnnouncement, AnnouncementAnalysis, Company)
        .join(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
        .join(Company, Company.id == RawAnnouncement.company_id)
        .where(Company.active.is_(True))
        .where(AnnouncementAnalysis.composite_score >= min_score)
        .where(RawAnnouncement.announced_at >= since)
    )
    if event_type:
        base = base.where(AnnouncementAnalysis.event_type == event_type)
    if direction:
        base = base.where(AnnouncementAnalysis.direction == direction)
    if sector:
        base = base.where(Company.sector == sector)
    if company_id:
        base = base.where(Company.id == company_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = db.scalar(count_stmt) or 0

    rows = db.execute(
        base.order_by(
            AnnouncementAnalysis.composite_score.desc(),
            RawAnnouncement.announced_at.desc().nullslast(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        FeedItem(
            id=ann.id,
            headline=ann.headline,
            company=_company_out(company),
            bse_scrip_code=ann.bse_scrip_code,
            category=ann.category,
            event_type=analysis.event_type,
            direction=analysis.direction,
            sentiment=analysis.sentiment,
            summary=analysis.summary,
            composite_score=analysis.composite_score,
            announced_at=ann.announced_at,
            attachment_url=ann.attachment_url,
            model_provider=analysis.model_provider,
        )
        for ann, analysis, company in rows
    ]
    return FeedResponse(total=total, items=items)


@app.get("/api/announcements/{ann_id}", response_model=FeedItemDetail)
def announcement_detail(ann_id: int, db: Session = Depends(get_db)) -> FeedItemDetail:
    ann = db.get(RawAnnouncement, ann_id)
    if ann is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    analysis = db.scalar(
        select(AnnouncementAnalysis).where(AnnouncementAnalysis.announcement_id == ann_id)
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail="Announcement not yet analyzed")
    company = db.get(Company, ann.company_id) if ann.company_id else None
    esr = db.scalar(select(EventStudyResult).where(EventStudyResult.announcement_id == ann_id))

    event_study = None
    if esr is not None:
        event_study = EventStudyOut(
            alpha=esr.alpha, beta=esr.beta, ar_day0=esr.ar_day0,
            car_t1=esr.car_t1, car_t5=esr.car_t5, car_t20=esr.car_t20,
            abnormal_volume=esr.abnormal_volume, t_stat=esr.t_stat,
        )

    return FeedItemDetail(
        id=ann.id,
        headline=ann.headline,
        company=_company_out(company),
        bse_scrip_code=ann.bse_scrip_code,
        category=ann.category,
        event_type=analysis.event_type,
        direction=analysis.direction,
        sentiment=analysis.sentiment,
        summary=analysis.summary,
        composite_score=analysis.composite_score,
        announced_at=ann.announced_at,
        attachment_url=ann.attachment_url,
        model_provider=analysis.model_provider,
        factors=Factors(
            event_type=analysis.factor_event_type,
            materiality=analysis.factor_materiality,
            surprise=analysis.factor_surprise,
            sentiment=analysis.factor_sentiment,
            price_reaction=analysis.factor_price_reaction,
            liquidity=analysis.factor_liquidity,
            confidence=analysis.factor_confidence,
            time_decay=analysis.factor_time_decay,
        ),
        extracted=analysis.extracted,
        event_study=event_study,
    )


@app.get("/api/companies", response_model=list[CompanyAdmin])
def list_companies(
    db: Session = Depends(get_db),
    active_only: bool = True,
    q: str | None = None,
) -> list[CompanyAdmin]:
    stmt = select(Company)
    if active_only:
        stmt = stmt.where(Company.active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Company.name).like(like) | func.lower(Company.nse_symbol).like(like)
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

    try:
        rec = bse_master.lookup(scrip_code=payload.scrip_code, symbol=payload.nse_symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"BSE master lookup failed: {exc}") from exc
    if rec is None:
        raise HTTPException(status_code=404, detail="Stock not found on BSE (check scrip code / symbol)")

    scrip = rec["scrip_code"]
    symbol = payload.nse_symbol or rec.get("symbol")
    company = db.scalar(select(Company).where(Company.bse_scrip_code == scrip))
    if company is None:
        company = Company(bse_scrip_code=scrip)
        db.add(company)
    company.name = rec["name"]
    company.isin = rec.get("isin")
    company.nse_symbol = symbol
    company.yahoo_symbol = f"{symbol}.NS" if symbol else None
    company.sector = company.sector or rec.get("industry")
    company.active = True
    db.commit()
    db.refresh(company)

    # Per-scrip backfill: only inserts filings not already in DB (analysis cache preserved).
    try:
        backfill = backfill_company(company.id, days=payload.backfill_days)
    except Exception:  # noqa: BLE001
        backfill = None

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
    return StatsResponse(
        companies=db.scalar(
            select(func.count()).select_from(Company).where(Company.active.is_(True))
        )
        or 0,
        announcements_total=db.scalar(select(func.count()).select_from(RawAnnouncement)) or 0,
        analyzed=db.scalar(select(func.count()).select_from(AnnouncementAnalysis)) or 0,
        pending=db.scalar(
            select(func.count())
            .select_from(RawAnnouncement)
            .where(RawAnnouncement.analysis_status == AnalysisStatus.pending)
        )
        or 0,
        last_announcement_at=db.scalar(select(func.max(RawAnnouncement.announced_at))),
    )


@app.get("/api/event-types")
def event_types(db: Session = Depends(get_db)) -> list[str]:
    rows = db.scalars(
        select(AnnouncementAnalysis.event_type)
        .distinct()
        .where(AnnouncementAnalysis.event_type.isnot(None))
    ).all()
    return sorted(r for r in rows if r)
