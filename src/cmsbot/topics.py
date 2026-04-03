"""Topic brief loading and formatting for CMSBOT."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_topic_briefs(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        briefs = data.get("topicBriefs") or data.get("briefs") or []
        if isinstance(briefs, list):
            return [item for item in briefs if isinstance(item, dict)]
    raise ValueError(f"Unsupported topic brief payload: {path}")


def latest_topic_brief_file(output_dir: str | Path) -> Path | None:
    directory = Path(output_dir)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("topic_briefs_*.json"))
    return matches[-1] if matches else None


def merge_topic_briefs(*collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for collection in collections:
        for brief in collection:
            keys = [
                str(brief.get("title_seed") or "").strip().lower(),
                str((brief.get("target_keywords") or [""])[0]).strip().lower(),
            ]
            fingerprint = " | ".join(keys)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(brief)

    merged.sort(
        key=lambda brief: (
            int(brief.get("priority", 999)),
            str(brief.get("cluster", "")),
            str(brief.get("title_seed", "")),
        )
    )
    return merged


def format_topic_brief(brief: dict[str, Any], index: int) -> str:
    title = brief.get("title_seed", "Untitled topic")
    cluster = brief.get("cluster", "general")
    source = brief.get("source", "unknown")
    priority = brief.get("priority", "?")
    keywords = ", ".join(brief.get("target_keywords", [])[:5])
    return (
        f"[{index}] P{priority} [{cluster}] {title}\n"
        f"    source: {source}\n"
        f"    keywords: {keywords or '(none)'}"
    )


def build_cmsbot_command(
    brief: dict[str, Any],
    project_file: str | None = None,
    style: str | None = None,
    output_dir: str | None = None,
) -> str:
    prompt = brief.get("prompt") or brief.get("title_seed") or "Write a blog post"
    parts = ["cmsbot", "generate", json.dumps(prompt)]
    parts.extend(["--type", "blog"])
    if project_file:
        parts.extend(["--project-file", project_file])
    if style:
        parts.extend(["--style", style])
    if output_dir:
        parts.extend(["--output", output_dir])
    return " ".join(parts)
