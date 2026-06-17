"""Feed row → API schema helpers."""
from __future__ import annotations

from app.api.schemas import CompanyOut, FeedItem
from app.db.models import AnnouncementAnalysis, Company, RawAnnouncement


def chart_url(c: Company | None) -> str | None:
    if c is None:
        return None
    if c.yahoo_symbol:
        return f"https://finance.yahoo.com/chart/{c.yahoo_symbol}"
    if c.nse_symbol:
        return f"https://finance.yahoo.com/chart/{c.nse_symbol}.NS"
    if c.bse_scrip_code:
        return f"https://finance.yahoo.com/chart/{c.bse_scrip_code}.BO"
    return None


def company_out(c: Company | None) -> CompanyOut | None:
    if c is None:
        return None
    return CompanyOut(
        id=c.id,
        name=c.name,
        bse_scrip_code=c.bse_scrip_code,
        nse_symbol=c.nse_symbol,
        sector=c.sector,
        market_cap_cr=c.market_cap_cr,
        adv_cr=c.adv_cr,
        chart_url=chart_url(c),
    )


def feed_item_from_row(
    ann: RawAnnouncement,
    analysis: AnnouncementAnalysis | None,
    company: Company | None,
) -> FeedItem:
    return FeedItem(
        id=ann.id,
        headline=ann.headline,
        company=company_out(company),
        bse_scrip_code=ann.bse_scrip_code,
        category=ann.category,
        subcategory=ann.subcategory,
        analysis_status=ann.analysis_status.value if ann.analysis_status else None,
        triage_event_type=ann.triage_event_type,
        triage_tier=ann.triage_tier,
        category_rank=ann.category_rank,
        event_type=analysis.event_type if analysis else ann.triage_event_type,
        direction=analysis.direction if analysis else None,
        sentiment=analysis.sentiment if analysis else None,
        summary=analysis.summary if analysis else None,
        composite_score=analysis.composite_score if analysis else None,
        announced_at=ann.announced_at,
        attachment_url=ann.attachment_url,
        model_provider=analysis.model_provider if analysis else None,
    )
