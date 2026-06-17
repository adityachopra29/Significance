"""Cross-exchange announcement deduplication (BSE vs NSE).

Dual-listed companies often publish the same filing on both exchanges with
different external IDs and slightly different headline text. We fingerprint
filings by company + calendar day + normalized headline (and subcategory fallback).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

_BOILERPLATE_PREFIXES = (
    "has informed the exchange regarding",
    "has informed the exchange about",
    "has informed the exchange that",
    "has informed the exchange",
    "has informed the stock exchange",
    "this is to inform that",
    "this is to inform you that",
    "this is further to our earlier intimation",
    "pursuant to regulation",
)


def normalize_headline(headline: str | None, subcategory: str | None = None) -> str:
    """Normalize headline text for cross-exchange matching."""
    text = (headline or "").strip()
    if not text and subcategory:
        text = subcategory.strip()
    text = text.lower()
    text = re.sub(r"['\"`]", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for prefix in _BOILERPLATE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    words = text.split()
    return " ".join(words[:48])


def exchange_dedup_hash(
    company_id: int | None,
    announced_at: dt.datetime | None,
    headline: str | None,
    *,
    subcategory: str | None = None,
) -> str | None:
    """Stable hash for the same filing across BSE and NSE."""
    if company_id is None or announced_at is None:
        return None
    norm = normalize_headline(headline, subcategory)
    if not norm:
        return None
    day = announced_at.date().isoformat()
    basis = f"{company_id}:{day}:{norm}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


# Fallback when headline normalization is too weak: same company, same day,
# announcements within this window are treated as duplicates.
CROSS_EXCHANGE_TIME_WINDOW = dt.timedelta(minutes=30)
