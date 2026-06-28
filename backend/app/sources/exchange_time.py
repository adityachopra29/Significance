"""Parse NSE/BSE announcement timestamps.

Exchange APIs return wall-clock times in IST without a timezone offset. Naive
values must be localized to Asia/Kolkata before persistence so they are stored
as correct UTC regardless of the Postgres session timezone.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = dt.timezone.utc

_NAIVE_FORMATS = (
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d %b %Y %H:%M:%S",
)


def _parse_naive(value: str) -> dt.datetime | None:
    for fmt in _NAIVE_FORMATS:
        try:
            return dt.datetime.strptime(value[:26], fmt)
        except ValueError:
            continue
    return None


def _to_utc(parsed: dt.datetime) -> dt.datetime:
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST).astimezone(UTC)
    return parsed.astimezone(UTC)


def parse_exchange_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    # ISO with explicit offset / Z — trust the source.
    if re.search(r"[zZ]$", value) or re.search(r"[+-]\d{2}:?\d{2}$", value):
        try:
            normalized = value.replace("Z", "+00:00").replace("z", "+00:00")
            return _to_utc(dt.datetime.fromisoformat(normalized))
        except ValueError:
            pass

    naive = _parse_naive(value)
    if naive is not None:
        return _to_utc(naive)

    try:
        return _to_utc(dt.datetime.fromisoformat(value))
    except ValueError:
        return None
