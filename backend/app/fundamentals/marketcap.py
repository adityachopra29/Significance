"""Unified market-cap source for BSE and NSE stocks.

Resolution order: BSE (official StockTrading API) → NSE (quote-equity) →
Yahoo Finance (shares × price fallback). Market cap is stored in INR crore on
Company.market_cap_cr.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import httpx
from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Company, PriceDaily
from app.sources.bse import HEADERS as BSE_HEADERS
from app.sources.bse_marketcap import fetch_market_cap_cr as bse_market_cap_cr
from app.sources.nse_marketcap import fetch_market_cap_cr as nse_market_cap_cr
from app.sources.nse_session import warm_client

logger = logging.getLogger(__name__)

ADV_LOOKBACK_DAYS = 20

# Set after first quote-equity 403 in a refresh run (Akamai blocks scripts).
_nse_quote_blocked = False


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


def _fetch_market_cap_cr(
    company: Company,
    *,
    bse_client: httpx.Client | None = None,
    nse_client: httpx.Client | None = None,
) -> tuple[float | None, float | None]:
    """Return (market_cap_cr, shares_outstanding) — shares set only on Yahoo fallback."""
    global _nse_quote_blocked

    if company.bse_scrip_code:
        mcap = bse_market_cap_cr(company.bse_scrip_code, client=bse_client)
        if mcap is not None:
            return mcap, None
    if company.nse_symbol and not _nse_quote_blocked:
        try:
            mcap = nse_market_cap_cr(company.nse_symbol, client=nse_client)
            if mcap is not None:
                return mcap, None
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                _nse_quote_blocked = True
                logger.info(
                    "NSE quote-equity blocked (403); skipping NSE market cap for remainder of run"
                )
            else:
                logger.debug("NSE market cap failed for %s: %s", company.nse_symbol, exc)
    shares, price = _fetch_shares_and_price_yahoo(company)
    if shares and price:
        return round(shares * price / 1e7, 2), shares
    return None, None


def _fetch_shares_and_price_yahoo(company: Company) -> tuple[float | None, float | None]:
    """Yahoo fallback: return (shares_outstanding, last_price) from the first symbol that resolves."""
    import yfinance as yf

    for symbol in _candidate_symbols(company):
        try:
            ticker = yf.Ticker(symbol)
            fi = ticker.fast_info
            shares = getattr(fi, "shares", None)
            price = getattr(fi, "last_price", None)
            if not shares:
                try:
                    shares = ticker.get_info().get("sharesOutstanding")
                except Exception:  # noqa: BLE001
                    shares = None
            if shares:
                return float(shares), (float(price) if price else None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Yahoo market cap lookup failed for %s: %s", symbol, exc)
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


def refresh_company(
    company_id: int,
    *,
    bse_client: httpx.Client | None = None,
    nse_client: httpx.Client | None = None,
) -> float | None:
    """Refresh one company's market cap and ADV. Returns market_cap_cr or None."""
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            return None

        if (company.yahoo_symbol or company.nse_symbol) and _latest_close(company) is None:
            try:
                from app.prices import yahoo

                yahoo.update_symbol(
                    company.yahoo_symbol or f"{company.nse_symbol}.NS",
                    lookback_days=max(60, ADV_LOOKBACK_DAYS * 3),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("price refresh failed for company %d: %s", company_id, exc)

        mcap, shares = _fetch_market_cap_cr(company, bse_client=bse_client, nse_client=nse_client)
        adv = _adv_cr(company)
        if adv is not None:
            company.adv_cr = adv
        if mcap is not None:
            company.market_cap_cr = mcap
            company.market_cap_asof = dt.date.today()
            if shares is not None:
                company.shares_outstanding = shares
            return company.market_cap_cr
        return company.market_cap_cr


def _open_bse_client() -> httpx.Client:
    client = httpx.Client(headers=BSE_HEADERS, timeout=30.0, follow_redirects=True)
    try:
        client.get("https://www.bseindia.com/")
    except httpx.HTTPError as exc:
        logger.debug("BSE warm failed for market cap refresh: %s", exc)
    return client


def _open_nse_client() -> httpx.Client | None:
    try:
        return warm_client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("NSE client warm failed for market cap refresh: %s", exc)
        return None


def refresh_all(only_missing: bool = False, pause: float = 0.2) -> dict:
    """Refresh market caps for active companies. Throttled for politeness."""
    global _nse_quote_blocked
    _nse_quote_blocked = False

    with session_scope() as session:
        stmt = select(Company.id).where(Company.ingest_enabled.is_(True))
        if only_missing:
            stmt = stmt.where(Company.market_cap_cr.is_(None))
        ids = list(session.scalars(stmt))

    updated = 0
    bse_client = _open_bse_client()
    nse_client = _open_nse_client()
    yf_logger = logging.getLogger("yfinance")
    prev_yf_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    try:
        for idx, cid in enumerate(ids, start=1):
            try:
                if refresh_company(cid, bse_client=bse_client, nse_client=nse_client):
                    updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("market cap refresh failed for company %d: %s", cid, exc)
            if idx % 50 == 0:
                logger.info("Market cap refresh: %d/%d (%d updated)", idx, len(ids), updated)
            time.sleep(pause)
    finally:
        yf_logger.setLevel(prev_yf_level)
        bse_client.close()
        if nse_client is not None:
            nse_client.close()

    logger.info("Market cap refresh complete: %d/%d updated", updated, len(ids))
    return {"companies": len(ids), "updated": updated}
