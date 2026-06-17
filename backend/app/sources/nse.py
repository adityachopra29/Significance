"""NSE corporate announcements adapter.

Uses the public JSON endpoint behind nseindia.com corporate filings:
  GET /api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY

Unlike BSE per-scrip backfill, NSE supports date-range queries across all equities
in one call per day — much faster for universe backfills.

Cloud note: NSE may block AWS/Azure egress IPs. Configure NSE_PROXY_URL for a
residential/ISP proxy if polls fail with 403.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from urllib.parse import urlencode

import httpx

from app.sources.base import RawAnnouncementDTO, Source
from app.sources.nse_session import get_json, warm_client

logger = logging.getLogger(__name__)

ANN_API = "https://www.nseindia.com/api/corporate-announcements"

_HEADLINE_RE = re.compile(r"regarding\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return dt.datetime.strptime(value[:26], fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _headline_from_row(row: dict) -> str:
    text = (row.get("attchmntText") or "").strip()
    match = _HEADLINE_RE.search(text)
    if match:
        return match.group(1).strip()[:500]
    if text:
        first = re.split(r"\.\s+", text, maxsplit=1)[0].strip()
        if len(first) >= 20:
            return first[:500]
    symbol = (row.get("symbol") or "").strip()
    desc = (row.get("desc") or "").strip()
    name = (row.get("sm_name") or symbol).strip()
    if desc:
        return f"{name}: {desc}"[:500]
    return name or symbol or "NSE announcement"


class NSEAnnouncementsSource(Source):
    name = "nse"

    def __init__(
        self,
        request_pause: float = 0.8,
        timeout: float = 30.0,
        max_backfill_days: int = 90,
    ):
        self.request_pause = request_pause
        self.timeout = timeout
        self.max_backfill_days = max_backfill_days

    def fetch(self, since: dt.datetime | None = None) -> list[RawAnnouncementDTO]:
        if since is None:
            since = dt.datetime.now() - dt.timedelta(days=2)
        return self.fetch_range(since.date(), dt.date.today())

    def fetch_range(
        self,
        from_date: dt.date,
        to_date: dt.date,
        *,
        symbol: str | None = None,
        client: httpx.Client | None = None,
    ) -> list[RawAnnouncementDTO]:
        today = dt.date.today()
        if (today - from_date).days > self.max_backfill_days:
            from_date = today - dt.timedelta(days=self.max_backfill_days)
        if to_date < from_date:
            return []

        owns_client = client is None
        if owns_client:
            client = warm_client(timeout=self.timeout)

        results: list[RawAnnouncementDTO] = []
        try:
            day = from_date
            while day <= to_date:
                rows = self._fetch_day(client, day, symbol=symbol)
                for row in rows:
                    dto = self._row_to_dto(row)
                    if dto is not None:
                        results.append(dto)
                day += dt.timedelta(days=1)
                if day <= to_date:
                    time.sleep(self.request_pause)
        finally:
            if owns_client:
                client.close()

        logger.info(
            "NSE fetch returned %d announcements (%s .. %s%s)",
            len(results),
            from_date,
            to_date,
            f", symbol={symbol}" if symbol else "",
        )
        return results

    def fetch_symbol(
        self,
        symbol: str,
        since: dt.datetime | None = None,
        client: httpx.Client | None = None,
    ) -> list[RawAnnouncementDTO]:
        if since is None:
            since = dt.datetime.now() - dt.timedelta(days=30)
        return self.fetch_range(
            since.date(),
            dt.date.today(),
            symbol=symbol.upper(),
            client=client,
        )

    def warmed_client(self) -> httpx.Client:
        return warm_client(timeout=self.timeout)

    def _fetch_day(
        self,
        client: httpx.Client,
        day: dt.date,
        *,
        symbol: str | None = None,
    ) -> list[dict]:
        dmy = day.strftime("%d-%m-%Y")
        params: dict[str, str] = {
            "index": "equities",
            "from_date": dmy,
            "to_date": dmy,
        }
        if symbol:
            params["symbol"] = symbol.upper()
        url = f"{ANN_API}?{urlencode(params)}"
        data = get_json(client, url)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
                if isinstance(value, dict) and isinstance(value.get("data"), list):
                    return value["data"]
        return []

    def _row_to_dto(self, row: dict) -> RawAnnouncementDTO | None:
        symbol = (row.get("symbol") or "").strip().upper() or None
        headline = _headline_from_row(row)
        if not headline:
            return None

        announced_at = _parse_dt(row.get("an_dt") or row.get("sort_date") or row.get("exchdisstime"))

        seq = row.get("seq_id")
        external_id = str(seq).strip() if seq is not None else None

        attachment = (row.get("attchmntFile") or "").strip() or None
        body = (row.get("attchmntText") or "").strip() or None
        subcategory = (row.get("desc") or "").strip() or None
        industry = (row.get("smIndustry") or "").strip() or None

        return RawAnnouncementDTO(
            source=self.name,
            external_id=external_id,
            nse_symbol=symbol,
            headline=headline,
            body=body,
            category=industry,
            subcategory=subcategory,
            attachment_url=attachment,
            announced_at=announced_at,
            raw_json=row,
        )
