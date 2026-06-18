"""Report companies missing market cap after enrichment.

Usage:
    python -m app.scripts.verify_market_caps
    python -m app.scripts.verify_market_caps --output data/marketcap_gaps.json
"""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import select

from app.db.base import init_db, session_scope
from app.db.models import Company


def verify(*, output: str | None) -> dict:
    init_db()
    with session_scope() as session:
        companies = list(
            session.scalars(
                select(Company).where(Company.ingest_enabled.is_(True)).order_by(Company.name)
            )
        )

    with_cap = [c for c in companies if c.market_cap_cr is not None]
    missing = [c for c in companies if c.market_cap_cr is None]

    report = {
        "ok": len(missing) == 0,
        "total_ingest_enabled": len(companies),
        "with_market_cap": len(with_cap),
        "missing_market_cap": len(missing),
        "coverage_pct": round(100 * len(with_cap) / len(companies), 2) if companies else 100,
        "missing": [
            {
                "id": c.id,
                "name": c.name,
                "bse_scrip_code": c.bse_scrip_code,
                "nse_symbol": c.nse_symbol,
                "yahoo_symbol": c.yahoo_symbol,
            }
            for c in missing
        ],
    }

    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/marketcap_gaps.json")
    args = parser.parse_args()

    report = verify(output=args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "missing"}, indent=2))
    if report["missing"]:
        print(f"\nMissing market cap for {len(report['missing'])} companies — see {args.output}")
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
