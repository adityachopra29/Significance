"""Load the company universe into the DB.

Modes:
  nifty500  — Nifty 500 via NSE CSV + BSE ISIN map (legacy default)
  merged    — full BSE equity master + NSE EQUITY_L symbols + NSE-only listings

Usage:
    python -m app.scripts.load_universe
    python -m app.scripts.load_universe --mode merged
    python -m app.scripts.load_universe --mode merged --enrich
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
NSE_EQUITY_CSV = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def _fetch_nifty500() -> list[dict]:
    r = httpx.get(NIFTY500_CSV, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def _fetch_nse_equity() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_isin, by_symbol) for NSE EQ series."""
    r = httpx.get(NSE_EQUITY_CSV, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=60, follow_redirects=True)
    r.raise_for_status()
    by_isin: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        series = (row.get(" SERIES") or row.get("SERIES") or "").strip()
        if series != "EQ":
            continue
        isin = (row.get(" ISIN NUMBER") or row.get("ISIN NUMBER") or "").strip()
        symbol = (row.get("SYMBOL") or "").strip().upper()
        name = (row.get("NAME OF COMPANY") or "").strip()
        if not symbol:
            continue
        rec = {"symbol": symbol, "name": name, "isin": isin or None}
        by_symbol[symbol] = rec
        if isin:
            by_isin[isin] = rec
    return by_isin, by_symbol


def _find_company(session, *, scrip: str | None = None, nse_symbol: str | None = None, isin: str | None = None):
    if scrip:
        company = session.scalar(select(Company).where(Company.bse_scrip_code == scrip))
        if company is not None:
            return company
    if nse_symbol:
        company = session.scalar(select(Company).where(Company.nse_symbol == nse_symbol.upper()))
        if company is not None:
            return company
    if isin:
        return session.scalar(select(Company).where(Company.isin == isin))
    return None


def _upsert_company(
    session,
    *,
    scrip: str | None,
    name: str,
    isin: str | None,
    nse_symbol: str | None,
    sector: str | None,
) -> Company:
    nse_symbol = nse_symbol.upper() if nse_symbol else None
    company = _find_company(session, scrip=scrip, nse_symbol=nse_symbol, isin=isin)
    if company is None:
        company = Company(
            bse_scrip_code=scrip,
            nse_symbol=nse_symbol,
            name=name or nse_symbol or scrip or "Unknown",
        )
        session.add(company)
    if scrip:
        company.bse_scrip_code = scrip
    company.name = name or company.name
    company.isin = isin or company.isin
    if nse_symbol:
        company.nse_symbol = nse_symbol
        company.yahoo_symbol = f"{nse_symbol}.NS"
    elif not company.yahoo_symbol and company.nse_symbol:
        company.yahoo_symbol = f"{company.nse_symbol}.NS"
    company.sector = sector or company.sector
    company.ingest_enabled = True
    return company


def load_nifty500(enrich: bool = False) -> dict:
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
            _upsert_company(
                session,
                scrip=rec["scrip_code"],
                name=name or rec["name"],
                isin=isin or rec.get("isin"),
                nse_symbol=symbol or rec.get("symbol"),
                sector=sector,
            )
            matched += 1

    if enrich:
        from app.scripts.load_companies import _enrich_market_caps

        _enrich_market_caps()

    return {"mode": "nifty500", "matched": matched, "unmatched": len(unmatched), "unmatched_symbols": unmatched[:20]}


def load_merged(enrich: bool = False) -> dict:
    init_db()
    master = get_master()
    nse_by_isin, nse_by_symbol = _fetch_nse_equity()
    bse_matched = 0
    nse_only = 0

    bse_isins = set(master["by_isin"].keys())

    with session_scope() as session:
        for rec in master["by_scrip"].values():
            isin = rec.get("isin")
            nse = nse_by_isin.get(isin) if isin else None
            nse_symbol = nse["symbol"] if nse else rec.get("symbol")
            name = rec["name"]
            if nse and nse.get("name"):
                name = nse["name"]
            _upsert_company(
                session,
                scrip=rec["scrip_code"],
                name=name,
                isin=isin,
                nse_symbol=nse_symbol,
                sector=rec.get("industry"),
            )
            bse_matched += 1

        for isin, nse in nse_by_isin.items():
            if isin in bse_isins:
                continue
            _upsert_company(
                session,
                scrip=None,
                name=nse["name"],
                isin=isin,
                nse_symbol=nse["symbol"],
                sector=None,
            )
            nse_only += 1

    if enrich:
        from app.scripts.load_companies import _enrich_market_caps

        _enrich_market_caps()

    return {
        "mode": "merged",
        "bse_matched": bse_matched,
        "nse_only": nse_only,
        "nse_equity_total": len(nse_by_symbol),
        "nse_isin_map": len(nse_by_isin),
    }


def lookup_nse_symbol(symbol: str) -> dict | None:
    """Resolve a symbol from NSE EQUITY_L (for NSE-only watchlist adds)."""
    _, by_symbol = _fetch_nse_equity()
    return by_symbol.get(symbol.upper())


def load_universe(mode: str = "nifty500", enrich: bool = False) -> dict:
    if mode == "merged":
        return load_merged(enrich=enrich)
    return load_nifty500(enrich=enrich)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("nifty500", "merged"), default="nifty500")
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args()
    res = load_universe(mode=args.mode, enrich=args.enrich)
    print(f"Universe loaded: {res}")


if __name__ == "__main__":
    main()
