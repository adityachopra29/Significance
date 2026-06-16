"""Select an explicitly configured LLM provider."""
from __future__ import annotations

import logging

from app.analysis.llm.base import LLMProvider
from app.analysis.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
)
from app.config import settings

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def get_provider() -> LLMProvider:
    name = (settings.llm_provider or "").strip().lower()
    if not name:
        raise RuntimeError(
            "LLM is not configured. Set LLM_PROVIDER to one of: openai, anthropic, gemini."
        )
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
            "Set it to one of: openai, anthropic, gemini."
        )
    if not settings.llm_api_key:
        raise RuntimeError(
            f"LLM_PROVIDER='{name}' requires LLM_API_KEY. Add an API key or pause the worker."
        )
    return cls(model=settings.llm_model, api_key=settings.llm_api_key)


def llm_status() -> dict:
    """Small status payload for API/UI; does not instantiate clients."""
    name = (settings.llm_provider or "").strip().lower()
    supported = ", ".join(sorted(_PROVIDERS))
    if not name:
        return {
            "configured": False,
            "provider": None,
            "error": f"LLM_PROVIDER is not set. Supported providers: {supported}.",
        }
    if name not in _PROVIDERS:
        return {
            "configured": False,
            "provider": name,
            "error": f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. Supported providers: {supported}.",
        }
    if not settings.llm_api_key:
        return {
            "configured": False,
            "provider": name,
            "error": f"LLM_API_KEY is missing for provider '{name}'.",
        }
    return {"configured": True, "provider": name, "error": None}
