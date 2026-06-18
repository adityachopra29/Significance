"""Pre-deploy audit: universe gaps, market-cap gaps, ingest coverage.

Usage:
    python -m app.scripts.audit_pre_deploy
    python -m app.scripts.audit_pre_deploy --output-dir data/pre_deploy_audit
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict

from sqlalchemy import func, select

from app.db.base import init_db, session_scope
from app.db.models import Company, RawAnnouncement
from app.scripts.load_universe import _fetch_nse_equity
from app.scripts.verify_market_caps import verify as verify_market_caps
from app.sources.bse_master import get_master


def _company_row(c: Company) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "isin": c.isin,
        "bse_scrip_code": c.bse_scrip_code,
        "nse_symbol": c.nse_symbol,
        "yahoo_symbol": c.yahoo_symbol,
        "listing": (
            "dual"
            if c.bse_scrip_code and c.nse_symbol
            else "bse_only"
            if c.bse_scrip_code
            else "nse_only"
            if c.nse_symbol
            else "unknown"
        ),
    }


def audit_universe_gaps() -> dict:
    """Stocks on BSE/NSE masters but missing from DB (or stale symbols)."""
    master = get_master(force=True)
    nse_by_isin, nse_by_symbol = _fetch_nse_equity()

    bse_online = set(master["by_scrip"].keys())
    nse_online = {s.upper() for s in nse_by_symbol.keys()}
    nse_isins_online = set(nse_by_isin.keys())
    bse_isins_online = set(master["by_isin"].keys())
    nse_only_isins_online = nse_isins_online - bse_isins_online

    with session_scope() as session:
        companies = list(session.scalars(select(Company)))

    db_bse = {c.bse_scrip_code for c in companies if c.bse_scrip_code}
    db_nse = {c.nse_symbol.upper() for c in companies if c.nse_symbol}
    db_isins = {c.isin for c in companies if c.isin}

    missing_bse = []
    for scrip in sorted(bse_online - db_bse):
        row = master["by_scrip"][scrip]
        missing_bse.append(
            {
                "bse_scrip_code": scrip,
                "name": row.get("name") or row.get("issuer_name"),
                "isin": row.get("isin"),
            }
        )

    missing_nse_eq = []
    for sym in sorted(nse_online - db_nse):
        row = nse_by_symbol[sym]
        missing_nse_eq.append(
            {
                "nse_symbol": sym,
                "name": row.get("name"),
                "isin": row.get("isin"),
            }
        )

    missing_nse_only = []
    for isin in sorted(nse_only_isins_online - db_isins):
        row = nse_by_isin[isin]
        missing_nse_only.append(
            {
                "nse_symbol": row["symbol"],
                "name": row.get("name"),
                "isin": isin,
            }
        )

    stale_nse = [
        _company_row(c)
        for c in companies
        if c.bse_scrip_code
        and c.nse_symbol
        and c.nse_symbol.upper() not in nse_online
    ]

    return {
        "summary": {
            "missing_from_db_bse_active": len(missing_bse),
            "missing_from_db_nse_eq": len(missing_nse_eq),
            "missing_from_db_nse_only_isins": len(missing_nse_only),
            "stale_nse_symbol_on_db_row": len(stale_nse),
        },
        "missing_bse_active_equities": missing_bse,
        "missing_nse_eq_symbols": missing_nse_eq,
        "missing_nse_only_listings": missing_nse_only,
        "stale_nse_symbols_in_db": stale_nse,
    }


def audit_ingest_coverage() -> dict:
    """Companies in universe with no stored announcements (ingest gap)."""
    with session_scope() as session:
        companies = list(
            session.scalars(
                select(Company).where(Company.ingest_enabled.is_(True)).order_by(Company.name)
            )
        )
        ann_counts = dict(
            session.execute(
                select(RawAnnouncement.company_id, func.count())
                .where(RawAnnouncement.company_id.isnot(None))
                .group_by(RawAnnouncement.company_id)
            ).all()
        )
        last_ann = dict(
            session.execute(
                select(
                    RawAnnouncement.company_id,
                    func.max(RawAnnouncement.announced_at),
                )
                .where(RawAnnouncement.company_id.isnot(None))
                .group_by(RawAnnouncement.company_id)
            ).all()
        )

    no_announcements = []
    for c in companies:
        if ann_counts.get(c.id, 0) == 0:
            no_announcements.append(_company_row(c))

    by_listing = defaultdict(list)
    for row in no_announcements:
        by_listing[row["listing"]].append(row)

    # Ingest-enabled but missing exchange identifier needed to poll
    unmappable = [
        _company_row(c)
        for c in companies
        if not c.bse_scrip_code and not c.nse_symbol
    ]

    return {
        "summary": {
            "ingest_enabled_total": len(companies),
            "with_any_announcement": sum(1 for c in companies if ann_counts.get(c.id, 0) > 0),
            "zero_announcements_in_db": len(no_announcements),
            "unmappable_no_bse_or_nse_code": len(unmappable),
            "zero_announcements_bse_only": len(by_listing["bse_only"]),
            "zero_announcements_nse_only": len(by_listing["nse_only"]),
            "zero_announcements_dual": len(by_listing["dual"]),
        },
        "zero_announcements": no_announcements,
        "zero_announcements_by_listing": {k: v for k, v in by_listing.items()},
        "unmappable_companies": unmappable,
        "note": (
            "Zero announcements may mean no filings in the backfill window, "
            "BSE/NSE poll gap, or NSE-only names not reached by BSE. "
            "Not always an error."
        ),
    }


def audit_market_cap_gaps() -> dict:
    return verify_market_caps(output=None)


def run_audit(*, output_dir: str) -> dict:
    init_db()
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()

    universe = audit_universe_gaps()
    ingest = audit_ingest_coverage()
    marketcap = audit_market_cap_gaps()

    report = {
        "generated_at": generated_at,
        "universe_gaps": universe,
        "market_cap_gaps": {
            k: v for k, v in marketcap.items() if k != "missing"
        },
        "ingest_coverage": ingest,
    }

    os.makedirs(output_dir, exist_ok=True)

    paths = {
        "summary": os.path.join(output_dir, "summary.json"),
        "missing_market_cap": os.path.join(output_dir, "missing_market_cap.json"),
        "zero_announcements": os.path.join(output_dir, "zero_announcements.json"),
        "missing_from_exchanges": os.path.join(output_dir, "missing_from_exchanges.json"),
    }

    with open(paths["summary"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    with open(paths["missing_market_cap"], "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated_at": generated_at,
                "summary": {k: marketcap[k] for k in ("missing_market_cap", "coverage_pct", "total_ingest_enabled")},
                "companies": marketcap["missing"],
            },
            fh,
            indent=2,
        )

    with open(paths["zero_announcements"], "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated_at": generated_at,
                "summary": ingest["summary"],
                "companies": ingest["zero_announcements"],
            },
            fh,
            indent=2,
        )

    with open(paths["missing_from_exchanges"], "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated_at": generated_at,
                "summary": universe["summary"],
                "missing_bse_active_equities": universe["missing_bse_active_equities"],
                "missing_nse_eq_symbols": universe["missing_nse_eq_symbols"],
                "missing_nse_only_listings": universe["missing_nse_only_listings"],
                "stale_nse_symbols_in_db": universe["stale_nse_symbols_in_db"],
            },
            fh,
            indent=2,
        )

    report["output_files"] = paths
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-deploy universe / market-cap / ingest audit")
    parser.add_argument("--output-dir", default="data/pre_deploy_audit")
    args = parser.parse_args()

    report = run_audit(output_dir=args.output_dir)
    print(json.dumps(
        {
            "generated_at": report["generated_at"],
            "universe_gaps": report["universe_gaps"]["summary"],
            "market_cap_gaps": report["market_cap_gaps"],
            "ingest_coverage": report["ingest_coverage"]["summary"],
            "output_files": report["output_files"],
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
