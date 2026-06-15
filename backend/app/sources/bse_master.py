"""BSE equity master: the full list of active equities (scrip code, name, ISIN,
symbol, group). Used to resolve a stock when the user adds one by scrip code or
symbol, and to map ISINs to BSE scrip codes when loading the universe.

Cached in-process with a TTL since it's ~1.7MB and changes rarely.
"""
from __future__ import annotations

import time

import httpx

from app.sources.bse import HEADERS

MASTER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListOfScripData/w"
_TTL_SECONDS = 6 * 3600

_cache: dict | None = None
_cache_ts: float = 0.0


def _fetch_master() -> list[dict]:
    params = {"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"}
    with httpx.Client(headers=HEADERS, timeout=40.0, follow_redirects=True) as client:
        client.get("https://www.bseindia.com/")
        resp = client.get(MASTER_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def get_master(force: bool = False) -> dict:
    """Return indexed master: {'by_scrip': {...}, 'by_isin': {...}, 'by_symbol': {...}}."""
    global _cache, _cache_ts
    if not force and _cache is not None and (time.time() - _cache_ts) < _TTL_SECONDS:
        return _cache

    rows = _fetch_master()
    by_scrip, by_isin, by_symbol = {}, {}, {}
    for r in rows:
        scrip = str(r.get("SCRIP_CD", "")).strip()
        if not scrip:
            continue
        rec = {
            "scrip_code": scrip,
            "name": (r.get("Scrip_Name") or "").strip(),
            "isin": (r.get("ISIN_NUMBER") or "").strip() or None,
            "symbol": (r.get("scrip_id") or "").strip() or None,
            "group": (r.get("GROUP") or "").strip() or None,
            "industry": (r.get("INDUSTRY") or "").strip() or None,
        }
        by_scrip[scrip] = rec
        if rec["isin"]:
            by_isin.setdefault(rec["isin"], rec)
        if rec["symbol"]:
            by_symbol[rec["symbol"].upper()] = rec

    _cache = {"by_scrip": by_scrip, "by_isin": by_isin, "by_symbol": by_symbol}
    _cache_ts = time.time()
    return _cache


def lookup(scrip_code: str | None = None, symbol: str | None = None) -> dict | None:
    """Resolve a stock by BSE scrip code or NSE/BSE symbol."""
    master = get_master()
    if scrip_code:
        rec = master["by_scrip"].get(str(scrip_code).strip())
        if rec:
            return rec
    if symbol:
        rec = master["by_symbol"].get(symbol.strip().upper())
        if rec:
            return rec
    return None
