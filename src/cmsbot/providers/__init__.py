"""Provider registry — instantiate providers from config."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cmsbot.config import Config
    from cmsbot.providers.base import LLMProvider


def build_provider(name: str, config: Config) -> LLMProvider:
    """Build a provider instance by name from the global config."""
    cfg = config.get_provider_config(name)

    if name in ("anthropic", "anthropic_opus"):
        from cmsbot.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=cfg.api_key, model=cfg.model)
    elif name == "openai":
        from cmsbot.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=cfg.api_key, model=cfg.model)
    elif name == "gemini":
        from cmsbot.providers.gemini import GeminiProvider
        return GeminiProvider(api_key=cfg.api_key, model=cfg.model)
    elif name == "openai_compat":
        from cmsbot.providers.openai_provider import OpenAICompatProvider
        return OpenAICompatProvider(
            api_key=cfg.api_key,
            model=cfg.model,
            base_url=cfg.base_url or "https://api.together.xyz/v1",
        )
    else:
        raise ValueError(f"Unknown provider: {name}")


__all__ = [
    "build_provider",
]
