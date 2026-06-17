"""Shared NSE India HTTP session with cookie warming.

NSE blocks many datacenter IPs. Session cookies are required even when the
homepage returns 403; the announcements JSON endpoint often still works.
For AWS/Azure, set NSE_PROXY_URL to a residential or ISP proxy if requests fail.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com/"
NSE_ANN_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_ANN_PAGE,
}


def _proxy() -> str | None:
    url = (settings.nse_proxy_url or "").strip()
    return url or None


def warm_client(*, timeout: float = 30.0) -> httpx.Client:
    """Create an httpx client and warm NSE cookies (best-effort)."""
    client = httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        follow_redirects=True,
        proxy=_proxy(),
    )
    for url in (NSE_HOME, NSE_ANN_PAGE):
        try:
            resp = client.get(url)
            if resp.status_code < 500:
                logger.debug("NSE warm %s -> %s", url, resp.status_code)
                break
        except httpx.HTTPError as exc:
            logger.debug("NSE warm failed for %s: %s", url, exc)
    return client


def get_json(client: httpx.Client, url: str, *, retries: int = 3) -> Any:
    """GET JSON from an NSE API path with simple retries."""
    import time

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url)
            if resp.status_code == 403:
                raise httpx.HTTPStatusError(
                    "NSE returned 403 (often datacenter IP block — set NSE_PROXY_URL)",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            wait = 1.5 * (attempt + 1)
            logger.warning("NSE GET failed (attempt %d): %s; retry in %.1fs", attempt + 1, exc, wait)
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    return None
