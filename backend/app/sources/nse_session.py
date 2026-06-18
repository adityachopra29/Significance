"""Shared NSE India HTTP session with cookie warming.

NSE endpoints have uneven protection (Akamai/WAF). The corporate-announcements
API often works from scripts; quote-equity is frequently 403 even on home IPs.
Datacenter egress may also be blocked — set NSE_PROXY_URL if announcements fail.
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
    """GET JSON from an NSE API path with simple retries (403 fails fast)."""
    import time

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url)
            if resp.status_code == 403:
                raise httpx.HTTPStatusError(
                    "NSE returned 403 (WAF/bot protection or IP block — proxy may help for announcements)",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                raise
            last_exc = exc
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
        if attempt + 1 < retries:
            wait = 1.5 * (attempt + 1)
            logger.warning("NSE GET failed (attempt %d): %s; retry in %.1fs", attempt + 1, last_exc, wait)
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    return None
