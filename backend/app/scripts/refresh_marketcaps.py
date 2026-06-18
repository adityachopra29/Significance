"""Refresh market caps for the monitored universe (BSE → NSE → Yahoo).

Usage:
    python -m app.scripts.refresh_marketcaps              # refresh all active
    python -m app.scripts.refresh_marketcaps --missing    # only those without a cap
"""
from __future__ import annotations

import argparse

from app.db.base import init_db
from app.fundamentals import marketcap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--missing", action="store_true", help="only companies missing market cap")
    args = parser.parse_args()

    init_db()
    result = marketcap.refresh_all(only_missing=args.missing)
    print(f"Market caps: {result['updated']}/{result['companies']} updated")


if __name__ == "__main__":
    main()
