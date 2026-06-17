"""Smoke-test NSE corporate announcements fetch.

Usage:
    python -m app.scripts.test_nse_source
    python -m app.scripts.test_nse_source --days 3
    python -m app.scripts.test_nse_source --symbol TCS
"""
from __future__ import annotations

import argparse
import datetime as dt

from app.sources.nse import NSEAnnouncementsSource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    src = NSEAnnouncementsSource()
    if args.symbol:
        since = dt.datetime.now() - dt.timedelta(days=args.days)
        rows = src.fetch_symbol(args.symbol, since=since)
    else:
        since = dt.datetime.now() - dt.timedelta(days=args.days)
        rows = src.fetch(since=since)

    print(f"Fetched {len(rows)} NSE announcements")
    for dto in rows[:5]:
        print(f"  [{dto.nse_symbol}] {dto.announced_at} — {dto.headline[:80]}")


if __name__ == "__main__":
    main()
