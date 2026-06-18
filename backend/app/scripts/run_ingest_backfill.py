"""Run a audited announcement backfill for the monitored universe.

Usage:
    python -m app.scripts.run_ingest_backfill --days 7
"""
from __future__ import annotations

import argparse
import json

from app.db.base import init_db
from app.ingestion.ingest import run_backfill


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    init_db()
    stats = run_backfill(days=args.days)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
