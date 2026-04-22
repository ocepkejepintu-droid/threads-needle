"""Unified LLM client supporting Anthropic Claude and Z.ai GLM.

Automatically selects provider based on LLM_PROVIDER env var.
Both providers use OpenAI-compatible chat completions API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict | None = None


class LLMClient:
    """Unified client for LLM providers."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = (self.settings.llm_provider or "anthropic").lower()

        if self.provider == "anthropic":
            self.api_key = self.settings.anthropic_api_key
            self.base_url = "https://api.anthropic.com/v1"
            self.default_model = self.settings.claude_recommender_model
        elif self.provider == "zai":
            self.api_key = self.settings.zai_api_key
            # Zhipu AI (GLM) uses OpenAI-compatible endpoint
            self.base_url = "https://open.bigmodel.cn/api/paas/v4"
            self.default_model = self.settings.zai_model
        elif self.provider == "openrouter":
            self.api_key = self.settings.openrouter_api_key
            self.base_url = "https://openrouter.ai/api/v1"
            self.default_model = self.settings.openrouter_model or "anthropic/claude-3.5-sonnet"
        elif self.provider == "none":
            self.api_key = None
            self.base_url = None
            self.default_model = None
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

        if not self.api_key:
            raise ValueError(f"API key not set for provider: {self.provider}")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter requires additional headers
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://threads-analytics.local"
            headers["X-Title"] = "Threads Analytics"
        
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=120.0,
        )

    def create_message(
        self,
        model: str | None = None,
        max_tokens: int = 4096,
        system: str | None = None,
        messages: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a message to the LLM and return the response."""
        model = model or self.default_model
        messages = messages or []

        if self.provider == "anthropic":
            return self._call_anthropic(model, max_tokens, system, messages, temperature)
        else:
            return self._call_openai_compatible(model, max_tokens, system, messages, temperature)

    def _call_anthropic(
        self,
        model: str,
        max_tokens: int,
        system: str | None,
        messages: list[dict],
        temperature: float,
    ) -> LLMResponse:
        """Call Anthropic API."""
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        resp = client.messages.create(**kwargs)
        text = "".join(block.text for block in resp.content if getattr(block, "text", None))
        return LLMResponse(
            text=text,
            model=resp.model,
            usage={
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            } if resp.usage else None,
        )

    def _call_openai_compatible(
        self,
        model: str,
        max_tokens: int,
        system: str | None,
        messages: list[dict],
        temperature: float,
    ) -> LLMResponse:
        """Call OpenAI-compatible API (Z.ai, etc.)."""
        # Convert Anthropic-style messages to OpenAI format
        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            content = msg.get("content", "")
            # Handle Anthropic's content blocks format (text/image)
            if isinstance(content, list):
                # For now, extract just text content
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "image":
                            # Skip images for now - would need base64 encoding
                            pass
                content = "\n".join(text_parts)
            openai_messages.append({"role": msg.get("role", "user"), "content": content})

        payload = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        resp = self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content", "")

        usage = data.get("usage")

        return LLMResponse(
            text=text,
            model=data.get("model", model),
            usage=usage,
        )

    def close(self) -> None:
        self.client.close()


_cached_llm_client: LLMClient | None = None


def create_llm_client() -> LLMClient:
    """Factory function to create an LLM client."""
    return LLMClient()


def get_llm_client() -> LLMClient:
    """Return a cached LLM client instance for reuse."""
    global _cached_llm_client
    if _cached_llm_client is None:
        _cached_llm_client = LLMClient()
    return _cached_llm_client
