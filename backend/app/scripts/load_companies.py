"""Load the BSE 500 company master from a CSV into the database.

Usage:
    python -m app.scripts.load_companies                       # load seed CSV
    python -m app.scripts.load_companies --csv path/to.csv     # load custom CSV
    python -m app.scripts.load_companies --enrich              # also fetch market cap via Yahoo

CSV columns required: bse_scrip_code, nse_symbol, name, sector
"""
from __future__ import annotations

import argparse
import csv
import os

from sqlalchemy import select

from app.db.base import init_db, session_scope
from app.db.models import Company

SEED_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "bse500_seed.csv")


def load_csv(path: str, enrich: bool = False) -> int:
    init_db()
    count = 0
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    with session_scope() as session:
        for row in rows:
            scrip = (row.get("bse_scrip_code") or "").strip()
            if not scrip:
                continue
            nse_symbol = (row.get("nse_symbol") or "").strip() or None
            yahoo_symbol = f"{nse_symbol}.NS" if nse_symbol else None

            company = session.scalar(
                select(Company).where(Company.bse_scrip_code == scrip)
            )
            if company is None:
                company = Company(bse_scrip_code=scrip)
                session.add(company)

            company.nse_symbol = nse_symbol
            company.yahoo_symbol = yahoo_symbol
            company.name = (row.get("name") or "").strip()
            company.sector = (row.get("sector") or "").strip() or None
            company.ingest_enabled = True
            count += 1

    if enrich:
        _enrich_market_caps()
    return count


def _enrich_market_caps() -> None:
    """Best-effort market-cap enrichment via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed; skipping enrichment.")
        return

    with session_scope() as session:
        companies = list(session.scalars(select(Company).where(Company.yahoo_symbol.isnot(None))))
        for company in companies:
            try:
                info = yf.Ticker(company.yahoo_symbol).fast_info
                mcap = getattr(info, "market_cap", None)
                if mcap:
                    company.market_cap_cr = float(mcap) / 1e7  # rupees -> crore
            except Exception as exc:  # noqa: BLE001
                print(f"  enrich failed for {company.yahoo_symbol}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=SEED_CSV)
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args()

    n = load_csv(args.csv, enrich=args.enrich)
    print(f"Loaded/updated {n} companies from {args.csv}")


if __name__ == "__main__":
    main()
