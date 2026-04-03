"""Configuration loading from environment variables and .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class ProviderConfig:
    api_key: str
    model: str
    base_url: str | None = None


@dataclass
class RoleAssignment:
    """Which provider + model handles each pipeline role."""
    author: str = "openai_compat"
    strawman: str = "openai_compat"
    rewriter: str = "anthropic"
    synthesizer: str = "openai"
    judges: list[str] = field(default_factory=lambda: ["anthropic_opus", "openai", "gemini"])
    image_director: str = "openai"


@dataclass
class Config:
    # Provider configs
    anthropic: ProviderConfig | None = None
    anthropic_opus: ProviderConfig | None = None
    openai: ProviderConfig | None = None
    gemini: ProviderConfig | None = None
    openai_compat: ProviderConfig | None = None

    # Pipeline settings
    budget_cap: float = 75.0
    max_rounds: int = 5
    roles: RoleAssignment = field(default_factory=RoleAssignment)

    # Output settings
    output_dir: Path = field(default_factory=lambda: Path("output"))

    # Optional / image providers
    unsplash_key: str | None = None
    xai_key: str | None = None

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> Config:
        """Load config from environment variables, optionally reading a .env file."""
        if env_path:
            load_dotenv(env_path)
        else:
            # Search current dir and parents for .env
            load_dotenv()

        anthropic = None
        anthropic_opus = None
        if key := os.getenv("Anthropic_API_Key") or os.getenv("ANTHROPIC_API_KEY"):
            anthropic = ProviderConfig(
                api_key=key,
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            )
            anthropic_opus = ProviderConfig(
                api_key=key,
                model="claude-opus-4-6",
            )

        openai = None
        if key := os.getenv("OpenAI_API_Key") or os.getenv("OPENAI_API_KEY"):
            openai = ProviderConfig(
                api_key=key,
                model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
            )

        gemini = None
        if key := os.getenv("GEMINI_API_Key") or os.getenv("GEMINI_API_KEY"):
            gemini = ProviderConfig(
                api_key=key,
                model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
            )

        openai_compat = None
        if key := os.getenv("OPENAI_COMPAT_API_KEY"):
            openai_compat = ProviderConfig(
                api_key=key,
                model=os.getenv("OPENAI_COMPAT_MODEL", "qwen/qwen3.5-397b-a17b"),
                base_url=os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.together.xyz/v1"),
            )

        budget_cap = float(os.getenv("CMSBOT_BUDGET_CAP", "75.0"))
        max_rounds = int(os.getenv("CMSBOT_MAX_ROUNDS", "5"))
        output_dir = Path(os.getenv("CMSBOT_OUTPUT_DIR", "output"))
        unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY")
        xai_key = os.getenv("XAI_API_KEY")

        return cls(
            anthropic=anthropic,
            anthropic_opus=anthropic_opus,
            openai=openai,
            gemini=gemini,
            openai_compat=openai_compat,
            budget_cap=budget_cap,
            max_rounds=max_rounds,
            output_dir=output_dir,
            unsplash_key=unsplash_key,
            xai_key=xai_key,
        )

    def get_provider_config(self, name: str) -> ProviderConfig:
        """Get a provider config by name, raising if not configured."""
        cfg = getattr(self, name, None)
        if cfg is None:
            raise ValueError(
                f"Provider '{name}' not configured. Set the corresponding API key."
            )
        return cfg

    def available_providers(self) -> list[str]:
        return [
            name
            for name in ("anthropic", "anthropic_opus", "openai", "gemini", "openai_compat")
            if getattr(self, name) is not None
        ]
