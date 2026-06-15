"""Load the ~500-company universe into the DB.

Strategy: take the Nifty 500 constituent list (reliable CSV from NSE archives:
symbol + ISIN + name + industry) and map each ISIN to its BSE scrip code via the
BSE equity master. This yields ~500 companies with BOTH a BSE scrip code (for
announcements) and an NSE symbol (for Yahoo .NS prices).

Usage:
    python -m app.scripts.load_universe
    python -m app.scripts.load_universe --enrich     # also fetch market caps
"""
from __future__ import annotations

import argparse
import csv
import io

import httpx
from sqlalchemy import select

from app.db.base import init_db, session_scope
from app.db.models import Company
from app.sources.bse import HEADERS
from app.sources.bse_master import get_master

NIFTY500_CSV = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


def _fetch_nifty500() -> list[dict]:
    r = httpx.get(NIFTY500_CSV, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def load_universe(enrich: bool = False) -> dict:
    init_db()
    rows = _fetch_nifty500()
    master = get_master()
    by_isin = master["by_isin"]

    matched, unmatched = 0, []
    with session_scope() as session:
        for row in rows:
            isin = (row.get("ISIN Code") or "").strip()
            symbol = (row.get("Symbol") or "").strip()
            name = (row.get("Company Name") or "").strip()
            sector = (row.get("Industry") or "").strip() or None

            rec = by_isin.get(isin)
            if rec is None:
                unmatched.append(symbol or name)
                continue
            scrip = rec["scrip_code"]

            company = session.scalar(select(Company).where(Company.bse_scrip_code == scrip))
            if company is None:
                company = Company(bse_scrip_code=scrip)
                session.add(company)
            company.nse_symbol = symbol or rec.get("symbol")
            company.yahoo_symbol = f"{symbol}.NS" if symbol else None
            company.isin = isin or rec.get("isin")
            company.name = name or rec["name"]
            company.sector = sector
            company.active = True
            matched += 1

    if enrich:
        from app.scripts.load_companies import _enrich_market_caps

        _enrich_market_caps()

    result = {"matched": matched, "unmatched": len(unmatched), "unmatched_symbols": unmatched[:20]}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args()
    res = load_universe(enrich=args.enrich)
    print(f"Universe loaded: matched={res['matched']} unmatched={res['unmatched']}")
    if res["unmatched_symbols"]:
        print("First few unmatched (no BSE ISIN match):", res["unmatched_symbols"])


if __name__ == "__main__":
    main()
