"""Unified market-cap source for BSE and NSE stocks.

Market cap is a property of the *company* (ISIN), not the exchange:
    market_cap = shares_outstanding x price

So one resolver serves both exchanges. Shares outstanding changes rarely, so we
cache it on the Company and recompute market cap cheaply from the latest stored
close price. Yahoo Finance is the unified provider — `.NS` (NSE) has the best
coverage and is preferred; `.BO` (BSE) is the fallback for BSE-only names. This
also works unchanged when NSE-listed stocks are added later.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Company, PriceDaily

logger = logging.getLogger(__name__)

# Refresh shares at most this often; market cap is recomputed from price daily.
SHARES_TTL_DAYS = 7
ADV_LOOKBACK_DAYS = 20


def _candidate_symbols(company: Company) -> list[str]:
    symbols: list[str] = []
    if company.yahoo_symbol:
        symbols.append(company.yahoo_symbol)
    if company.nse_symbol:
        candidate = f"{company.nse_symbol}.NS"
        if candidate not in symbols:
            symbols.append(candidate)
    if company.bse_scrip_code:
        symbols.append(f"{company.bse_scrip_code}.BO")
    return symbols


def _fetch_shares_and_price(company: Company) -> tuple[float | None, float | None]:
    """Return (shares_outstanding, last_price) from the first symbol that resolves."""
    import yfinance as yf

    for symbol in _candidate_symbols(company):
        try:
            ticker = yf.Ticker(symbol)
            fi = ticker.fast_info
            shares = getattr(fi, "shares", None)
            price = getattr(fi, "last_price", None)
            if not shares:
                # fast_info miss — fall back to the heavier info dict.
                try:
                    shares = ticker.get_info().get("sharesOutstanding")
                except Exception:  # noqa: BLE001
                    shares = None
            if shares:
                return float(shares), (float(price) if price else None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("market cap lookup failed for %s: %s", symbol, exc)
            continue
    return None, None


def _latest_close(company: Company) -> float | None:
    symbol = company.yahoo_symbol or (f"{company.nse_symbol}.NS" if company.nse_symbol else None)
    if not symbol:
        return None
    with session_scope() as session:
        row = session.scalar(
            select(PriceDaily)
            .where(PriceDaily.yahoo_symbol == symbol)
            .order_by(PriceDaily.date.desc())
            .limit(1)
        )
        if row is None:
            return None
        return row.adj_close or row.close


def _adv_cr(company: Company, lookback_days: int = ADV_LOOKBACK_DAYS) -> float | None:
    """Average daily traded value in INR crore from recent stored OHLCV data."""
    symbol = company.yahoo_symbol or (f"{company.nse_symbol}.NS" if company.nse_symbol else None)
    if not symbol:
        return None
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(PriceDaily)
                .where(PriceDaily.yahoo_symbol == symbol)
                .order_by(PriceDaily.date.desc())
                .limit(lookback_days)
            )
        )
    values = []
    for row in rows:
        close = row.adj_close or row.close
        if close and row.volume:
            values.append(float(close) * float(row.volume) / 1e7)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def refresh_company(company_id: int, force_shares: bool = False) -> float | None:
    """Refresh one company's market cap and ADV. Returns market_cap_cr or None."""
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            return None

        shares = company.shares_outstanding
        stale = (
            force_shares
            or shares is None
            or company.market_cap_asof is None
            or (dt.date.today() - company.market_cap_asof).days >= SHARES_TTL_DAYS
        )

        price = _latest_close(company)
        adv = _adv_cr(company)
        if (price is None or adv is None) and (company.yahoo_symbol or company.nse_symbol):
            # Ensure we have recent OHLCV before computing market cap from close and ADV.
            try:
                from app.prices import yahoo

                yahoo.update_symbol(
                    company.yahoo_symbol or f"{company.nse_symbol}.NS",
                    lookback_days=max(60, ADV_LOOKBACK_DAYS * 3),
                )
                price = price or _latest_close(company)
                adv = adv or _adv_cr(company)
            except Exception as exc:  # noqa: BLE001
                logger.debug("price/ADV refresh failed for company %d: %s", company_id, exc)

        fetched_price = None
        if stale:
            shares, fetched_price = _fetch_shares_and_price(company)
            if shares:
                company.shares_outstanding = shares

        price = price or fetched_price
        if adv is not None:
            company.adv_cr = adv
        if shares and price:
            company.market_cap_cr = round(shares * price / 1e7, 2)  # rupees -> crore
            company.market_cap_asof = dt.date.today()
            return company.market_cap_cr
        return company.market_cap_cr


def refresh_all(only_missing: bool = False, pause: float = 0.4) -> dict:
    """Refresh market caps for all active companies. Throttled for politeness."""
    with session_scope() as session:
        stmt = select(Company.id).where(Company.active.is_(True))
        if only_missing:
            stmt = stmt.where(Company.market_cap_cr.is_(None))
        ids = list(session.scalars(stmt))

    updated = 0
    for idx, cid in enumerate(ids, start=1):
        try:
            if refresh_company(cid):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("market cap refresh failed for company %d: %s", cid, exc)
        if idx % 50 == 0:
            logger.info("Market cap refresh: %d/%d (%d updated)", idx, len(ids), updated)
        time.sleep(pause)
    logger.info("Market cap refresh complete: %d/%d updated", updated, len(ids))
    return {"companies": len(ids), "updated": updated}
