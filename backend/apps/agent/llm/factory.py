"""Chooses the LLM client from settings. Tests replace `get_llm_client` with a fake."""

from __future__ import annotations

from django.conf import settings

from .base import LLMClient, LLMUnavailable


def get_llm_client() -> LLMClient:
    provider = settings.AGENT_LLM_PROVIDER
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        if not settings.ANTHROPIC_API_KEY:
            raise LLMUnavailable("The language model is not configured.")
        return AnthropicClient(
            api_key=settings.ANTHROPIC_API_KEY,
            base_url=settings.ANTHROPIC_BASE_URL,
            models={"large": settings.AGENT_MODEL_LARGE, "small": settings.AGENT_MODEL_SMALL},
        )
    raise LLMUnavailable("The language model is not configured.")
