"""Composite scoring: turns LLM analysis + company context + event-study output
into a transparent 0..100 relevance/impact score.

    score = (w_event*eventType + w_mat*materiality + w_surp*surprise
             + w_sent*|sentiment| + w_price*priceReaction)
            * liquidity * confidence * timeDecay  * 100

Every sub-factor is stored so the dashboard can explain the ranking.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.analysis import taxonomy
from app.analysis.event_study import EventStudyOutput
from app.analysis.llm.base import LLMAnalysis

# Additive-part weights (sum to 1.0). Sector momentum deferred to a later phase.
W_EVENT = 0.28
W_MATERIALITY = 0.28
W_SURPRISE = 0.16
W_SENTIMENT = 0.12
W_PRICE = 0.16

# Materiality: an event worth >= 10% of market cap saturates the quant signal.
MATERIALITY_MCAP_SATURATION = 0.10
# Price reaction: a >= 6% day-0 abnormal return is treated as "fully reacted".
PRICE_REACTION_AR_SCALE = 0.06
# Time decay half-life in hours.
TIME_DECAY_HALFLIFE_H = 48.0
TIME_DECAY_FLOOR = 0.2


@dataclass
class ScoreResult:
    event_type: str
    direction: str
    sentiment: float
    factor_event_type: float
    factor_materiality: float
    factor_surprise: float
    factor_sentiment: float
    factor_price_reaction: float
    factor_liquidity: float
    factor_confidence: float
    factor_time_decay: float
    composite_score: float


def _materiality(llm: LLMAnalysis, market_cap_cr: float | None) -> float:
    hint = llm.materiality_hint
    amount = None
    if isinstance(llm.extracted, dict):
        amount = llm.extracted.get("amount_cr") or llm.extracted.get("amount")
    quant = None
    if amount and market_cap_cr and market_cap_cr > 0:
        try:
            ratio = float(amount) / float(market_cap_cr)
            quant = min(1.0, ratio / MATERIALITY_MCAP_SATURATION)
        except (TypeError, ValueError):
            quant = None
    if quant is None:
        return hint
    return round(0.6 * quant + 0.4 * hint, 4)


def _price_reaction(es: EventStudyOutput | None) -> float:
    if es is None or es.ar_day0 is None:
        return 0.5  # unknown / not yet observable -> neutral
    # Under-reaction: the less price has already moved, the more opportunity remains.
    under_reaction = 1.0 - min(1.0, abs(es.ar_day0) / PRICE_REACTION_AR_SCALE)
    factor = under_reaction
    if es.abnormal_volume and es.abnormal_volume > 1.5:
        factor = min(1.0, factor + 0.1)  # volume confirms genuine interest
    return round(factor, 4)


def _liquidity(market_cap_cr: float | None, matched: bool) -> float:
    if not matched:
        return 0.5
    if market_cap_cr is None:
        return 0.6
    if market_cap_cr >= 20000:
        return 1.0
    if market_cap_cr >= 5000:
        return 0.85
    if market_cap_cr >= 1000:
        return 0.65
    return 0.4


def _time_decay(announced_at: dt.datetime | None, now: dt.datetime | None = None) -> float:
    if announced_at is None:
        return 0.7
    now = now or dt.datetime.now(tz=announced_at.tzinfo)
    hours = max(0.0, (now - announced_at).total_seconds() / 3600.0)
    decay = 0.5 ** (hours / TIME_DECAY_HALFLIFE_H)
    return round(max(TIME_DECAY_FLOOR, decay), 4)


def score(
    llm: LLMAnalysis,
    market_cap_cr: float | None,
    company_matched: bool,
    event_study: EventStudyOutput | None,
    announced_at: dt.datetime | None,
) -> ScoreResult:
    fe = taxonomy.EVENT_TYPE_WEIGHTS.get(llm.event_type, 0.3)
    fm = _materiality(llm, market_cap_cr)
    fs = llm.surprise_hint
    fse = abs(llm.sentiment)
    fp = _price_reaction(event_study)
    fl = _liquidity(market_cap_cr, company_matched)
    fc = min(1.0, llm.confidence + (0.1 if company_matched else 0.0))
    ft = _time_decay(announced_at)

    additive = (
        W_EVENT * fe
        + W_MATERIALITY * fm
        + W_SURPRISE * fs
        + W_SENTIMENT * fse
        + W_PRICE * fp
    )
    composite01 = additive * fl * fc * ft
    composite = round(100.0 * composite01, 1)

    return ScoreResult(
        event_type=llm.event_type,
        direction=llm.direction,
        sentiment=llm.sentiment,
        factor_event_type=round(fe, 4),
        factor_materiality=round(fm, 4),
        factor_surprise=round(fs, 4),
        factor_sentiment=round(fse, 4),
        factor_price_reaction=round(fp, 4),
        factor_liquidity=round(fl, 4),
        factor_confidence=round(fc, 4),
        factor_time_decay=round(ft, 4),
        composite_score=composite,
    )
