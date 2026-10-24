"""Configuration loading from environment variables and .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only in minimal environments
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


@dataclass
class ProviderConfig:
    api_key: str
    model: str
    base_url: str | None = None


# Order used when a role's preferred provider has no API key configured.
PROVIDER_FALLBACK_ORDER = ("anthropic", "openai", "gemini", "openai_compat", "anthropic_opus")

ROLE_NAMES = (
    "author", "strawman", "rewriter", "synthesizer", "image_director",
    "planner", "drafter", "restructurer", "auditor",
)


@dataclass
class RoleAssignment:
    """Preferred provider for each pipeline role.

    Each can be overridden with CMSBOT_ROLE_<ROLE> (e.g. CMSBOT_ROLE_AUTHOR=anthropic,
    CMSBOT_ROLE_JUDGES=anthropic_opus,openai). Unconfigured providers fall back
    in PROVIDER_FALLBACK_ORDER, so the pipeline runs with any single API key.
    """
    author: str = "openai_compat"
    strawman: str = "openai_compat"
    rewriter: str = "anthropic"
    synthesizer: str = "openai"
    judges: list[str] = field(default_factory=lambda: ["anthropic_opus", "openai", "gemini"])
    image_director: str = "openai"
    # Multipass stages. The first test run (2026-10-23) showed a small auditor
    # model "keeping" the strongest AI tells and a cheap drafter writing choppy
    # fragments, so plan and draft run on Opus and the audit on Sonnet.
    planner: str = "anthropic_opus"
    drafter: str = "anthropic_opus"
    restructurer: str = "anthropic"
    auditor: str = "anthropic"

    @classmethod
    def from_env(cls) -> RoleAssignment:
        roles = cls()
        for role in ROLE_NAMES:
            if value := os.getenv(f"CMSBOT_ROLE_{role.upper()}"):
                setattr(roles, role, value.strip())
        if value := os.getenv("CMSBOT_ROLE_JUDGES"):
            roles.judges = [v.strip() for v in value.split(",") if v.strip()]
        return roles


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
    openai_image_model: str = "gpt-image-2"

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
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            )
            anthropic_opus = ProviderConfig(
                api_key=key,
                model=os.getenv("ANTHROPIC_OPUS_MODEL", "claude-opus-5-5"),
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
        openai_image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

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
            openai_image_model=openai_image_model,
            roles=RoleAssignment.from_env(),
        )

    def get_provider_config(self, name: str) -> ProviderConfig:
        """Get a provider config by name, raising if not configured."""
        cfg = getattr(self, name, None)
        if cfg is None:
            raise ValueError(
                f"Provider '{name}' not configured. Set the corresponding API key."
            )
        return cfg

    def provider_for(self, role: str) -> str:
        """Configured provider for a role, falling back when the preferred one has no key."""
        preferred = getattr(self.roles, role)
        if getattr(self, preferred, None) is not None:
            return preferred
        for name in PROVIDER_FALLBACK_ORDER:
            if getattr(self, name) is not None:
                return name
        raise ValueError(
            f"No LLM provider configured for role '{role}'. Set at least one of: "
            "ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, OPENAI_COMPAT_API_KEY"
        )

    def judge_providers(self) -> list[str]:
        """Up to three judges, preferring distinct configured providers."""
        available = self.available_providers()
        if not available:
            raise ValueError("No LLM provider configured for the judge panel.")
        chosen = [name for name in self.roles.judges if name in available]
        for name in PROVIDER_FALLBACK_ORDER:
            if len(chosen) >= 3:
                break
            if name in available and name not in chosen:
                chosen.append(name)
        # With fewer than three providers, repeat them so Borda still has three votes
        while len(chosen) < 3:
            chosen.append(chosen[len(chosen) % len(chosen)])
        return chosen[:3]

    def available_providers(self) -> list[str]:
        return [
            name
            for name in ("anthropic", "anthropic_opus", "openai", "gemini", "openai_compat")
            if getattr(self, name) is not None
        ]
