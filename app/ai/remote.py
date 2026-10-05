"""OpenAI and Anthropic providers over their official REST APIs (httpx; no SDK dependency)."""
from __future__ import annotations

import httpx

from ..config import Settings
from .base import SYSTEM_PROMPT, AIProvider, AIProviderError, build_user_prompt, parse_json_reply


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.openai_api_key:
            raise AIProviderError("OPENAI_API_KEY is not set")
        self.s, self.http = settings, http or httpx.Client(timeout=40)

    def generate_pin_copy(self, facts: dict, concept_key: str, variation: int) -> dict:
        try:
            r = self.http.post("https://api.openai.com/v1/chat/completions",
                               headers={"Authorization": f"Bearer {self.s.openai_api_key}"},
                               json={"model": self.s.openai_model, "temperature": 0.7,
                                     "response_format": {"type": "json_object"},
                                     "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                  {"role": "user", "content": build_user_prompt(facts, concept_key, variation)}]})
        except httpx.HTTPError as ex:
            raise AIProviderError("Could not reach OpenAI") from ex
        if r.status_code != 200:
            raise AIProviderError(f"OpenAI error (HTTP {r.status_code})")
        try:
            return parse_json_reply(r.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as ex:
            raise AIProviderError("Unexpected OpenAI response") from ex


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.anthropic_api_key:
            raise AIProviderError("ANTHROPIC_API_KEY is not set")
        self.s, self.http = settings, http or httpx.Client(timeout=40)

    def generate_pin_copy(self, facts: dict, concept_key: str, variation: int) -> dict:
        try:
            r = self.http.post("https://api.anthropic.com/v1/messages",
                               headers={"x-api-key": self.s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                               json={"model": self.s.anthropic_model, "max_tokens": 700, "system": SYSTEM_PROMPT,
                                     "messages": [{"role": "user", "content": build_user_prompt(facts, concept_key, variation)}]})
        except httpx.HTTPError as ex:
            raise AIProviderError("Could not reach Anthropic") from ex
        if r.status_code != 200:
            raise AIProviderError(f"Anthropic error (HTTP {r.status_code})")
        try:
            return parse_json_reply("".join(b.get("text", "") for b in r.json()["content"]))
        except (KeyError, TypeError, ValueError) as ex:
            raise AIProviderError("Unexpected Anthropic response") from ex
