"""Concrete LLM providers."""
from __future__ import annotations

from app.analysis.llm.base import LLMProvider

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-1.5-flash",
}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def _complete_json(self, system: str, user: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.model or DEFAULT_MODELS["openai"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def _complete_json(self, system: str, user: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model or DEFAULT_MODELS["anthropic"],
            max_tokens=2048,
            temperature=0.1,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def _complete_json(self, system: str, user: str) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(
            self.model or DEFAULT_MODELS["gemini"],
            system_instruction=system,
            generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        )
        resp = model.generate_content(user)
        return resp.text or ""

