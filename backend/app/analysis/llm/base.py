"""Provider-agnostic LLM abstraction.

A single `LLMProvider.analyze()` contract produces a structured `LLMAnalysis`
for an announcement. Concrete providers (OpenAI / Anthropic / Gemini) implement
`_complete_json()`.
"""
from __future__ import annotations

import datetime as dt
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.analysis import taxonomy
from app.analysis.llm.validate import VALID_EVENT_TYPES, validate_analysis

ANALYSIS_SCHEMA_VERSION = "v2"

VALID_EVENT_TYPES_HINT = ", ".join(sorted(VALID_EVENT_TYPES))

SYSTEM_PROMPT = (
    "You are a sell-side equity analyst for Indian markets (NSE/BSE). "
    "Given a corporate announcement (and, when available, the full text of its "
    "attached filing), extract structured, factual signal. "
    "Base your numbers on the FILING TEXT when present, not just the headline. "
    "Do not speculate or hallucinate numbers; only use what the text supports. "
    "Convert all monetary amounts to INR crore in extracted fields: "
    "1 crore = 100 lakh; if only lakhs are stated, divide by 100. "
    "Respond with STRICT JSON only, no prose.\n\n"
    "BSE disambiguation examples:\n"
    "- 'Outcome of board meeting' with no material decision -> board_meeting, is_routine=true\n"
    "- 'Newspaper publication' / statutory advertisement -> newspaper_publication, is_routine=true\n"
    "- 'Trading window closure' -> trading_window, is_routine=true\n"
    "- 'Bagging of order' / commercial contract -> order_win (not legal_regulatory)\n"
    "- SEBI penalty / court order -> legal_regulatory\n"
    "- Quarterly/annual financial results -> results (extract revenue, PAT, YoY/QoQ %)"
)

JSON_INSTRUCTION = f"""Return a JSON object with exactly these keys:
{{
  "event_type": one of [{VALID_EVENT_TYPES_HINT}],
  "direction": "bullish" | "bearish" | "neutral",
  "sentiment": number from -1 (very negative) to 1 (very positive),
  "materiality_hint": number 0..1 (how much this could move the stock),
  "surprise_hint": number 0..1 (how unexpected vs routine this is),
  "confidence": number 0..1 (your confidence in this assessment),
  "is_routine": boolean (true for scheduled compliance, intimation-only, newspaper ads),
  "routine_reason": string|null (short reason if is_routine is true),
  "extraction_quality": "from_filing" | "headline_only" | "inferred",
  "summary": "2-3 sentence plain-English takeaway: what happened, why it matters",
  "extracted": {{
    "amount_cr": number|null,
    "amount_unit": "cr" | "lakh" | "million" | null,
    "revenue_cr": number|null,
    "pat_cr": number|null,
    "ebitda_cr": number|null,
    "eps_rs": number|null,
    "yoy_pct": number|null,
    "qoq_pct": number|null,
    "pct_change": number|null,
    "stake_pct": number|null,
    "guidance_change": "up" | "down" | "maintained" | "none" | null,
    "notes": string|null
  }}
}}"""


@dataclass
class LLMAnalysis:
    event_type: str = "other"
    direction: str = "neutral"
    sentiment: float = 0.0
    materiality_hint: float = 0.3
    surprise_hint: float = 0.3
    confidence: float = 0.4
    is_routine: bool = False
    routine_reason: str | None = None
    extraction_quality: str = "headline_only"
    summary: str = ""
    extracted: dict = field(default_factory=dict)
    schema_version: str = ANALYSIS_SCHEMA_VERSION
    provider: str = ""
    model: str = ""


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def _complete_json(self, system: str, user: str) -> str:
        """Return the model's raw text response (expected to be JSON)."""
        raise NotImplementedError

    def analyze(
        self,
        headline: str,
        body: str | None,
        company_name: str | None,
        attachment_text: str | None = None,
        market_cap_cr: float | None = None,
        adv_cr: float | None = None,
        announced_at: dt.datetime | None = None,
    ) -> LLMAnalysis:
        user = self._build_prompt(
            headline,
            body,
            company_name,
            attachment_text,
            market_cap_cr=market_cap_cr,
            adv_cr=adv_cr,
            announced_at=announced_at,
        )
        raw = self._complete_json(SYSTEM_PROMPT, user)
        parsed = self._parse(raw)
        return validate_analysis(parsed, headline, body, attachment_text)

    def _build_prompt(
        self,
        headline: str,
        body: str | None,
        company_name: str | None,
        attachment_text: str | None = None,
        market_cap_cr: float | None = None,
        adv_cr: float | None = None,
        announced_at: dt.datetime | None = None,
    ) -> str:
        parts = [JSON_INSTRUCTION, ""]
        if company_name:
            parts.append(f"Company: {company_name}")
        context = []
        if market_cap_cr is not None:
            context.append(f"market_cap_cr={market_cap_cr:.2f}")
        if adv_cr is not None:
            context.append(f"adv_cr={adv_cr:.2f}")
        if context:
            parts.append(
                "Company context (for scale only; do not invent numbers from it): "
                + ", ".join(context)
            )
        if announced_at is not None:
            parts.append(f"Announced at (IST): {announced_at.isoformat()}")
            parts.append(
                "Note: results/filings after ~15:30 IST may not be fully priced until the next session."
            )
        parts.append(f"Headline: {headline}")
        if body and body.strip() and body.strip() != headline.strip():
            parts.append(f"Details: {body.strip()[:2000]}")

        combined_for_nums = f"{headline}\n{body or ''}\n{attachment_text or ''}"
        pre_extracted = taxonomy.extract_numbers(combined_for_nums)
        if pre_extracted:
            parts.append(
                "Pre-extracted figures (verify against filing; convert to crore if needed): "
                + json.dumps(pre_extracted)
            )

        if attachment_text and attachment_text.strip():
            parts.append("\nFILING TEXT (extracted from the attached PDF):")
            parts.append(attachment_text.strip()[:9000])
        return "\n".join(parts)

    def _parse(self, raw: str) -> LLMAnalysis:
        data = _loads_lenient(raw)
        if not isinstance(data, dict):
            return LLMAnalysis(provider=self.name, model=self.model)
        extracted = data.get("extracted")
        routine_reason = data.get("routine_reason")
        return LLMAnalysis(
            event_type=str(data.get("event_type", "other")),
            direction=str(data.get("direction", "neutral")),
            sentiment=_clip(data.get("sentiment", 0.0), -1, 1),
            materiality_hint=_clip(data.get("materiality_hint", 0.3), 0, 1),
            surprise_hint=_clip(data.get("surprise_hint", 0.3), 0, 1),
            confidence=_clip(data.get("confidence", 0.5), 0, 1),
            is_routine=bool(data.get("is_routine", False)),
            routine_reason=str(routine_reason)[:200] if routine_reason else None,
            extraction_quality=str(data.get("extraction_quality", "headline_only")),
            summary=str(data.get("summary", ""))[:1000],
            extracted=extracted if isinstance(extracted, dict) else {},
            schema_version=ANALYSIS_SCHEMA_VERSION,
            provider=self.name,
            model=self.model,
        )


def _loads_lenient(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    # Strip code fences if present.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _clip(value, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo if lo > 0 else 0.0
