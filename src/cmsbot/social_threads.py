"""Threads/X export helpers for repurposing generated CMSBOT blog posts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cmsbot.blog_draft import extract_body_html, extract_title
from cmsbot.config import Config
from cmsbot.context import load_context_file
from cmsbot.models import ContentType
from cmsbot.pipeline import AutoreasonPipeline
from cmsbot.project import ProjectProfile, combine_context_text, load_project_profile
from cmsbot.providers import build_provider


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
    output_path = Path(output_dir)
    thread_dir = Path(save_dir) if save_dir else (output_path / "threads")
    thread_dir.mkdir(parents=True, exist_ok=True)

    config = Config.from_env(env_file)
    project = load_project_profile(project_file)
    extra_context = load_context_file(context_file) if context_file else None

    source = _load_blog_source(output_path)
    selected = _select_section(
        source["sections"],
        section=section,
        section_title=section_title,
    )

    available = config.available_providers()
    if not available:
        raise ValueError("No API providers configured. Set at least one provider in .env")

    chosen_provider = provider_name or _pick_provider_name(config)
    provider = build_provider(chosen_provider, config)

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
        output_dir=output_path,
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
            "output_dir": str(output_path.resolve()),
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
    return artifact, json_path, md_path


def _run_thread_generation(provider, prompt: str):
    import asyncio

    return asyncio.run(
        provider.generate_simple(
            user_prompt=prompt,
            system_prompt=THREAD_SYSTEM,
            max_tokens=1800,
            temperature=0.6,
        )
    )


def _pick_provider_name(config: Config) -> str:
    preferred = config.roles.author
    if preferred in config.available_providers():
        return preferred
    return config.available_providers()[0]


def _load_blog_source(output_dir: Path) -> dict[str, Any]:
    markdown_path = output_dir / "content.md"
    html_path = output_dir / "index.html"

    content = ""
    title = "Untitled"
    if markdown_path.exists():
        content = markdown_path.read_text(encoding="utf-8").strip()
        title = _extract_markdown_title(content)
    elif html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        title = extract_title(html)
        content = _html_to_markdownish(extract_body_html(html))
    else:
        raise FileNotFoundError(f"No content.md or index.html found in {output_dir}")

    sections = _split_markdown_sections(content)
    if not sections:
        body = "\n".join(line.strip() for line in content.splitlines() if line.strip())
        sections = [{"index": 1, "heading": title, "body": body}]

    summary = ""
    for entry in sections:
        if len(entry["body"]) > 80:
            summary = entry["body"][:400]
            break

    return {
        "title": title,
        "content": content,
        "sections": sections,
        "summary": summary,
    }


def _extract_markdown_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Untitled"


def _split_markdown_sections(content: str) -> list[dict[str, Any]]:
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


def _html_to_markdownish(html: str) -> str:
    text = html
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", lambda m: "# " + _strip_tags(m.group(1)), text, flags=re.I | re.S)
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", lambda m: "\n## " + _strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", lambda m: "\n### " + _strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: "- " + _strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<p[^>]*>(.*?)</p>", lambda m: _strip_tags(m.group(1)) + "\n\n", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_tags(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _select_section(
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


def _build_thread_prompt(
    *,
    project: ProjectProfile,
    extra_context: str | None,
    article_title: str,
    section_heading: str,
    section_body: str,
    full_article_summary: str,
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

Write a Threads/X thread that repurposes this section for the project's audience.
The thread should feel native to short-form social, but remain faithful to the source.
"""


def _parse_thread_payload(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    candidate = match.group(0) if match else text.strip()
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Thread response was not a JSON object")
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
