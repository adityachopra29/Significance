"""Post-LLM validation and normalization (no extra API calls)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis import taxonomy
from app.analysis.taxonomy import EVENT_TYPE_WEIGHTS

if TYPE_CHECKING:
    from app.analysis.llm.base import LLMAnalysis

VALID_EVENT_TYPES = frozenset(EVENT_TYPE_WEIGHTS.keys())

_ROUTINE_TYPES = frozenset(
    {"board_meeting", "newspaper_publication", "trading_window", "agm_egm"}
)
_VALID_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})
_VALID_EXTRACTION_QUALITY = frozenset({"from_filing", "headline_only", "inferred"})


def _combined_text(headline: str, body: str | None, attachment_text: str | None) -> str:
    parts = [headline or ""]
    if body and body.strip():
        parts.append(body.strip())
    if attachment_text and attachment_text.strip():
        parts.append(attachment_text.strip()[:4000])
    return " ".join(parts)


def normalize_event_type(
    raw: str,
    headline: str,
    body: str | None,
    attachment_text: str | None,
) -> str:
    """Map LLM output to a canonical event type."""
    cleaned = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in VALID_EVENT_TYPES:
        return cleaned
    # Common aliases the model may emit.
    aliases = {
        "earnings": "results",
        "financial_results": "results",
        "quarterly_results": "results",
        "order": "order_win",
        "contract_win": "order_win",
        "fund_raising": "fundraise",
        "buy_back": "buyback",
        "open_offer_for_shares": "open_offer",
        "regulatory": "legal_regulatory",
        "compliance": "disclosure_other",
    }
    if cleaned in aliases:
        return aliases[cleaned]
    return taxonomy.classify(_combined_text(headline, body, attachment_text))


def normalize_extracted(extracted: dict) -> dict:
    """Normalize units and legacy keys in the extracted payload."""
    if not isinstance(extracted, dict):
        return {}
    out = dict(extracted)
    unit = str(out.get("amount_unit") or "").lower()
    amount = out.get("amount_cr")
    if amount in (None, ""):
        amount = out.get("amount")
    if amount not in (None, ""):
        try:
            val = float(amount)
            if unit in ("lakh", "lakhs", "lac", "lacs"):
                val /= 100.0
            elif unit in ("million", "mn"):
                val /= 10.0  # 1 million INR = 0.1 crore
            out["amount_cr"] = round(val, 4)
        except (TypeError, ValueError):
            pass
    return out


def validate_analysis(
    llm: LLMAnalysis,
    headline: str,
    body: str | None,
    attachment_text: str | None,
) -> LLMAnalysis:
    """Normalize types, cap routine filings, and clean extracted numbers."""
    llm.event_type = normalize_event_type(llm.event_type, headline, body, attachment_text)

    if llm.direction not in _VALID_DIRECTIONS:
        llm.direction = "neutral"

    if llm.extraction_quality not in _VALID_EXTRACTION_QUALITY:
        has_filing = bool(attachment_text and attachment_text.strip())
        llm.extraction_quality = "from_filing" if has_filing else "headline_only"

    llm.extracted = normalize_extracted(llm.extracted)

    # Routine / compliance filings should not score as high-signal events.
    routine = llm.is_routine or llm.event_type in _ROUTINE_TYPES
    if routine:
        llm.is_routine = True
        llm.surprise_hint = min(llm.surprise_hint, 0.2)
        llm.materiality_hint = min(llm.materiality_hint, 0.25)
        if llm.event_type in EVENT_TYPE_WEIGHTS:
            # taxonomy weight already low; keep hints capped above.
            pass
    elif llm.extraction_quality == "headline_only" and not llm.extracted:
        llm.confidence = min(llm.confidence, 0.45)

    return llm
