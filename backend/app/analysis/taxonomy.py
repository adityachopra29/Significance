"""Announcement taxonomy: canonical event types, their base weights, and a
keyword-based classifier + naive sentiment used by the heuristic LLM fallback.
"""
from __future__ import annotations

import re

# Base materiality weight per canonical event type (0..1), calibrated initially
# by heuristic and later refined from historical |CAR| in the backtest loop.
EVENT_TYPE_WEIGHTS: dict[str, float] = {
    "acquisition": 1.0,
    "merger": 0.95,
    "fraud_default": 0.9,
    "order_win": 0.9,
    "demerger": 0.85,
    "fundraise": 0.85,
    "qip": 0.85,
    "open_offer": 0.8,
    "delisting": 0.85,
    "buyback": 0.8,
    "capex_expansion": 0.75,
    "results": 0.7,
    "credit_rating": 0.7,
    "sast_stake_change": 0.7,
    "preferential_issue": 0.7,
    "legal_regulatory": 0.6,
    "bonus": 0.6,
    "stock_split": 0.55,
    "management_change": 0.5,
    "resignation": 0.5,
    "dividend": 0.4,
    "investor_presentation": 0.3,
    "analyst_meet": 0.3,
    "agm_egm": 0.3,
    "disclosure_other": 0.3,
    "board_meeting": 0.25,
    "newspaper_publication": 0.05,
    "trading_window": 0.05,
    "other": 0.3,
}

# Ordered (priority high -> low) keyword rules.
_RULES: list[tuple[str, list[str]]] = [
    ("fraud_default", ["fraud", "default", "insolvency", "nclt", "ibc", "winding up", "embezzl"]),
    ("acquisition", ["acquisition", "acquire", "acquires", "stake acquisition", "to acquire"]),
    ("merger", ["merger", "amalgamation", "scheme of arrangement", "merge"]),
    ("demerger", ["demerger", "demerge", "spin-off", "spin off"]),
    ("order_win", ["order", "contract", "bags", "wins", "awarded", "work order", "loa", "letter of award"]),
    ("buyback", ["buyback", "buy-back", "buy back"]),
    ("qip", ["qip", "qualified institutional"]),
    ("fundraise", ["fund raising", "fundraise", "raise funds", "raising of funds", "fund-raising"]),
    ("preferential_issue", ["preferential", "preferential allotment", "warrants"]),
    ("open_offer", ["open offer"]),
    ("delisting", ["delisting", "delist"]),
    ("capex_expansion", ["capex", "expansion", "new plant", "capacity", "commission", "greenfield", "brownfield"]),
    ("credit_rating", ["credit rating", "rating", "icra", "crisil", "care ratings", "upgrade", "downgrade"]),
    ("sast_stake_change", ["sast", "reg. 29", "regulation 29", "acquisition of shares", "pledge", "encumbrance"]),
    ("results", ["financial result", "quarterly result", "results", "earnings", "un-audited", "audited results", "q1", "q2", "q3", "q4"]),
    ("dividend", ["dividend"]),
    ("bonus", ["bonus"]),
    ("stock_split", ["stock split", "sub-division", "subdivision", "split of"]),
    ("resignation", ["resignation", "resign", "cessation"]),
    ("management_change", ["appointment", "appointed", "managing director", "ceo", "cfo", "director", "kmp"]),
    ("legal_regulatory", ["sebi", "order", "penalty", "show cause", "litigation", "court", "tribunal", "gst", "income tax"]),
    ("investor_presentation", ["investor presentation", "presentation"]),
    ("analyst_meet", ["analyst", "investor meet", "conference call", "earnings call"]),
    ("agm_egm", ["agm", "egm", "annual general meeting", "extraordinary general"]),
    ("board_meeting", ["board meeting", "intimation of board", "outcome of board"]),
    ("newspaper_publication", ["newspaper", "publication", "advertisement"]),
    ("trading_window", ["trading window", "closure of trading"]),
]

_POSITIVE = [
    "wins", "bags", "order", "awarded", "acquire", "acquisition", "expansion", "record",
    "growth", "profit", "rises", "surge", "approval", "approved", "buyback", "bonus",
    "dividend", "upgrade", "commission", "launch", "highest", "strong",
]
_NEGATIVE = [
    "fraud", "default", "penalty", "decline", "fall", "loss", "resignation", "downgrade",
    "show cause", "litigation", "insolvency", "delay", "warning", "qualification", "fire",
    "shut", "suspension", "recall", "weak",
]


def classify(text: str) -> str:
    low = (text or "").lower()
    for event_type, keywords in _RULES:
        for kw in keywords:
            if kw in low:
                return event_type
    return "other"


def naive_sentiment(text: str) -> tuple[str, float]:
    low = (text or "").lower()
    pos = sum(1 for w in _POSITIVE if w in low)
    neg = sum(1 for w in _NEGATIVE if w in low)
    if pos == 0 and neg == 0:
        return "neutral", 0.0
    score = (pos - neg) / max(1, pos + neg)
    if score > 0.15:
        return "bullish", round(score, 3)
    if score < -0.15:
        return "bearish", round(score, 3)
    return "neutral", round(score, 3)


def extract_numbers(text: str) -> dict:
    """Best-effort numeric extraction (INR crore amounts, percentages)."""
    low = (text or "").lower()
    out: dict = {}
    # ₹ amounts in crore: "rs 1,234 crore", "₹500 cr"
    cr = re.findall(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(?:cr|crore)", low)
    if cr:
        try:
            out["amount_cr"] = max(float(c.replace(",", "")) for c in cr)
        except ValueError:
            pass
    pct = re.findall(r"([\d.]+)\s*%", low)
    if pct:
        try:
            out["max_pct"] = max(float(p) for p in pct)
        except ValueError:
            pass
    return out
