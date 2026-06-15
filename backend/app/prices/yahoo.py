"""Yahoo Finance price client (uses .NS symbols for NSE-quality data).

NSE carries the bulk of cash-equity liquidity, and Yahoo serves NSE OHLCV via
the `.NS` suffix without requiring us to scrape NSE directly.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import session_scope
from app.db.models import PriceDaily

logger = logging.getLogger(__name__)


def fetch_history(symbol: str, start: dt.date, end: dt.date | None = None) -> pd.DataFrame:
    """Download daily OHLCV for a Yahoo symbol. Returns an empty frame on failure."""
    import yfinance as yf

    end = end or (dt.date.today() + dt.timedelta(days=1))
    try:
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance download failed for %s: %s", symbol, exc)
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance can return MultiIndex columns for a single ticker.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def store_history(symbol: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    records = []
    for idx, r in df.iterrows():
        date = idx.date() if hasattr(idx, "date") else idx
        records.append(
            {
                "yahoo_symbol": symbol,
                "date": date,
                "open": _f(r.get("Open")),
                "high": _f(r.get("High")),
                "low": _f(r.get("Low")),
                "close": _f(r.get("Close")),
                "adj_close": _f(r.get("Adj Close")),
                "volume": _f(r.get("Volume")),
            }
        )
    if not records:
        return 0

    with session_scope() as session:
        stmt = pg_insert(PriceDaily).values(records)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_price_symbol_date")
        session.execute(stmt)
    return len(records)


def update_symbol(symbol: str, lookback_days: int = 200) -> int:
    start = dt.date.today() - dt.timedelta(days=lookback_days)
    df = fetch_history(symbol, start)
    return store_history(symbol, df)


def get_series(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Read stored daily prices for a symbol from the DB as a DataFrame indexed by date."""
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(PriceDaily)
                .where(PriceDaily.yahoo_symbol == symbol)
                .where(PriceDaily.date >= start)
                .where(PriceDaily.date <= end)
                .order_by(PriceDaily.date)
            )
        )
    if not rows:
        return pd.DataFrame()
    data = {
        "date": [r.date for r in rows],
        "close": [r.adj_close or r.close for r in rows],
        "volume": [r.volume for r in rows],
    }
    return pd.DataFrame(data).set_index("date")


def _f(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
