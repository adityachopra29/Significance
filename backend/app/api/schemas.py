"""Pydantic response schemas for the API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class CompanyOut(BaseModel):
    id: int
    name: str
    bse_scrip_code: str
    nse_symbol: str | None = None
    sector: str | None = None
    market_cap_cr: float | None = None
    adv_cr: float | None = None
    chart_url: str | None = None


class CompanyAdmin(BaseModel):
    id: int
    name: str
    bse_scrip_code: str
    nse_symbol: str | None = None
    sector: str | None = None
    market_cap_cr: float | None = None
    adv_cr: float | None = None
    active: bool = True
    announcement_count: int = 0
    analyzed_count: int = 0
    backfill_new: int | None = None  # set on add: filings fetched for the first time
    backfill_cached: int | None = None  # set on add: filings already stored (not re-analyzed)


class AddCompanyRequest(BaseModel):
    scrip_code: str | None = None
    nse_symbol: str | None = None
    backfill_days: int | None = None


class Factors(BaseModel):
    event_type: float | None = None
    materiality: float | None = None
    surprise: float | None = None
    sentiment: float | None = None
    price_reaction: float | None = None
    liquidity: float | None = None
    confidence: float | None = None
    time_decay: float | None = None


class EventStudyOut(BaseModel):
    alpha: float | None = None
    beta: float | None = None
    ar_day0: float | None = None
    car_t1: float | None = None
    car_t5: float | None = None
    car_t20: float | None = None
    abnormal_volume: float | None = None
    t_stat: float | None = None


class FeedItem(BaseModel):
    id: int
    headline: str
    company: CompanyOut | None = None
    bse_scrip_code: str | None = None
    category: str | None = None
    subcategory: str | None = None
    analysis_status: str | None = None
    triage_event_type: str | None = None
    triage_tier: str | None = None
    category_rank: int | None = None
    event_type: str | None = None
    direction: str | None = None
    sentiment: float | None = None
    summary: str | None = None
    composite_score: float | None = None
    announced_at: dt.datetime | None = None
    attachment_url: str | None = None
    model_provider: str | None = None


class FeedItemDetail(FeedItem):
    factors: Factors | None = None
    extracted: dict | None = None
    event_study: EventStudyOut | None = None
    analysis_schema_version: str | None = None
    materiality_hint: float | None = None
    surprise_hint: float | None = None
    llm_confidence: float | None = None
    is_routine: bool = False


class FeedResponse(BaseModel):
    total: int
    items: list[FeedItem]
    limit: int = 50
    offset: int = 0
    view: str = "live"


class StatsResponse(BaseModel):
    universe_companies: int = 0
    watchlist_companies: int = 0
    companies: int = 0  # alias for universe_companies
    announcements_total: int
    triage_passed: int = 0
    analyzed: int
    pending: int
    skipped: int = 0
    errors: int = 0
    llm_configured: bool = True
    llm_provider: str | None = None
    llm_error: str | None = None
    last_announcement_at: dt.datetime | None = None
