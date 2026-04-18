"""Project profile loading and context generation for reusable marketing tooling."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_PROFILE: dict[str, Any] = {
    "name": "Example Project",
    "website": "https://example.com",
    "description": "Describe the product, the market, and why it exists.",
    "audience": [
        "Primary customer segment",
        "What they care about",
    ],
    "voice": [
        "Direct",
        "Specific",
        "Credible",
    ],
    "goals": [
        "Grow organic traffic",
        "Turn content into qualified pipeline",
    ],
    "products": [
        "Main product or service",
    ],
    "differentiators": [
        "What makes this project meaningfully different",
    ],
    "content": {
        "ctas": [
            "Book a demo",
            "Start a free trial",
        ],
        "brand_rules": [
            "Prefer concrete examples over abstractions.",
            "Avoid inflated marketing language.",
        ],
        "disallowed_claims": [
            "Do not make unsupported legal, financial, or medical claims.",
        ],
    },
    "seo": {
        "seed_keywords": [],
        "expansion_seeds": [],
        "long_tail_topics": [],
        "question_prefixes": [
            "how to",
            "best way to",
            "what is",
            "why does",
        ],
        "buyer_intent_signals": [
            "best",
            "top",
            "compare",
            "vs",
            "alternative",
            "review",
            "pricing",
            "software",
            "tool",
            "platform",
        ],
        "subreddits": [],
        "competitors": [],
        "relevance_terms": [],
        "blog_path_prefix": "/blog/",
        "blog_api_url": "",
        "gsc_site_url": "",
        "ga4_property_id": "",
        "google_service_account_key_path": "",
        "conversion_event_name": "sign_up",
    },
    "slideshow": {
        "tagline": "",
        "cta_variants": [
            "See how it works",
            "Try it yourself",
        ],
        "voice": "Direct, credible, and conversational.",
        "banned_words": [
            "game-changer",
            "revolutionary",
            "must-have",
            "unlock your potential",
        ],
        "screenshot_dir": "./slideshow/screenshots",
        "screenshot_refs": [],
        "platforms": ["tiktok", "instagram"],
    },
    "content_ops": {
        "pillars": [
            {
                "key": "education",
                "label": "Education",
                "goals": ["traffic", "awareness"],
                "default_formats": ["blog", "thread", "carousel"],
                "topic_seeds": [],
            },
            {
                "key": "product-proof",
                "label": "Product Proof",
                "goals": ["consideration", "conversion"],
                "default_formats": ["blog", "thread", "carousel"],
                "topic_seeds": [],
            },
            {
                "key": "workflow",
                "label": "Workflow",
                "goals": ["activation", "retention"],
                "default_formats": ["blog", "thread", "video_script"],
                "topic_seeds": [],
            },
        ],
        "default_formats": ["blog", "thread", "carousel"],
        "cadence": {
            "timezone": "America/Los_Angeles",
            "planning_horizon_days": 30,
            "default_schedule": [
                {
                    "day_offset": 0,
                    "formats": ["carousel", "thread"],
                },
                {
                    "day_offset": 1,
                    "formats": ["video_script"],
                },
                {
                    "day_offset": 2,
                    "formats": ["blog"],
                },
            ],
        },
        "platforms": {
            "blog": {"enabled": True},
            "x": {"enabled": True},
            "threads": {"enabled": True},
            "instagram": {"enabled": True},
            "linkedin": {"enabled": True},
            "tiktok": {"enabled": True},
        },
        "formats": {
            "carousel": {
                "templates": [
                    "fix-your-stack",
                    "timing-breakdown",
                    "interaction-map",
                    "system-blueprint",
                ],
            },
            "thread": {
                "templates": ["insight-thread", "recap-thread"],
            },
            "video_script": {
                "templates": ["hook-problem-fix", "system-breakdown"],
            },
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def slugify_project_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


@dataclass(frozen=True)
class ProjectProfile:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name") or DEFAULT_PROJECT_PROFILE["name"])

    @property
    def slug(self) -> str:
        raw = self.data.get("slug")
        if raw:
            return slugify_project_name(str(raw))
        return slugify_project_name(self.name)

    @property
    def website(self) -> str:
        return str(self.data.get("website") or "")

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def render_context_markdown(self) -> str:
        content = self.section("content")
        seo = self.section("seo")

        lines = [f"# {self.name}"]

        if self.website:
            lines.append("")
            lines.append(f"Website: {self.website}")

        description = str(self.data.get("description") or "").strip()
        if description:
            lines.append("")
            lines.append("## Product")
            lines.append(description)

        def add_list(title: str, values: list[str]) -> None:
            clean = [str(v).strip() for v in values if str(v).strip()]
            if not clean:
                return
            lines.append("")
            lines.append(f"## {title}")
            lines.extend(f"- {item}" for item in clean)

        add_list("Audience", list(self.data.get("audience", [])))
        add_list("Brand Voice", list(self.data.get("voice", [])))
        add_list("Goals", list(self.data.get("goals", [])))
        add_list("Products", list(self.data.get("products", [])))
        add_list("Differentiators", list(self.data.get("differentiators", [])))
        add_list("Brand Rules", list(content.get("brand_rules", [])))
        add_list("CTAs", list(content.get("ctas", [])))
        add_list("Disallowed Claims", list(content.get("disallowed_claims", [])))

        competitors = seo.get("competitors", [])
        if competitors:
            lines.append("")
            lines.append("## SEO Competitors")
            for competitor in competitors:
                if isinstance(competitor, dict):
                    label = competitor.get("name") or competitor.get("domain") or "Unknown"
                    domain = competitor.get("domain")
                    if domain:
                        lines.append(f"- {label}: {domain}")
                    else:
                        lines.append(f"- {label}")

        return "\n".join(lines).strip() + "\n"

    def summary(self) -> str:
        parts = [self.name]
        if self.website:
            parts.append(self.website)
        description = str(self.data.get("description") or "").strip()
        if description:
            parts.append(description)
        return " | ".join(parts)


def load_project_profile(path: str | Path) -> ProjectProfile:
    profile_path = Path(path)
    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Project profile must be a JSON object: {profile_path}")
    data = _deep_merge(DEFAULT_PROJECT_PROFILE, raw)
    return ProjectProfile(path=profile_path, data=data)


def make_project_profile(name: str) -> dict[str, Any]:
    profile = deepcopy(DEFAULT_PROJECT_PROFILE)
    profile["name"] = name
    profile["slug"] = slugify_project_name(name)
    return profile


def write_project_profile(path: str | Path, data: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return output


def combine_context_text(
    project_context: str | None = None,
    explicit_context: str | None = None,
) -> str | None:
    parts = [part.strip() for part in (project_context, explicit_context) if part and part.strip()]
    if not parts:
        return None
    return "\n\n".join(parts)
