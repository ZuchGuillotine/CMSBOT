"""Anthropic (Claude) provider."""

from __future__ import annotations

import anthropic

from cmsbot.budget import compute_cost
from cmsbot.models import LLMResponse
from cmsbot.providers.base import LLMProvider

# Claude 5-generation models: thinking is always on (responses can start with
# thinking blocks), non-default sampling parameters return a 400, and safety
# classifiers can decline a request. Health/supplement copy can trip the bio
# classifier, so these requests opt into server-side fallbacks.
_CLAUDE_5_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Thinking shares max_tokens with the visible answer; leave room for both.
_MIN_MAX_TOKENS_WITH_THINKING = 16000


class RefusalError(RuntimeError):
    """Claude (and its fallback) declined the request."""


def _is_claude_5(model: str) -> bool:
    return model.startswith(_CLAUDE_5_PREFIXES)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5-5"):
        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        if _is_claude_5(self.model):
            kwargs["max_tokens"] = max(max_tokens, _MIN_MAX_TOKENS_WITH_THINKING)
            # The installed SDK has no `fallbacks` keyword yet, so it goes in the body
            response = await self._client.beta.messages.create(
                **kwargs,
                betas=[_FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
            )
        else:
            response = await self._client.messages.create(**kwargs, temperature=temperature)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise RefusalError(f"{self.model} declined the request (category: {category or 'unknown'})")

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        content = "".join(block.text for block in response.content if block.type == "text")

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=response.model or self.model,
            provider=self.name,
            cost=compute_cost(self.model, input_tokens, output_tokens),
        )
