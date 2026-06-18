"""Compare DB universe against live BSE equity master and NSE EQUITY_L.

Usage:
    python -m app.scripts.verify_universe
    python -m app.scripts.verify_universe --show-missing 20
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from app.db.base import init_db, session_scope
from app.db.models import Company
from app.scripts.load_universe import _fetch_nse_equity
from app.sources.bse_master import get_master


def verify(*, show_missing: int) -> dict:
    init_db()
    master = get_master(force=True)
    nse_by_isin, nse_by_symbol = _fetch_nse_equity()

    bse_online = set(master["by_scrip"].keys())
    bse_isins_online = set(master["by_isin"].keys())
    nse_online = set(nse_by_symbol.keys())
    nse_isins_online = set(nse_by_isin.keys())

    with session_scope() as session:
        companies = session.scalars(select(Company)).all()

    db_bse = {c.bse_scrip_code for c in companies if c.bse_scrip_code}
    db_nse = {c.nse_symbol.upper() for c in companies if c.nse_symbol}
    db_isins = {c.isin for c in companies if c.isin}
    db_ingest = sum(1 for c in companies if c.ingest_enabled)
    nse_only_db = [c for c in companies if c.nse_symbol and not c.bse_scrip_code]
    bse_only_db = [c for c in companies if c.bse_scrip_code and not c.nse_symbol]
    dual_db = [c for c in companies if c.bse_scrip_code and c.nse_symbol]

    missing_bse = sorted(bse_online - db_bse)
    extra_bse = sorted(db_bse - bse_online)
    missing_nse = sorted(nse_online - db_nse)
    extra_nse = sorted(db_nse - nse_online)

    # NSE ISINs not on BSE (expected NSE-only universe additions)
    nse_only_isins_online = nse_isins_online - bse_isins_online
    db_nse_only_isins = {c.isin for c in nse_only_db if c.isin}
    missing_nse_only_isins = sorted(nse_only_isins_online - db_isins)

    # BSE rows with ISIN should have NSE symbol when NSE lists same ISIN
    dual_expected = 0
    dual_missing_nse_symbol = []
    for isin in bse_isins_online & nse_isins_online:
        nse_sym = nse_by_isin[isin]["symbol"]
        dual_expected += 1
        company = next((c for c in companies if c.isin == isin), None)
        if company is None:
            continue
        if company.nse_symbol != nse_sym:
            dual_missing_nse_symbol.append(
                {"isin": isin, "db_nse": company.nse_symbol, "nse": nse_sym, "bse": company.bse_scrip_code}
            )

    ok = not missing_bse and not missing_nse and not missing_nse_only_isins

    stale_nse_on_bse_only = [
        c.nse_symbol
        for c in companies
        if c.bse_scrip_code and c.nse_symbol and c.nse_symbol.upper() not in nse_online
    ]

    report = {
        "ok": ok,
        "online": {
            "bse_active_equities": len(bse_online),
            "bse_with_isin": len(bse_isins_online),
            "nse_eq_symbols": len(nse_online),
            "nse_eq_isins": len(nse_isins_online),
            "nse_only_isins_online": len(nse_only_isins_online),
            "dual_listed_isins": len(bse_isins_online & nse_isins_online),
        },
        "database": {
            "companies_total": len(companies),
            "ingest_enabled": db_ingest,
            "with_bse_scrip": len(db_bse),
            "with_nse_symbol": len(db_nse),
            "dual_listed_rows": len(dual_db),
            "bse_only_rows": len(bse_only_db),
            "nse_only_rows": len(nse_only_db),
        },
        "coverage": {
            "bse_pct": round(100 * len(db_bse) / len(bse_online), 2) if bse_online else 100,
            "nse_pct": round(100 * len(db_nse & nse_online) / len(nse_online), 2) if nse_online else 100,
        },
        "gaps": {
            "missing_bse_scrips": len(missing_bse),
            "missing_nse_symbols": len(missing_nse),
            "missing_nse_only_isins": len(missing_nse_only_isins),
            "extra_bse_scrips": len(extra_bse),
            "extra_nse_symbols": len(extra_nse),
            "dual_isin_nse_symbol_mismatch": len(dual_missing_nse_symbol),
            "stale_nse_symbols_on_bse_rows": len(stale_nse_on_bse_only),
        },
        "samples": {
            "missing_bse_scrips": missing_bse[:show_missing],
            "missing_nse_symbols": missing_nse[:show_missing],
            "missing_nse_only_isins": missing_nse_only_isins[:show_missing],
            "dual_nse_mismatch": dual_missing_nse_symbol[:show_missing],
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-missing", type=int, default=15)
    args = parser.parse_args()
    report = verify(show_missing=args.show_missing)
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
