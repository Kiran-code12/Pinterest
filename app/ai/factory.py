import logging

import httpx

from ..config import Settings
from .base import AIProvider, AIProviderError
from .local import LocalProvider
from .remote import AnthropicProvider, OpenAIProvider

log = logging.getLogger("engine.ai")


def build_ai_provider(settings: Settings, http: httpx.Client | None = None) -> AIProvider:
    """AI_PROVIDER=local (default, free) | openai | anthropic. Falls back to local if keys are missing."""
    try:
        if settings.ai_provider == "openai":
            return OpenAIProvider(settings, http)
        if settings.ai_provider == "anthropic":
            return AnthropicProvider(settings, http)
    except AIProviderError as ex:
        log.warning("AI provider unavailable (%s); using local deterministic copy", ex)
    return LocalProvider()
