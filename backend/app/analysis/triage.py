"""Pre-LLM triage: decide which filings enter the Live feed and LLM queue.

Bias toward recall — analyze unless confidently routine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.analysis import taxonomy
from app.analysis.taxonomy import EVENT_TYPE_WEIGHTS

# BSE subcategory names (normalized lowercase) → tier
_BSE_SUBCATEGORY_TIER: dict[str, str] = {
    "acquisition": "A",
    "amalgamation": "A",
    "merger": "A",
    "demerger": "A",
    "slump sale": "A",
    "bagging/receiving of orders/contracts": "A",
    "bagging / receiving of orders/contracts": "A",
    "raising of funds": "A",
    "qualified institutional placement": "A",
    "preferential issue": "A",
    "rights issue": "A",
    "buyback of shares": "A",
    "buy-back of shares": "A",
    "open offer": "A",
    "delisting": "A",
    "fraud": "A",
    "default": "A",
    "insolvency": "A",
    "capacity expansion": "A",
    "new project": "A",
    "financial results": "B",
    "financial result": "B",
    "limited review report": "B",
    "credit rating": "B",
    "regulation 29": "B",
    "shareholding": "B",
    "pledge": "B",
    "encumbrance": "B",
    "regulatory action": "B",
    "litigation": "B",
    "outcome of board meeting": "B",
    "dividend": "C",
    "bonus": "C",
    "stock split": "C",
    "sub-division of shares": "C",
    "appointment": "C",
    "resignation": "C",
    "cessation": "C",
    "newspaper publication": "D",
    "advertisement": "D",
    "closure of trading window": "D",
    "trading window": "D",
    "analyst meet": "D",
    "investor meet": "D",
    "investor presentation": "D",
    "agm": "D",
    "egm": "D",
    "intimation of board meeting": "D",
}

# Headline classify → tier when no BSE subcategory match
_EVENT_TIER: dict[str, str] = {
    "acquisition": "A",
    "merger": "A",
    "demerger": "A",
    "order_win": "A",
    "fundraise": "A",
    "qip": "A",
    "buyback": "A",
    "open_offer": "A",
    "delisting": "A",
    "fraud_default": "A",
    "capex_expansion": "A",
    "results": "B",
    "credit_rating": "B",
    "sast_stake_change": "B",
    "preferential_issue": "B",
    "legal_regulatory": "B",
    "management_change": "C",
    "resignation": "C",
    "bonus": "C",
    "stock_split": "C",
    "dividend": "C",
    "investor_presentation": "D",
    "analyst_meet": "D",
    "agm_egm": "D",
    "board_meeting": "D",
    "newspaper_publication": "D",
    "trading_window": "D",
    "disclosure_other": "E",
    "other": "E",
}

_TIER_RANK = {"A": 1, "B": 2, "E": 2, "C": 3, "D": 4}

_ALWAYS_ANALYZE_SUBSTR = frozenset(
    s.lower()
    for s in (
        "outcome of board meeting",
        "financial result",
        "quarterly result",
        "acquisition",
        "amalgamation",
        "merger",
        "demerger",
        "order",
        "contract",
        "fund raise",
        "fundraise",
        "buyback",
        "open offer",
        "delisting",
        "insolvency",
        "default",
        "fraud",
        "qip",
        "preferential",
    )
)

_ROUTINE_ONLY_SUBSTR = frozenset(
    s.lower()
    for s in (
        "intimation of board meeting",
        "newspaper publication",
        "newspaper advert",
        "trading window closure",
        "closure of trading window",
        "schedule of analyst",
        "schedule of investor",
        "investor presentation",
        "analyst meet",
        "investor meet",
        "agm notice",
        "egm notice",
    )
)

_MATERIAL_RESCUE = re.compile(
    r"\b(acqui(re|sition)|merger|amalgamation|demerger|spin[\s-]?off|"
    r"order|contract|awarded|bags|work order|fund\s?rais|buy[\s-]?back|"
    r"open offer|delist|insolvency|default|fraud|nclt|qip|preferential|"
    r"sebi|penalty|show cause|litigation|capacity|expansion|commissioning|"
    r"material|substantial|significant|record)\b",
    re.I,
)


class TriageAction(str, Enum):
    pass_through = "pass"
    skip = "skip"


@dataclass
class TriageResult:
    action: TriageAction
    triage_event_type: str
    triage_tier: str
    triage_priority: int
    category_rank: int
    skip_reason: str | None = None
    triage_reason: str | None = None


def _combined_text(headline: str, body: str | None, subcategory: str | None) -> str:
    parts = [headline or ""]
    if body and body.strip():
        parts.append(body.strip())
    if subcategory:
        parts.append(subcategory)
    return " ".join(parts)


def _tier_from_subcategory(subcategory: str | None) -> str | None:
    if not subcategory:
        return None
    low = subcategory.strip().lower()
    if low in _BSE_SUBCATEGORY_TIER:
        return _BSE_SUBCATEGORY_TIER[low]
    for key, tier in _BSE_SUBCATEGORY_TIER.items():
        if key in low:
            return tier
    return None


def _has_numeric_hint(text: str) -> bool:
    nums = taxonomy.extract_numbers(text)
    return bool(nums)


def _is_routine_only(text: str, subcategory: str | None) -> bool:
    low = text.lower()
    if any(p in low for p in _ROUTINE_ONLY_SUBSTR):
        if not _MATERIAL_RESCUE.search(low):
            sub_tier = _tier_from_subcategory(subcategory)
            if sub_tier == "D":
                return True
            if "intimation of board meeting" in low and "outcome" not in low:
                return True
    return False


def compute_ranks(tier: str, event_type: str) -> tuple[int, int]:
    """Return (triage_priority, category_rank). Lower = higher priority / earlier in feed."""
    tier_rank = _TIER_RANK.get(tier, 3)
    event_weight = EVENT_TYPE_WEIGHTS.get(event_type, 0.3)
    event_rank = int((1.0 - event_weight) * 100)
    triage_priority = tier_rank * 100 + event_rank
    # category_rank: sort Live by importance (lower = more important)
    category_rank = tier_rank * 10_000 + int((1.0 - event_weight) * 1000)
    return triage_priority, category_rank


def triage(
    headline: str,
    body: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> TriageResult:
    text = _combined_text(headline, body, subcategory)
    event_type = taxonomy.classify(text)
    tier = _tier_from_subcategory(subcategory) or _EVENT_TIER.get(event_type, "E")
    triage_priority, category_rank = compute_ranks(tier, event_type)

    # Pass signals (any → analyze)
    if tier in ("A", "B", "E"):
        return TriageResult(
            action=TriageAction.pass_through,
            triage_event_type=event_type,
            triage_tier=tier,
            triage_priority=triage_priority,
            category_rank=category_rank,
            triage_reason=f"tier_{tier}",
        )

    if any(p in text.lower() for p in _ALWAYS_ANALYZE_SUBSTR):
        return TriageResult(
            action=TriageAction.pass_through,
            triage_event_type=event_type,
            triage_tier=tier if tier != "D" else "B",
            triage_priority=triage_priority,
            category_rank=category_rank,
            triage_reason="keyword_pass",
        )

    if _MATERIAL_RESCUE.search(text):
        tp, cr = compute_ranks("B", event_type)
        return TriageResult(
            action=TriageAction.pass_through,
            triage_event_type=event_type,
            triage_tier="B",
            triage_priority=tp,
            category_rank=cr,
            triage_reason="material_rescue",
        )

    if _has_numeric_hint(text):
        tp, cr = compute_ranks("B", event_type)
        return TriageResult(
            action=TriageAction.pass_through,
            triage_event_type=event_type,
            triage_tier="B",
            triage_priority=tp,
            category_rank=cr,
            triage_reason="numeric_hint",
        )

    if subcategory and "outcome of board meeting" in subcategory.lower():
        return TriageResult(
            action=TriageAction.pass_through,
            triage_event_type=event_type,
            triage_tier="B",
            triage_priority=triage_priority,
            category_rank=category_rank,
            triage_reason="board_outcome",
        )

    # Skip only when confidently routine
    if tier == "D" and _is_routine_only(text, subcategory):
        return TriageResult(
            action=TriageAction.skip,
            triage_event_type=event_type,
            triage_tier=tier,
            triage_priority=triage_priority,
            category_rank=category_rank,
            skip_reason="routine_confident",
        )

    # Default: pass (recall bias)
    return TriageResult(
        action=TriageAction.pass_through,
        triage_event_type=event_type,
        triage_tier=tier if tier != "D" else "E",
        triage_priority=triage_priority,
        category_rank=category_rank,
        triage_reason="default_pass",
    )
