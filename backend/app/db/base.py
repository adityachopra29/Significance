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
]


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, coltype in _COLUMN_MIGRATIONS:
            conn.execute(
                text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}')
            )


def init_db() -> None:
    """Create all tables, then apply idempotent column migrations."""
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    try:
        _run_column_migrations()
    except Exception:  # noqa: BLE001
        logger.exception("Column migration failed")
