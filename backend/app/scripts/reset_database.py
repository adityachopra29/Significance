"""Reset application data (companies + announcements + prices). Schema is preserved.

Usage:
    python -m app.scripts.reset_database
    python -m app.scripts.reset_database --yes   # skip confirmation prompt
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from app.db.base import engine, init_db


def reset_database() -> dict:
    init_db()
    tables = (
        "event_study_results",
        "announcement_analysis",
        "raw_announcements",
        "prices_daily",
        "companies",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"
            )
        )
    return {"truncated": list(tables)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Truncate all companies and announcement data")
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    args = parser.parse_args()
    if not args.yes:
        reply = input("Delete ALL companies, announcements, analysis, and prices? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return
    result = reset_database()
    print(f"Database reset: {result}")


if __name__ == "__main__":
    main()
