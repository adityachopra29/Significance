"""Select an LLM provider from configuration, with safe fallback to heuristic."""
from __future__ import annotations

import logging

from app.analysis.llm.base import LLMProvider
from app.analysis.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    HeuristicProvider,
    OpenAIProvider,
)
from app.config import settings

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "heuristic": HeuristicProvider,
}


def get_provider() -> LLMProvider:
    name = (settings.llm_provider or "heuristic").lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        logger.warning("Unknown LLM_PROVIDER '%s'; falling back to heuristic.", name)
        return HeuristicProvider()
    if name != "heuristic" and not settings.llm_api_key:
        logger.warning("LLM_PROVIDER='%s' but no LLM_API_KEY set; falling back to heuristic.", name)
        return HeuristicProvider()
    return cls(model=settings.llm_model, api_key=settings.llm_api_key)
