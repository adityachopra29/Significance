"""SQLAlchemy engine, session factory, and declarative base."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context manager."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# Idempotent column additions for tables that predate a model change.
# (create_all only creates missing tables, never alters existing ones.)
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("raw_announcements", "attachment_text", "TEXT"),
    ("raw_announcements", "attachment_fetched", "BOOLEAN DEFAULT FALSE"),
    ("companies", "shares_outstanding", "DOUBLE PRECISION"),
    ("companies", "market_cap_asof", "DATE"),
    ("announcement_analysis", "materiality_hint", "DOUBLE PRECISION"),
    ("announcement_analysis", "surprise_hint", "DOUBLE PRECISION"),
    ("announcement_analysis", "llm_confidence", "DOUBLE PRECISION"),
    ("announcement_analysis", "is_routine", "BOOLEAN DEFAULT FALSE"),
    ("announcement_analysis", "analysis_schema_version", "VARCHAR(16)"),
    ("companies", "ingest_enabled", "BOOLEAN DEFAULT TRUE"),
    ("raw_announcements", "triage_passed", "BOOLEAN DEFAULT FALSE"),
    ("raw_announcements", "triage_event_type", "VARCHAR(64)"),
    ("raw_announcements", "triage_tier", "VARCHAR(4)"),
    ("raw_announcements", "triage_priority", "INTEGER"),
    ("raw_announcements", "category_rank", "INTEGER"),
    ("raw_announcements", "skip_reason", "VARCHAR(64)"),
    ("raw_announcements", "triage_reason", "VARCHAR(64)"),
    ("raw_announcements", "nse_symbol", "VARCHAR(32)"),
    ("raw_announcements", "exchange_dedup_hash", "VARCHAR(64)"),
]


def _run_enum_migrations() -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TYPE analysis_status ADD VALUE IF NOT EXISTS 'skipped'"))


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, coltype in _COLUMN_MIGRATIONS:
            conn.execute(
                text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}')
            )
        conn.execute(text("ALTER TABLE companies ALTER COLUMN bse_scrip_code DROP NOT NULL"))


def _backfill_triage_defaults() -> None:
    """Existing rows pre-triage: treat analyzed/pending as triage-passed."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE raw_announcements
                SET triage_passed = TRUE,
                    triage_event_type = COALESCE(triage_event_type, 'other'),
                    triage_tier = COALESCE(triage_tier, 'E'),
                    triage_priority = COALESCE(triage_priority, 300),
                    category_rank = COALESCE(category_rank, 7000)
                WHERE triage_passed = FALSE
                  AND triage_event_type IS NULL
                  AND analysis_status IN ('pending', 'processing', 'done', 'error')
                """
            )
        )


def init_db() -> None:
    """Create all tables, then apply idempotent column migrations."""
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    try:
        _run_column_migrations()
        _run_enum_migrations()
        _backfill_triage_defaults()
    except Exception:  # noqa: BLE001
        logger.exception("Column migration failed")
