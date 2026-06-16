"""Database models for the announcement intelligence engine."""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AnalysisStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    error = "error"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bse_scrip_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    nse_symbol: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    yahoo_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(256))
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_cap_cr: Mapped[float | None] = mapped_column(Float, nullable=True)  # in INR crore
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)  # absolute share count
    market_cap_asof: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    free_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    adv_cr: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg daily traded value, crore
    fno_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    announcements: Mapped[list[RawAnnouncement]] = relationship(back_populates="company")


class RawAnnouncement(Base):
    __tablename__ = "raw_announcements"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_raw_content_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)  # e.g. "bse"
    external_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    bse_scrip_code: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)

    headline: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # extracted PDF body
    attachment_fetched: Mapped[bool] = mapped_column(Boolean, default=False)  # PDF fetch attempted

    announced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus, name="analysis_status"),
        default=AnalysisStatus.pending,
        index=True,
    )
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    company: Mapped[Company | None] = relationship(back_populates="announcements")
    analysis: Mapped[AnnouncementAnalysis | None] = relationship(
        back_populates="announcement", uselist=False
    )


class PriceDaily(Base):
    __tablename__ = "prices_daily"
    __table_args__ = (UniqueConstraint("yahoo_symbol", "date", name="uq_price_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yahoo_symbol: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)


class AnnouncementAnalysis(Base):
    __tablename__ = "announcement_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("raw_announcements.id"), unique=True, index=True
    )
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)

    event_type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)  # bullish/bearish/neutral
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..1
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Factor sub-scores (0..1 unless noted)
    factor_event_type: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_materiality: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_surprise: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_price_reaction: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_time_decay: Mapped[float | None] = mapped_column(Float, nullable=True)

    composite_score: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)  # 0..100

    model_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyzed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    announcement: Mapped[RawAnnouncement] = relationship(back_populates="analysis")
    event_study: Mapped[EventStudyResult | None] = relationship(
        back_populates="analysis", uselist=False
    )


class EventStudyResult(Base):
    __tablename__ = "event_study_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("raw_announcements.id"), unique=True, index=True
    )
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("announcement_analysis.id"), nullable=True
    )

    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    ar_day0: Mapped[float | None] = mapped_column(Float, nullable=True)
    car_t1: Mapped[float | None] = mapped_column(Float, nullable=True)
    car_t5: Mapped[float | None] = mapped_column(Float, nullable=True)
    car_t20: Mapped[float | None] = mapped_column(Float, nullable=True)
    abnormal_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_stat: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis: Mapped[AnnouncementAnalysis | None] = relationship(back_populates="event_study")
