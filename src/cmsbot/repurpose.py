"""Shared helpers for deriving platform-ready assets from generated source content."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from cmsbot.blog_draft import extract_body_html, extract_title
from cmsbot.config import Config
from cmsbot.content_ops import resolve_asset_set_root, resolve_source_dir
from cmsbot.context import load_context_file
from cmsbot.project import ProjectProfile, combine_context_text, load_project_profile
from cmsbot.providers import build_provider


def load_source_project(project_file: str | Path) -> ProjectProfile:
    return load_project_profile(project_file)


def load_source_context(context_file: str | Path | None) -> str | None:
    return load_context_file(context_file) if context_file else None


def pick_provider_name(config: Config) -> str:
    preferred = config.roles.author
    if preferred in config.available_providers():
        return preferred
    return config.available_providers()[0]


def run_json_generation(provider, *, system_prompt: str, prompt: str, max_tokens: int = 2400):
    return asyncio.run(
        provider.generate_simple(
            user_prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=0.6,
        )
    )


def load_blog_source(output_dir: str | Path) -> dict[str, Any]:
    source_dir = resolve_source_dir(output_dir)
    markdown_path = source_dir / "content.md"
    html_path = source_dir / "index.html"

    content = ""
    title = "Untitled"
    if markdown_path.exists():
        content = markdown_path.read_text(encoding="utf-8").strip()
        title = extract_markdown_title(content)
    elif html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        title = extract_title(html)
        content = html_to_markdownish(extract_body_html(html))
    else:
        raise FileNotFoundError(f"No content.md or index.html found in {source_dir}")

    sections = split_markdown_sections(content)
    if not sections:
        body = "\n".join(line.strip() for line in content.splitlines() if line.strip())
        sections = [{"index": 1, "heading": title, "body": body}]

    summary = ""
    for entry in sections:
        if len(entry["body"]) > 80:
            summary = entry["body"][:400]
            break

    return {
        "source_dir": source_dir,
        "asset_root": resolve_asset_set_root(source_dir),
        "title": title,
        "content": content,
        "sections": sections,
        "summary": summary,
    }


def extract_markdown_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Untitled"


def split_markdown_sections(content: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("## "):
            if current_heading is not None:
                sections.append(
                    {
                        "index": len(sections) + 1,
                        "heading": current_heading,
                        "body": "\n".join(part for part in current_body if part.strip()).strip(),
                    }
                )
            current_heading = line.strip()[3:].strip()
            current_body = []
            continue
        if current_heading is not None:
            current_body.append(line)

    if current_heading is not None:
        sections.append(
            {
                "index": len(sections) + 1,
                "heading": current_heading,
                "body": "\n".join(part for part in current_body if part.strip()).strip(),
            }
        )

    return [section for section in sections if section["body"]]


def html_to_markdownish(html: str) -> str:
    text = html
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", lambda m: "# " + strip_tags(m.group(1)), text, flags=re.I | re.S)
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", lambda m: "\n## " + strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", lambda m: "\n### " + strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: "- " + strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<p[^>]*>(.*?)</p>", lambda m: strip_tags(m.group(1)) + "\n\n", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_tags(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def select_section(
    sections: list[dict[str, Any]],
    *,
    section: int | None,
    section_title: str | None,
) -> dict[str, Any]:
    if not sections:
        raise ValueError("No sections found in source content")

    if section_title:
        wanted = section_title.strip().lower()
        for entry in sections:
            if entry["heading"].strip().lower() == wanted:
                return entry
        raise ValueError(f"Section title not found: {section_title}")

    if section is not None:
        for entry in sections:
            if entry["index"] == section:
                return entry
        raise ValueError(f"Section index out of range: {section}")

    for entry in sections:
        if len(entry["body"]) >= 250:
            return entry
    return sections[0]


def build_repurpose_prompt(
    *,
    project: ProjectProfile,
    extra_context: str | None,
    article_title: str,
    section_heading: str,
    section_body: str,
    full_article_summary: str,
    task_instructions: str,
) -> str:
    brand_context = combine_context_text(project.render_context_markdown(), extra_context)
    return f"""\
PROJECT NAME:
{project.name}

BRAND CONTEXT:
{brand_context or "Use a direct, credible, project-specific tone."}

ARTICLE TITLE:
{article_title}

SECTION TO REPURPOSE:
{section_heading}

SECTION CONTENT:
{section_body}

ARTICLE CONTEXT:
{full_article_summary}

TASK:
{task_instructions}
"""


def parse_json_object(text: str, *, required_keys: list[str] | None = None) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    candidate = match.group(0) if match else text.strip()
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Response was not a JSON object")
    if required_keys:
        for key in required_keys:
            if key not in payload:
                raise ValueError(f"Response did not include '{key}'")
    return payload


def resolve_distribution_dir(
    output_dir: str | Path,
    *,
    platform: str,
    save_dir: str | Path | None = None,
    legacy_name: str,
) -> tuple[Path, Path]:
    source_path = resolve_source_dir(output_dir)
    asset_root = resolve_asset_set_root(output_dir)
    default_dir = (
        asset_root / "distribution" / platform
        if asset_root != source_path
        else source_path / legacy_name
    )
    target_dir = Path(save_dir) if save_dir else default_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    return source_path, target_dir


def update_distribution_manifest(
    output_dir: str | Path,
    *,
    platform: str,
    artifact_paths: dict[str, Path],
) -> None:
    asset_root = resolve_asset_set_root(output_dir)
    manifest_path = asset_root / "manifest.json"
    if not manifest_path.exists():
        return

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    distribution = payload.setdefault("distribution_artifacts", {})
    platform_payload = distribution.setdefault(platform, {})
    for name, path in artifact_paths.items():
        try:
            platform_payload[name] = str(path.relative_to(asset_root))
        except ValueError:
            platform_payload[name] = str(path)
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_provider_from_config(
    *,
    env_file: str | Path | None,
    provider_name: str | None,
):
    config = Config.from_env(env_file)
    available = config.available_providers()
    if not available:
        raise ValueError("No API providers configured. Set at least one provider in .env")
    chosen_provider = provider_name or pick_provider_name(config)
    provider = build_provider(chosen_provider, config)
    return config, provider
