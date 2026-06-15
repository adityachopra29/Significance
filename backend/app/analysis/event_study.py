"""Event-study engine.

Estimates a market model (R_stock = alpha + beta * R_market) on a pre-event
estimation window, then computes Abnormal Returns (AR), Cumulative Abnormal
Returns (CAR) over several windows, and abnormal volume around the event.

Methodology follows MacKinlay's event-study framework and the PEAD literature:
a large surprise with a *small* day-0 abnormal return signals under-reaction
(opportunity), while a large move that already happened is "priced in".
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class EventStudyOutput:
    alpha: float | None = None
    beta: float | None = None
    ar_day0: float | None = None
    car_t1: float | None = None
    car_t5: float | None = None
    car_t20: float | None = None
    abnormal_volume: float | None = None
    t_stat: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _returns(series: pd.Series) -> pd.Series:
    return series.astype(float).pct_change().dropna()


def compute_event_study(
    stock_df: pd.DataFrame,
    market_df: pd.DataFrame,
    event_date: dt.date,
    estimation_window_days: int = 120,
) -> EventStudyOutput:
    """Compute event-study stats. Returns an empty output if data is insufficient."""
    out = EventStudyOutput()
    if stock_df.empty or market_df.empty:
        return out

    stock_ret = _returns(stock_df["close"])
    market_ret = _returns(market_df["close"])
    rets = pd.concat([stock_ret, market_ret], axis=1, keys=["stock", "market"]).dropna()
    if rets.empty:
        return out

    dates = list(rets.index)
    # Event day = first trading day on/after the announcement date.
    event_idx = next((i for i, d in enumerate(dates) if d >= event_date), None)
    if event_idx is None:
        return out

    est = rets.iloc[max(0, event_idx - estimation_window_days):event_idx]
    if len(est) < 30:  # need a minimally reliable estimation window
        return out

    # OLS market model via least squares.
    x = est["market"].values
    y = est["stock"].values
    A = np.vstack([np.ones_like(x), x]).T
    try:
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return out
    alpha, beta = float(coef[0]), float(coef[1])
    out.alpha, out.beta = alpha, beta

    resid = y - (alpha + beta * x)
    resid_std = float(np.std(resid, ddof=2)) if len(resid) > 2 else None

    def ar_on(i: int) -> float | None:
        if i >= len(dates):
            return None
        row = rets.iloc[i]
        return float(row["stock"] - (alpha + beta * row["market"]))

    out.ar_day0 = ar_on(event_idx)
    if out.ar_day0 is not None and resid_std:
        out.t_stat = out.ar_day0 / resid_std

    def car(k: int) -> float | None:
        ars = [ar_on(event_idx + j) for j in range(0, k + 1)]
        ars = [a for a in ars if a is not None]
        return float(np.sum(ars)) if ars else None

    out.car_t1 = car(1)
    out.car_t5 = car(5)
    out.car_t20 = car(20)

    # Abnormal volume = event-day volume / mean estimation-window volume.
    try:
        vol = stock_df["volume"].astype(float)
        est_dates = est.index
        mean_vol = float(vol.loc[vol.index.isin(est_dates)].mean())
        event_day = dates[event_idx]
        if mean_vol and event_day in vol.index:
            out.abnormal_volume = float(vol.loc[event_day]) / mean_vol
    except (KeyError, ValueError, ZeroDivisionError):
        pass

    return out
