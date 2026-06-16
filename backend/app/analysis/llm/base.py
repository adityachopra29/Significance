"""Provider-agnostic LLM abstraction.

A single `LLMProvider.analyze()` contract produces a structured `LLMAnalysis`
for an announcement. Concrete providers (OpenAI / Anthropic / Gemini) implement
`_complete_json()`.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

VALID_EVENT_TYPES_HINT = (
    "acquisition, merger, demerger, order_win, fundraise, qip, buyback, open_offer, "
    "delisting, capex_expansion, results, credit_rating, sast_stake_change, "
    "preferential_issue, legal_regulatory, fraud_default, bonus, stock_split, dividend, "
    "management_change, resignation, investor_presentation, analyst_meet, agm_egm, "
    "board_meeting, newspaper_publication, trading_window, disclosure_other, other"
)

SYSTEM_PROMPT = (
    "You are a sell-side equity analyst for Indian markets (NSE/BSE). "
    "Given a corporate announcement (and, when available, the full text of its "
    "attached filing), extract structured, factual signal. "
    "Base your numbers on the FILING TEXT when present, not just the headline. "
    "Do not speculate or hallucinate numbers; only use what the text supports. "
    "Respond with STRICT JSON only, no prose."
)

JSON_INSTRUCTION = f"""Return a JSON object with exactly these keys:
{{
  "event_type": one of [{VALID_EVENT_TYPES_HINT}],
  "direction": "bullish" | "bearish" | "neutral",
  "sentiment": number from -1 (very negative) to 1 (very positive),
  "materiality_hint": number 0..1 (how much this could move the stock),
  "surprise_hint": number 0..1 (how unexpected vs routine this is),
  "confidence": number 0..1 (your confidence in this assessment),
  "summary": "2-3 sentence plain-English takeaway: what happened, why it matters",
  "extracted": {{
    "amount_cr": number|null,        // headline value in INR crore (order size, deal value, raise)
    "revenue_cr": number|null,       // reported revenue/turnover in INR crore, if a result
    "pat_cr": number|null,           // profit after tax in INR crore, if a result
    "yoy_pct": number|null,          // year-on-year change % of the key metric, if stated
    "qoq_pct": number|null,          // quarter-on-quarter change %, if stated
    "pct_change": number|null,       // any other key percentage figure
    "stake_pct": number|null,        // stake / shareholding % involved
    "notes": string|null             // one short factual note
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
    summary: str = ""
    extracted: dict = field(default_factory=dict)
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
    ) -> LLMAnalysis:
        user = self._build_prompt(
            headline,
            body,
            company_name,
            attachment_text,
            market_cap_cr=market_cap_cr,
            adv_cr=adv_cr,
        )
        raw = self._complete_json(SYSTEM_PROMPT, user)
        return self._parse(raw)

    def _build_prompt(
        self,
        headline: str,
        body: str | None,
        company_name: str | None,
        attachment_text: str | None = None,
        market_cap_cr: float | None = None,
        adv_cr: float | None = None,
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
        parts.append(f"Headline: {headline}")
        if body and body.strip() and body.strip() != headline.strip():
            parts.append(f"Details: {body.strip()[:2000]}")
        if attachment_text and attachment_text.strip():
            parts.append("\nFILING TEXT (extracted from the attached PDF):")
            parts.append(attachment_text.strip()[:9000])
        return "\n".join(parts)

    def _parse(self, raw: str) -> LLMAnalysis:
        data = _loads_lenient(raw)
        if not isinstance(data, dict):
            return LLMAnalysis(provider=self.name, model=self.model)
        extracted = data.get("extracted")
        return LLMAnalysis(
            event_type=str(data.get("event_type", "other")),
            direction=str(data.get("direction", "neutral")),
            sentiment=_clip(data.get("sentiment", 0.0), -1, 1),
            materiality_hint=_clip(data.get("materiality_hint", 0.3), 0, 1),
            surprise_hint=_clip(data.get("surprise_hint", 0.3), 0, 1),
            confidence=_clip(data.get("confidence", 0.5), 0, 1),
            summary=str(data.get("summary", ""))[:1000],
            extracted=extracted if isinstance(extracted, dict) else {},
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
