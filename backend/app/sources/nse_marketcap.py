"""NSE market-cap lookup via the official quote-equity API.

Uses tradeInfo.totalMarketCap (INR crore) when available; falls back to
issuedSize × lastPrice. Often blocked by Akamai (403) even on home IPs — BSE
and Yahoo are the reliable sources; this is best-effort only.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx

from app.sources.nse_session import get_json, warm_client

logger = logging.getLogger(__name__)

QUOTE_URL = "https://www.nseindia.com/api/quote-equity"
QUOTE_REFERER = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"


def _trade_info_mcap(data: dict) -> float | None:
    book = data.get("marketDeptOrderBook")
    if not isinstance(book, dict):
        return None
    trade = book.get("tradeInfo")
    if not isinstance(trade, dict):
        return None
    mcap = trade.get("totalMarketCap")
    if mcap is None:
        return None
    try:
        value = float(mcap)
    except (TypeError, ValueError):
        return None
    return round(value, 2) if value > 0 else None


def _computed_mcap(data: dict) -> float | None:
    security = data.get("securityInfo")
    price = data.get("priceInfo")
    if not isinstance(security, dict) or not isinstance(price, dict):
        return None
    issued = security.get("issuedSize")
    last = price.get("lastPrice") or price.get("close")
    if not issued or not last:
        return None
    try:
        return round(float(issued) * float(last) / 1e7, 2)
    except (TypeError, ValueError):
        return None


def fetch_market_cap_cr(nse_symbol: str, *, client: httpx.Client | None = None) -> float | None:
    """Return full market cap in INR crore for an NSE symbol, or None."""
    symbol = nse_symbol.strip().upper()
    if not symbol:
        return None

    owns_client = client is None
    if owns_client:
        client = warm_client()

    try:
        client.headers["Referer"] = QUOTE_REFERER.format(symbol=symbol)
        for section in ("trade_info", None):
            params: dict[str, str] = {"symbol": symbol}
            if section:
                params["section"] = section
            url = f"{QUOTE_URL}?{urlencode(params)}"
            try:
                data = get_json(client, url, retries=1)
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("NSE market cap lookup failed for %s (%s): %s", symbol, section, exc)
                continue
            if not isinstance(data, dict):
                continue
            mcap = _trade_info_mcap(data) or _computed_mcap(data)
            if mcap is not None:
                return mcap
        return None
    finally:
        if owns_client:
            client.close()
