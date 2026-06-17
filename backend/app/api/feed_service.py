"""Feed query logic for Live and Ranked views."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.feed_helpers import feed_item_from_row
from app.api.schemas import FeedItem, FeedResponse
from app.config import settings
from app.db.models import AnalysisStatus, AnnouncementAnalysis, Company, RawAnnouncement


def _clamp_days(days: int) -> int:
    return min(days, settings.feed_max_days)


def query_feed(
    db: Session,
    *,
    view: str,
    sort_by: str,
    days: int,
    limit: int,
    offset: int,
    min_score: float = 0.0,
    event_type: str | None = None,
    direction: str | None = None,
    sector: str | None = None,
    company_id: int | None = None,
) -> FeedResponse:
    days = _clamp_days(days)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

    if view == "live":
        return _live_feed(
            db,
            since=since,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            event_type=event_type,
            sector=sector,
            company_id=company_id,
        )
    return _ranked_feed(
        db,
        since=since,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
        min_score=min_score,
        event_type=event_type,
        direction=direction,
        sector=sector,
        company_id=company_id,
    )


def _live_feed(
    db: Session,
    *,
    since: dt.datetime,
    sort_by: str,
    limit: int,
    offset: int,
    event_type: str | None,
    sector: str | None,
    company_id: int | None,
) -> FeedResponse:
    if sort_by not in ("category", "recency"):
        sort_by = "category"

    base = (
        select(RawAnnouncement, AnnouncementAnalysis, Company)
        .outerjoin(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
        .outerjoin(Company, Company.id == RawAnnouncement.company_id)
        .where(RawAnnouncement.triage_passed.is_(True))
        .where(RawAnnouncement.announced_at >= since)
    )
    if event_type:
        base = base.where(RawAnnouncement.triage_event_type == event_type)
    if sector:
        base = base.where(Company.sector == sector)
    if company_id:
        base = base.where(Company.id == company_id)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    if sort_by == "recency":
        order = (RawAnnouncement.announced_at.desc().nullslast(),)
    else:
        order = (
            RawAnnouncement.category_rank.asc().nullslast(),
            RawAnnouncement.announced_at.desc().nullslast(),
        )

    rows = db.execute(base.order_by(*order).limit(limit).offset(offset)).all()
    items = [feed_item_from_row(ann, analysis, company) for ann, analysis, company in rows]
    return FeedResponse(total=total, items=items, limit=limit, offset=offset, view="live")


def _ranked_feed(
    db: Session,
    *,
    since: dt.datetime,
    sort_by: str,
    limit: int,
    offset: int,
    min_score: float,
    event_type: str | None,
    direction: str | None,
    sector: str | None,
    company_id: int | None,
) -> FeedResponse:
    if sort_by not in ("score", "recency"):
        sort_by = "score"

    base = (
        select(RawAnnouncement, AnnouncementAnalysis, Company)
        .join(AnnouncementAnalysis, AnnouncementAnalysis.announcement_id == RawAnnouncement.id)
        .outerjoin(Company, Company.id == RawAnnouncement.company_id)
        .where(RawAnnouncement.analysis_status == AnalysisStatus.done)
        .where(AnnouncementAnalysis.composite_score.isnot(None))
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

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    if sort_by == "recency":
        order = (RawAnnouncement.announced_at.desc().nullslast(),)
    else:
        order = (
            AnnouncementAnalysis.composite_score.desc(),
            RawAnnouncement.announced_at.desc().nullslast(),
        )

    rows = db.execute(base.order_by(*order).limit(limit).offset(offset)).all()
    items = [feed_item_from_row(ann, analysis, company) for ann, analysis, company in rows]
    return FeedResponse(total=total, items=items, limit=limit, offset=offset, view="ranked")
