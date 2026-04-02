"""Tone and personality synthesis from a project profile."""

from __future__ import annotations

import json
import re
from typing import Any

from cmsbot.config import Config
from cmsbot.project import ProjectProfile
from cmsbot.providers import build_provider


def _pick_provider(config: Config, preferred: str | None = None) -> str:
    available = config.available_providers()
    if preferred:
        if preferred not in available:
            raise ValueError(
                f"Provider '{preferred}' is not configured. Available: {', '.join(available) or '(none)'}"
            )
        return preferred

    for name in ("anthropic", "openai", "gemini", "openai_compat"):
        if name in available:
            return name

    raise ValueError("No configured providers available for voice synthesis.")


def _extract_json_block(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]

    return json.loads(candidate)


def build_voice_prompt(profile: ProjectProfile, extra_brief: str | None = None) -> str:
    content = profile.section("content")
    seo = profile.section("seo")
    slideshow = profile.section("slideshow")

    prompt = {
        "name": profile.name,
        "website": profile.website,
        "description": profile.data.get("description", ""),
        "audience": profile.data.get("audience", []),
        "goals": profile.data.get("goals", []),
        "products": profile.data.get("products", []),
        "differentiators": profile.data.get("differentiators", []),
        "existing_voice": profile.data.get("voice", []),
        "brand_rules": content.get("brand_rules", []),
        "disallowed_claims": content.get("disallowed_claims", []),
        "cta_examples": content.get("ctas", []),
        "seo_relevance_terms": seo.get("relevance_terms", []),
        "slideshow_voice": slideshow.get("voice", ""),
        "extra_brief": extra_brief or "",
    }
    return json.dumps(prompt, indent=2)


async def synthesize_voice(
    profile: ProjectProfile,
    config: Config,
    preferred_provider: str | None = None,
    extra_brief: str | None = None,
) -> dict[str, Any]:
    provider_name = _pick_provider(config, preferred_provider)
    provider = build_provider(provider_name, config)

    system_prompt = (
        "You are a brand strategist building reusable content operating instructions. "
        "Return strict JSON only."
    )
    user_prompt = f"""
Create a reusable tone and personality profile for this project.

Return JSON with this shape:
{{
  "positioning": "one paragraph",
  "tone_attributes": ["attribute"],
  "personality_flags": [
    {{
      "flag": "short label",
      "guidance": "how the flag should influence content"
    }}
  ],
  "do": ["instruction"],
  "dont": ["instruction"],
  "cta_phrasing": ["example"],
  "hook_patterns": ["example"],
  "brand_metaphors": ["example"]
}}

Requirements:
- Make the voice specific to the project, not generic startup copy.
- Optimize for SEO content, long-form content, and short-form slideshow hooks.
- Keep the flags actionable enough to plug into prompt files.
- Avoid hype terms unless the project itself clearly warrants them.

Project data:
{build_voice_prompt(profile, extra_brief)}
""".strip()

    response = await provider.generate_simple(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.4,
        max_tokens=1800,
    )
    return _extract_json_block(response.content)
