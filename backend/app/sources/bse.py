"""BSE corporate announcements adapter.

Talks directly to the public api.bseindia.com endpoint that powers
bseindia.com's "Corporate Announcements" page. BSE is lenient toward
datacenter IPs (unlike NSE), so this runs fine from cloud hosts with
polite rate-limiting + retries.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import httpx

from app.sources.base import RawAnnouncementDTO, Source

logger = logging.getLogger(__name__)

ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/corporates/ann.html",
    "Origin": "https://www.bseindia.com",
}


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(value[: len(fmt) + 6], fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


class BSEAnnouncementsSource(Source):
    name = "bse"

    def __init__(self, max_pages: int = 5, request_pause: float = 0.6, timeout: float = 20.0):
        self.max_pages = max_pages
        self.request_pause = request_pause
        self.timeout = timeout

    # The BSE all-announcements feed (empty strScrip) only honors a SINGLE day
    # (strPrevDate must equal strToDate); ranges only work per-scrip. So we
    # iterate day-by-day from `since` to today.
    MAX_BACKFILL_DAYS = 90

    def fetch(self, since: dt.datetime | None = None) -> list[RawAnnouncementDTO]:
        if since is None:
            since = dt.datetime.now() - dt.timedelta(days=2)

        today = dt.date.today()
        start = since.date()
        num_days = (today - start).days
        if num_days > self.MAX_BACKFILL_DAYS:
            start = today - dt.timedelta(days=self.MAX_BACKFILL_DAYS)
        days = [today - dt.timedelta(days=i) for i in range((today - start).days + 1)]

        results: list[RawAnnouncementDTO] = []
        with httpx.Client(headers=HEADERS, timeout=self.timeout, follow_redirects=True) as client:
            # Warm cookies (best-effort).
            try:
                client.get("https://www.bseindia.com/", timeout=self.timeout)
            except httpx.HTTPError:
                pass

            for day in days:
                ymd = day.strftime("%Y%m%d")
                for page in range(1, self.max_pages + 1):
                    params = {
                        "pageno": page,
                        "strCat": "-1",
                        "strPrevDate": ymd,
                        "strScrip": "",
                        "strSearch": "P",
                        "strToDate": ymd,
                        "strType": "C",
                        "subcategory": "-1",
                    }
                    rows = self._get_page(client, params)
                    if not rows:
                        break
                    for row in rows:
                        dto = self._row_to_dto(row)
                        if dto is not None:
                            results.append(dto)
                    if len(rows) < 50:  # last page for this day
                        break
                    time.sleep(self.request_pause)

        logger.info("BSE fetch returned %d announcements across %d day(s)", len(results), len(days))
        return results

    def fetch_scrip(
        self,
        scrip_code: str,
        since: dt.datetime | None = None,
        client: httpx.Client | None = None,
    ) -> list[RawAnnouncementDTO]:
        """Fetch announcements for a single scrip over a date range.

        Unlike the all-feed (which needs a single day), the per-scrip query
        honors a from/to range, so this is used for complete backfills.

        Pass a pre-warmed `client` to reuse cookies across many scrips (much
        faster for bulk backfills); otherwise a one-off warmed client is used.
        """
        if since is None:
            since = dt.datetime.now() - dt.timedelta(days=30)
        from_date = since.strftime("%Y%m%d")
        to_date = dt.datetime.now().strftime("%Y%m%d")

        owns_client = client is None
        if owns_client:
            client = httpx.Client(headers=HEADERS, timeout=self.timeout, follow_redirects=True)
            try:
                client.get("https://www.bseindia.com/", timeout=self.timeout)
            except httpx.HTTPError:
                pass

        results: list[RawAnnouncementDTO] = []
        try:
            for page in range(1, self.max_pages + 1):
                params = {
                    "pageno": page,
                    "strCat": "-1",
                    "strPrevDate": from_date,
                    "strScrip": str(scrip_code),
                    "strSearch": "P",
                    "strToDate": to_date,
                    "strType": "C",
                    "subcategory": "-1",
                }
                rows = self._get_page(client, params)
                if not rows:
                    break
                for row in rows:
                    dto = self._row_to_dto(row)
                    if dto is not None:
                        results.append(dto)
                if len(rows) < 50:
                    break
                time.sleep(self.request_pause)
        finally:
            if owns_client:
                client.close()
        return results

    def warmed_client(self) -> httpx.Client:
        """Create a cookie-warmed client for reuse across many per-scrip calls."""
        client = httpx.Client(headers=HEADERS, timeout=self.timeout, follow_redirects=True)
        try:
            client.get("https://www.bseindia.com/", timeout=self.timeout)
        except httpx.HTTPError:
            pass
        return client

    def _get_page(self, client: httpx.Client, params: dict) -> list[dict]:
        for attempt in range(3):
            try:
                resp = client.get(ANN_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                return data.get("Table", []) or []
            except (httpx.HTTPError, ValueError) as exc:
                wait = 1.5 * (attempt + 1)
                logger.warning("BSE page fetch failed (attempt %d): %s; retrying in %.1fs", attempt + 1, exc, wait)
                time.sleep(wait)
        return []

    def _row_to_dto(self, row: dict) -> RawAnnouncementDTO | None:
        scrip = row.get("SCRIP_CD")
        scrip = str(scrip).strip() if scrip is not None else None
        headline = (row.get("HEADLINE") or row.get("NEWSSUB") or "").strip()
        if not headline:
            return None

        attachment = (row.get("ATTACHMENTNAME") or "").strip()
        attachment_url = f"{ATTACH_BASE}{attachment}" if attachment else None

        announced_at = _parse_dt(row.get("DT_TM") or row.get("News_submission_dt") or row.get("NEWS_DT"))

        return RawAnnouncementDTO(
            source=self.name,
            external_id=str(row.get("NEWSID")).strip() if row.get("NEWSID") else None,
            bse_scrip_code=scrip,
            headline=headline,
            body=(row.get("NEWSSUB") or "").strip() or None,
            category=(row.get("CATEGORYNAME") or "").strip() or None,
            subcategory=(row.get("SUBCATNAME") or "").strip() or None,
            attachment_url=attachment_url,
            announced_at=announced_at,
            raw_json=row,
        )
