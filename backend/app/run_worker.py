"""Background runner: polls BSE for new announcements and analyzes pending ones.

Run as a separate process from the API:
    python -m app.run_worker
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from sqlalchemy import func, select

from app.analysis.worker import process_pending
from app.config import settings
from app.db.base import init_db, session_scope
from app.db.models import Company, RawAnnouncement
from app.fundamentals import marketcap
from app.ingestion.ingest import backfill_universe, run_ingestion
from app.ingestion.retention import run_retention

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("run_worker")


def poll_job() -> None:
    try:
        stats = run_ingestion()
        logger.info("Poll done: %s", stats)
    except Exception:  # noqa: BLE001
        logger.exception("Poll job failed")


def analyze_job() -> None:
    try:
        n = process_pending()
        if n:
            logger.info("Analyzed %d announcements", n)
    except Exception:  # noqa: BLE001
        logger.exception("Analyze job failed")


def retention_job() -> None:
    try:
        result = run_retention()
        logger.info("Retention: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("Retention job failed")


def marketcap_job() -> None:
    try:
        result = marketcap.refresh_all()
        logger.info("Market cap refresh: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("Market cap job failed")


def _is_empty() -> bool:
    with session_scope() as session:
        return (session.scalar(select(func.count()).select_from(RawAnnouncement)) or 0) == 0


def _missing_market_caps() -> int:
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(Company)
                .where(Company.ingest_enabled.is_(True))
                .where(Company.market_cap_cr.is_(None))
            )
            or 0
        )


def main() -> None:
    init_db()
    logger.info("DB initialized. Starting scheduler (poll every %ds).", settings.poll_interval_seconds)

    # On a fresh DB, optionally backfill announcement history across the universe.
    if settings.ingest_on_startup and _is_empty():
        logger.info("Empty DB: running %d-day universe backfill...", settings.backfill_days)
        try:
            logger.info("Backfill done: %s", backfill_universe())
        except Exception:  # noqa: BLE001
            logger.exception("Universe backfill failed")

    # Populate market caps on first run if missing (needed for materiality/liquidity).
    if _missing_market_caps():
        logger.info("Refreshing market caps (missing on %d companies)...", _missing_market_caps())
        marketcap_job()

    if settings.ingest_on_startup:
        poll_job()
        analyze_job()
    else:
        logger.info("INGEST_ON_STARTUP=false — skipping poll/backfill/analyze until enabled.")

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    if settings.ingest_on_startup:
        scheduler.add_job(poll_job, "interval", seconds=settings.poll_interval_seconds, id="poll", max_instances=1)
        scheduler.add_job(analyze_job, "interval", seconds=20, id="analyze", max_instances=1)
    else:
        logger.info("INGEST_ON_STARTUP=false — poll/analyze jobs not scheduled.")
    scheduler.add_job(retention_job, "cron", hour=2, minute=30, id="retention", max_instances=1)
    # Daily market-cap refresh (after market close).
    scheduler.add_job(marketcap_job, "cron", hour=18, minute=30, id="marketcap", max_instances=1)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
