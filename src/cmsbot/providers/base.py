"""Abstract base for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from cmsbot.models import LLMResponse


class LLMProvider(ABC):
    """Unified interface for all LLM providers."""

    name: str  # "anthropic", "openai", "gemini", "openai_compat"

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Generate a completion and return a normalized response."""
        ...

    async def generate_simple(
        self,
        user_prompt: str,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Convenience wrapper for single-turn generation."""
        return await self.generate(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
