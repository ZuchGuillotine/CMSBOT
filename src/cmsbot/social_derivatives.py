"""Carousel and video-script exports derived from generated source content."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
    update_distribution_manifest,
)


CAROUSEL_SYSTEM = """\
You turn long-form source content into an Instagram or LinkedIn carousel package for a specific brand.

Hard requirements:
- Output ONLY valid JSON.
- Return an object with keys: "section_title", "summary", "slides", "caption", "cta", "visual_direction".
- "slides" must be an array of 4 to 8 objects.
- Each slide object must contain exactly: "index", "title", "body", "asset_note".
- Each slide title and body must be concise and publishable.
- Stay faithful to the source section and do not invent facts.
- Follow the project's voice, brand rules, and disallowed claims.
- Make the first slide hook-driven and the final slide CTA-driven.
- Do not include hashtags unless they appear in the caption.
- Do not add commentary outside the JSON object.
"""

VIDEO_SCRIPT_SYSTEM = """\
You turn long-form source content into a short-form video script package for a specific brand.

Hard requirements:
- Output ONLY valid JSON.
- Return an object with keys: "section_title", "summary", "hook", "target_duration_seconds", "beats", "caption", "cta".
- "beats" must be an array of 4 to 8 objects.
- Each beat object must contain exactly: "index", "narration", "visual", "overlay_text".
- Keep the script faithful to the source and platform-native.
- Follow the project's voice, brand rules, and disallowed claims.
- The hook must be concise and attention-grabbing without sounding spammy.
- "overlay_text" should be short enough for on-screen use.
- Do not add commentary outside the JSON object.
"""


def export_carousel(
    output_dir: str | Path,
    *,
    project_file: str | Path,
    env_file: str | Path | None = None,
    context_file: str | Path | None = None,
    section: int | None = None,
    section_title: str | None = None,
    provider_name: str | None = None,
    platform: str = "instagram",
    save_dir: str | Path | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    source_path, destination_dir = resolve_distribution_dir(
        output_dir,
        platform=platform,
        save_dir=save_dir,
        legacy_name="carousel",
    )
    _, provider = build_provider_from_config(env_file=env_file, provider_name=provider_name)
    project = load_source_project(project_file)
    extra_context = load_source_context(context_file)
    source = load_blog_source(source_path)
    selected = select_section(source["sections"], section=section, section_title=section_title)

    prompt = _build_carousel_prompt(
        project=project,
        extra_context=extra_context,
        article_title=source["title"],
        section_heading=selected["heading"],
        section_body=selected["body"],
        full_article_summary=source["summary"],
        platform=platform,
    )
    payload = _parse_carousel_payload(_run_generation(provider, CAROUSEL_SYSTEM, prompt))
    artifact = _build_carousel_artifact(payload, source, selected, project, platform)

    json_path = destination_dir / "carousel.json"
    md_path = destination_dir / "carousel.md"
    caption_path = destination_dir / "captions.md"
    json_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_render_carousel_markdown(artifact), encoding="utf-8")
    caption_path.write_text(_render_carousel_caption(artifact), encoding="utf-8")
    update_distribution_manifest(
        output_dir,
        platform=platform,
        artifact_paths={
            "carousel_json": json_path,
            "carousel_markdown": md_path,
            "carousel_caption": caption_path,
        },
    )
    return artifact, json_path, md_path, caption_path


def export_video_script(
    output_dir: str | Path,
    *,
    project_file: str | Path,
    env_file: str | Path | None = None,
    context_file: str | Path | None = None,
    section: int | None = None,
    section_title: str | None = None,
    provider_name: str | None = None,
    platform: str = "tiktok",
    save_dir: str | Path | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    source_path, destination_dir = resolve_distribution_dir(
        output_dir,
        platform=platform,
        save_dir=save_dir,
        legacy_name="video",
    )
    _, provider = build_provider_from_config(env_file=env_file, provider_name=provider_name)
    project = load_source_project(project_file)
    extra_context = load_source_context(context_file)
    source = load_blog_source(source_path)
    selected = select_section(source["sections"], section=section, section_title=section_title)

    prompt = _build_video_prompt(
        project=project,
        extra_context=extra_context,
        article_title=source["title"],
        section_heading=selected["heading"],
        section_body=selected["body"],
        full_article_summary=source["summary"],
        platform=platform,
    )
    payload = _parse_video_payload(_run_generation(provider, VIDEO_SCRIPT_SYSTEM, prompt))
    artifact = _build_video_artifact(payload, source, selected, project, platform)

    json_path = destination_dir / "video_script.json"
    script_path = destination_dir / "script.md"
    storyboard_path = destination_dir / "storyboard.json"
    json_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    script_path.write_text(_render_video_markdown(artifact), encoding="utf-8")
    storyboard_path.write_text(
        json.dumps(
            {
                "platform": platform,
                "hook": artifact["hook"],
                "beats": artifact["beats"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    update_distribution_manifest(
        output_dir,
        platform=platform,
        artifact_paths={
            "video_script_json": json_path,
            "video_script_markdown": script_path,
            "storyboard_json": storyboard_path,
        },
    )
    return artifact, json_path, script_path, storyboard_path


def _run_generation(provider, system_prompt: str, prompt: str) -> str:
    from cmsbot.repurpose import run_json_generation

    response = run_json_generation(provider, system_prompt=system_prompt, prompt=prompt, max_tokens=2600)
    return response.content


def _build_carousel_prompt(
    *,
    project: ProjectProfile,
    extra_context: str | None,
    article_title: str,
    section_heading: str,
    section_body: str,
    full_article_summary: str,
    platform: str,
) -> str:
    return build_repurpose_prompt(
        project=project,
        extra_context=extra_context,
        article_title=article_title,
        section_heading=section_heading,
        section_body=section_body,
        full_article_summary=full_article_summary,
        task_instructions=(
            f"Create a {platform.title()} carousel package from this section. "
            "Structure it so a creator can hand the slides directly to design and posting."
        ),
    )


def _build_video_prompt(
    *,
    project: ProjectProfile,
    extra_context: str | None,
    article_title: str,
    section_heading: str,
    section_body: str,
    full_article_summary: str,
    platform: str,
) -> str:
    return build_repurpose_prompt(
        project=project,
        extra_context=extra_context,
        article_title=article_title,
        section_heading=section_heading,
        section_body=section_body,
        full_article_summary=full_article_summary,
        task_instructions=(
            f"Create a short-form {platform} video script package from this section. "
            "Write it so a creator can record or edit it without additional rewriting."
        ),
    )


def _parse_carousel_payload(text: str) -> dict[str, Any]:
    payload = parse_json_object(
        text,
        required_keys=["slides", "caption", "cta", "visual_direction"],
    )
    slides = payload.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("Carousel response did not include slides")
    return payload


def _parse_video_payload(text: str) -> dict[str, Any]:
    payload = parse_json_object(
        text,
        required_keys=["hook", "beats", "caption", "cta"],
    )
    beats = payload.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("Video-script response did not include beats")
    return payload


def _build_carousel_artifact(
    payload: dict[str, Any],
    source: dict[str, Any],
    selected: dict[str, Any],
    project: ProjectProfile,
    platform: str,
) -> dict[str, Any]:
    slides = _normalize_slides(payload.get("slides", []))
    return {
        "platform": platform,
        "format": "carousel",
        "project": project.name,
        "source": {
            "output_dir": str(source["source_dir"].resolve()),
            "article_title": source["title"],
            "section_title": selected["heading"],
            "section_index": selected["index"],
        },
        "summary": str(payload.get("summary", "")).strip(),
        "cta": str(payload.get("cta", "")).strip(),
        "caption": _normalize_caption(str(payload.get("caption", "")).strip()),
        "visual_direction": str(payload.get("visual_direction", "")).strip(),
        "slides": slides,
    }


def _build_video_artifact(
    payload: dict[str, Any],
    source: dict[str, Any],
    selected: dict[str, Any],
    project: ProjectProfile,
    platform: str,
) -> dict[str, Any]:
    beats = _normalize_beats(payload.get("beats", []))
    duration = int(payload.get("target_duration_seconds") or (len(beats) * 4))
    return {
        "platform": platform,
        "format": "video_script",
        "project": project.name,
        "source": {
            "output_dir": str(source["source_dir"].resolve()),
            "article_title": source["title"],
            "section_title": selected["heading"],
            "section_index": selected["index"],
        },
        "summary": str(payload.get("summary", "")).strip(),
        "hook": str(payload.get("hook", "")).strip(),
        "cta": str(payload.get("cta", "")).strip(),
        "caption": _normalize_caption(str(payload.get("caption", "")).strip()),
        "target_duration_seconds": max(10, min(duration, 90)),
        "beats": beats,
    }


def _normalize_slides(slides: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, slide in enumerate(slides[:8], start=1):
        if not isinstance(slide, dict):
            raise ValueError("Each slide must be an object")
        title = _clean_line(str(slide.get("title", "")))
        body = _clean_line(str(slide.get("body", "")))
        asset_note = _clean_line(str(slide.get("asset_note", "")))
        if not title or not body:
            raise ValueError(f"Slide {idx} is missing title or body")
        normalized.append(
            {
                "index": idx,
                "title": title[:90],
                "body": body[:280],
                "asset_note": asset_note[:180],
            }
        )
    if len(normalized) < 4:
        raise ValueError("Carousel must contain at least 4 slides")
    return normalized


def _normalize_beats(beats: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, beat in enumerate(beats[:8], start=1):
        if not isinstance(beat, dict):
            raise ValueError("Each beat must be an object")
        narration = _clean_line(str(beat.get("narration", "")))
        visual = _clean_line(str(beat.get("visual", "")))
        overlay_text = _clean_line(str(beat.get("overlay_text", "")))
        if not narration or not visual:
            raise ValueError(f"Beat {idx} is missing narration or visual")
        normalized.append(
            {
                "index": idx,
                "narration": narration[:220],
                "visual": visual[:220],
                "overlay_text": overlay_text[:80],
            }
        )
    if len(normalized) < 4:
        raise ValueError("Video script must contain at least 4 beats")
    return normalized


def _normalize_caption(text: str) -> str:
    text = _clean_line(text)
    return text[:600]


def _clean_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _render_carousel_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['project']} {payload['platform'].title()} Carousel",
        "",
        f"Source article: {payload['source']['article_title']}",
        f"Source section: {payload['source']['section_title']}",
    ]
    if payload.get("summary"):
        lines.extend(["", f"Summary: {payload['summary']}"])
    if payload.get("visual_direction"):
        lines.extend(["", f"Visual direction: {payload['visual_direction']}"])

    for slide in payload["slides"]:
        lines.extend(
            [
                "",
                f"## Slide {slide['index']}",
                "",
                f"Title: {slide['title']}",
                f"Body: {slide['body']}",
            ]
        )
        if slide.get("asset_note"):
            lines.append(f"Asset note: {slide['asset_note']}")

    if payload.get("cta"):
        lines.extend(["", f"CTA: {payload['cta']}"])
    return "\n".join(lines).strip() + "\n"


def _render_carousel_caption(payload: dict[str, Any]) -> str:
    lines = [f"# {payload['project']} {payload['platform'].title()} Caption", ""]
    if payload.get("caption"):
        lines.append(payload["caption"])
    if payload.get("cta"):
        lines.extend(["", f"CTA: {payload['cta']}"])
    return "\n".join(lines).strip() + "\n"


def _render_video_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['project']} {payload['platform'].title()} Video Script",
        "",
        f"Source article: {payload['source']['article_title']}",
        f"Source section: {payload['source']['section_title']}",
        f"Target duration: {payload['target_duration_seconds']}s",
    ]
    if payload.get("hook"):
        lines.extend(["", f"Hook: {payload['hook']}"])
    if payload.get("summary"):
        lines.extend(["", f"Summary: {payload['summary']}"])

    for beat in payload["beats"]:
        lines.extend(
            [
                "",
                f"## Beat {beat['index']}",
                "",
                f"Narration: {beat['narration']}",
                f"Visual: {beat['visual']}",
            ]
        )
        if beat.get("overlay_text"):
            lines.append(f"Overlay: {beat['overlay_text']}")

    if payload.get("caption"):
        lines.extend(["", "## Caption", "", payload["caption"]])
    if payload.get("cta"):
        lines.extend(["", f"CTA: {payload['cta']}"])
    return "\n".join(lines).strip() + "\n"
