"""Threads/X export helpers for repurposing generated CMSBOT blog posts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cmsbot.config import Config
from cmsbot.models import ContentType
from cmsbot.project import ProjectProfile
from cmsbot.repurpose import (
    build_provider_from_config,
    build_repurpose_prompt,
    load_blog_source,
    load_source_context,
    load_source_project,
    parse_json_object,
    resolve_distribution_dir,
    select_section,
    split_markdown_sections,
    update_distribution_manifest,
)


THREAD_SYSTEM = """\
You turn long-form blog content into a compact Threads/X thread for a specific brand.

Hard requirements:
- Output ONLY valid JSON.
- Return an object with keys: "section_title", "summary", "posts".
- "posts" must be an array of 1 to 5 objects.
- Each post object must contain exactly: "index", "text".
- Each post must read like a real Threads/X post and stay within 280 characters.
- Never use hashtags.
- Always reference the project naturally by name at least once in the thread.
- Strictly follow the project's brand voice, brand rules, and disallowed claims.
- Summarize and repurpose the source section faithfully. Do not invent facts.
- Avoid repetitive comparative language like repeated "better than", "unlike", "instead of", "vs", or "compared to" constructions.
- Tastefully adapt the long-form material into a coherent short thread with logical flow.
- Do not include numbering like "1/" unless it helps clarity inside the post text.
- Do not add commentary outside the JSON object.
"""


def export_social_thread(
    output_dir: str | Path,
    *,
    project_file: str | Path,
    env_file: str | Path | None = None,
    context_file: str | Path | None = None,
    section: int | None = None,
    section_title: str | None = None,
    provider_name: str | None = None,
    image_mode: str = "auto",
    save_dir: str | Path | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Generate and write thread artifacts from a generated blog output directory."""
    source_path, thread_dir = resolve_distribution_dir(
        output_dir,
        platform="x",
        save_dir=save_dir,
        legacy_name="threads",
    )
    config, provider = build_provider_from_config(env_file=env_file, provider_name=provider_name)
    project = load_source_project(project_file)
    extra_context = load_source_context(context_file)

    source = load_blog_source(source_path)
    selected = select_section(
        source["sections"],
        section=section,
        section_title=section_title,
    )

    prompt = _build_thread_prompt(
        project=project,
        extra_context=extra_context,
        article_title=source["title"],
        section_heading=selected["heading"],
        section_body=selected["body"],
        full_article_summary=source["summary"],
    )

    response = _run_thread_generation(provider, prompt)
    payload = _parse_thread_payload(response.content)
    posts = _normalize_posts(payload["posts"], project_name=project.name)

    media_paths = _resolve_media_paths(
        output_dir=source_path,
        thread_dir=thread_dir,
        config=config,
        section_index=selected["index"],
        image_mode=image_mode,
        project_name=project.name,
        article_title=source["title"],
        thread_posts=posts,
        section_heading=selected["heading"],
        section_body=selected["body"],
    )

    artifact = {
        "platform": "threads_x",
        "project": project.name,
        "source": {
            "output_dir": str(source_path.resolve()),
            "article_title": source["title"],
            "section_title": selected["heading"],
            "section_index": selected["index"],
        },
        "summary": str(payload.get("summary", "")).strip(),
        "posts": [
            {
                "position": idx,
                "text": text,
                "media_paths": media_paths if idx == 1 and media_paths else [],
            }
            for idx, text in enumerate(posts, start=1)
        ],
    }

    json_path = thread_dir / "thread.json"
    md_path = thread_dir / "thread.md"
    json_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(artifact), encoding="utf-8")
    update_distribution_manifest(
        output_dir,
        platform="x",
        artifact_paths={"thread_json": json_path, "thread_markdown": md_path},
    )
    return artifact, json_path, md_path


def _run_thread_generation(provider, prompt: str):
    from cmsbot.repurpose import run_json_generation

    return run_json_generation(
        provider,
        system_prompt=THREAD_SYSTEM,
        prompt=prompt,
        max_tokens=1800,
    )


def _build_thread_prompt(
    *,
    project: ProjectProfile,
    extra_context: str | None,
    article_title: str,
    section_heading: str,
    section_body: str,
    full_article_summary: str,
) -> str:
    return build_repurpose_prompt(
        project=project,
        extra_context=extra_context,
        article_title=article_title,
        section_heading=section_heading,
        section_body=section_body,
        full_article_summary=full_article_summary,
        task_instructions=(
            "Write a Threads/X thread that repurposes this section for the project's audience. "
            "The thread should feel native to short-form social, but remain faithful to the source."
        ),
    )


def _parse_thread_payload(text: str) -> dict[str, Any]:
    payload = parse_json_object(text, required_keys=["posts"])
    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts:
        raise ValueError("Thread response did not include posts")
    return payload


def _normalize_posts(posts: list[Any], *, project_name: str) -> list[str]:
    normalized: list[str] = []
    for idx, post in enumerate(posts[:5], start=1):
        if not isinstance(post, dict):
            raise ValueError("Each post must be an object")
        text = str(post.get("text", "")).strip()
        if not text:
            raise ValueError("Post text cannot be empty")
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"#\w+", "", text).strip()
        if len(text) > 280:
            raise ValueError(f"Post {idx} exceeds 280 characters")
        normalized.append(text)
    if not normalized:
        raise ValueError("No usable posts returned")
    if not any(project_name.lower() in post.lower() for post in normalized):
        normalized[0] = _inject_project_reference(normalized[0], project_name)
    return normalized


def _inject_project_reference(text: str, project_name: str) -> str:
    reference = f"{project_name}: "
    if len(reference) + len(text) <= 280:
        return reference + text

    reserved = len(reference)
    trimmed = text[: 280 - reserved].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    trimmed = trimmed.rstrip(".,;: ")
    return reference + trimmed


def _resolve_media_paths(
    *,
    output_dir: Path,
    thread_dir: Path,
    config: Config,
    section_index: int,
    image_mode: str,
    project_name: str,
    article_title: str,
    thread_posts: list[str],
    section_heading: str,
    section_body: str,
) -> list[str]:
    mode = image_mode.lower().strip()
    if mode not in {"auto", "reuse", "generate", "none"}:
        raise ValueError("image_mode must be one of: auto, reuse, generate, none")

    if mode in {"auto", "reuse"}:
        existing = _find_existing_media(output_dir, section_index)
        if existing:
            return [str(path.resolve()) for path in existing]
        if mode == "reuse":
            return []

    if mode == "none":
        return []

    from cmsbot.pipeline import AutoreasonPipeline

    pipeline = AutoreasonPipeline(config)
    pipeline.set_logger(lambda msg: None)
    pipeline._build_agents()

    thread_text = "\n\n".join(thread_posts)
    task_prompt = (
        f"Create a Threads/X visual for {project_name} based on the article "
        f"'{article_title}' and the section '{section_heading}'."
    )

    import asyncio

    images, _ = asyncio.run(
        pipeline.image_director.generate_images(
            task_prompt=task_prompt,
            final_content=thread_text + "\n\n" + section_body,
            content_type=ContentType.SOCIAL,
            output_dir=thread_dir,
            log_fn=lambda msg: None,
        )
    )
    return [
        str((thread_dir / image.file_path).resolve())
        for image in images
        if image.file_path
    ]


def _find_existing_media(output_dir: Path, section_index: int) -> list[Path]:
    images_dir = output_dir / "images"
    if not images_dir.is_dir():
        return []

    matches: list[Path] = []
    for stem in (f"section-{section_index}", "hero"):
        for suffix in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
            candidate = images_dir / f"{stem}{suffix}"
            if candidate.exists():
                matches.append(candidate)
                break
        if matches:
            break
    return matches


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['project']} Threads/X Thread",
        "",
        f"Source article: {payload['source']['article_title']}",
        f"Source section: {payload['source']['section_title']}",
    ]

    summary = str(payload.get("summary") or "").strip()
    if summary:
        lines.extend(["", f"Summary: {summary}"])

    for post in payload["posts"]:
        lines.extend(["", f"## Post {post['position']}", "", post["text"]])
        media_paths = post.get("media_paths") or []
        if media_paths:
            lines.append("")
            lines.append("Media:")
            for path in media_paths:
                lines.append(f"- {path}")

    return "\n".join(lines).strip() + "\n"


__all__ = [
    "export_social_thread",
    "_find_existing_media",
    "_normalize_posts",
    "_select_section",
    "_split_markdown_sections",
]


def _split_markdown_sections(content: str) -> list[dict[str, Any]]:
    return split_markdown_sections(content)


def _select_section(
    sections: list[dict[str, Any]],
    *,
    section: int | None,
    section_title: str | None,
) -> dict[str, Any]:
    return select_section(sections, section=section, section_title=section_title)
