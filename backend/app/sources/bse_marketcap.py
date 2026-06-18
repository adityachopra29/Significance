"""BSE market-cap lookup via the official StockTrading API.

Returns full market cap in INR crore (MktCapFull), matching BSE's website.
Works from datacenter IPs — same api.bseindia.com surface as announcements.
"""
from __future__ import annotations

import logging

import httpx

from app.sources.bse import HEADERS

logger = logging.getLogger(__name__)

STOCK_TRADING_URL = "https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w"


def parse_crore(value: str | float | int | None) -> float | None:
    """Parse BSE crore fields (Indian grouping, e.g. '17,96,782.83')."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def fetch_market_cap_cr(scrip_code: str, *, client: httpx.Client | None = None) -> float | None:
    """Return full market cap in INR crore for a BSE scrip code, or None."""
    scrip = str(scrip_code).strip()
    if not scrip:
        return None

    owns_client = client is None
    if owns_client:
        client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)
        try:
            client.get("https://www.bseindia.com/")
        except httpx.HTTPError as exc:
            logger.debug("BSE warm failed for market cap: %s", exc)

    try:
        resp = client.get(
            STOCK_TRADING_URL,
            params={"flag": "", "quotetype": "EQ", "scripcode": scrip},
        )
        resp.raise_for_status()
        data = resp.json()
        mcap = parse_crore(data.get("MktCapFull") if isinstance(data, dict) else None)
        return round(mcap, 2) if mcap is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("BSE market cap lookup failed for %s: %s", scrip, exc)
        return None
    finally:
        if owns_client:
            client.close()
