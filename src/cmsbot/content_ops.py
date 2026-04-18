"""Planning and output-layout helpers for recurring content operations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


DEFAULT_FORMAT_PLATFORM = {
    "blog": "blog",
    "thread": "x",
    "carousel": "instagram",
    "video_script": "tiktok",
}

DEFAULT_FORMAT_TIME = {
    "blog": "09:00",
    "thread": "11:00",
    "carousel": "09:00",
    "video_script": "09:00",
}


@dataclass(frozen=True)
class GenerateLayout:
    asset_set_id: str
    layout: str
    asset_set_dir: Path
    source_dir: Path


def slugify_token(text: str, *, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if max_length > 0:
        slug = slug[:max_length].strip("-")
    return slug or "content"


def resolve_source_dir(output_dir: str | Path) -> Path:
    output_path = Path(output_dir)
    source_dir = output_path / "source"

    if (output_path / "content.md").exists() or (output_path / "index.html").exists():
        return output_path
    if (source_dir / "content.md").exists() or (source_dir / "index.html").exists():
        return source_dir
    return output_path


def resolve_asset_set_root(output_dir: str | Path) -> Path:
    source_dir = resolve_source_dir(output_dir)
    if source_dir.name == "source":
        return source_dir.parent
    return source_dir


def prepare_generate_layout(
    output_root: str | Path,
    prompt: str,
    *,
    requested_output_dir: str | Path | None = None,
    run_date: date | None = None,
) -> GenerateLayout:
    if requested_output_dir is not None:
        source_dir = Path(requested_output_dir)
        return GenerateLayout(
            asset_set_id=slugify_token(source_dir.name),
            layout="legacy",
            asset_set_dir=source_dir,
            source_dir=source_dir,
        )

    run_date = run_date or date.today()
    base_id = f"{run_date.isoformat()}-{slugify_token(prompt, max_length=48)}"
    base_dir = (
        Path(output_root)
        / "asset-sets"
        / run_date.strftime("%Y")
        / run_date.strftime("%Y-%m")
        / base_id
    )
    asset_set_dir = _ensure_unique_dir(base_dir)
    return GenerateLayout(
        asset_set_id=asset_set_dir.name,
        layout="asset_set",
        asset_set_dir=asset_set_dir,
        source_dir=asset_set_dir / "source",
    )


def initialize_generate_layout(layout: GenerateLayout) -> None:
    layout.source_dir.mkdir(parents=True, exist_ok=True)
    if layout.layout != "asset_set":
        return

    for extra_dir in (
        layout.asset_set_dir / "distribution",
        layout.asset_set_dir / "analytics",
        layout.asset_set_dir / "media",
        layout.asset_set_dir / "media" / "review",
    ):
        extra_dir.mkdir(parents=True, exist_ok=True)


def write_source_brief(
    layout: GenerateLayout,
    *,
    prompt: str,
    task_prompt: str,
    content_type: str,
    project_name: str | None,
    project_slug: str | None,
    project_file: str | None,
    context_file: str | None,
    style: str | None,
) -> dict[str, Path]:
    prompt_path = layout.source_dir / "prompt.md"
    prompt_path.write_text(task_prompt.strip() + "\n", encoding="utf-8")

    brief_path = layout.source_dir / "brief.json"
    brief_payload = {
        "asset_set_id": layout.asset_set_id,
        "created_at": datetime.now().isoformat(),
        "prompt": prompt,
        "content_type": content_type,
        "project": {
            "name": project_name,
            "slug": project_slug,
            "project_file": project_file,
        },
        "context_file": context_file,
        "style": style,
    }
    brief_path.write_text(json.dumps(brief_payload, indent=2) + "\n", encoding="utf-8")
    return {"prompt": prompt_path, "brief": brief_path}


def write_asset_manifest(
    layout: GenerateLayout,
    *,
    prompt: str,
    task_prompt: str,
    content_type: str,
    project_name: str | None,
    project_slug: str | None,
    project_file: str | None,
    context_file: str | None,
    style: str | None,
    pipeline_result: Any,
    budget_summary: dict[str, Any],
) -> Path:
    root = layout.asset_set_dir
    source_root = layout.source_dir

    source_artifacts: dict[str, str] = {}
    for name, rel_path in (
        ("brief", source_root / "brief.json"),
        ("prompt", source_root / "prompt.md"),
        ("content_markdown", source_root / "content.md"),
        ("content_html", source_root / "index.html"),
        ("images_dir", source_root / "images"),
    ):
        if rel_path.exists():
            source_artifacts[name] = str(rel_path.relative_to(root))

    manifest = {
        "asset_set_id": layout.asset_set_id,
        "layout": layout.layout,
        "created_at": datetime.now().isoformat(),
        "prompt": prompt,
        "task_prompt_preview": task_prompt[:400],
        "content_type": content_type,
        "project": {
            "name": project_name,
            "slug": project_slug,
            "project_file": project_file,
        } if project_name or project_slug or project_file else None,
        "context_file": context_file,
        "style": style,
        "source_artifacts": source_artifacts,
        "distribution_dir": (
            str((root / "distribution").relative_to(root))
            if (root / "distribution").exists() and layout.layout == "asset_set"
            else None
        ),
        "analytics_dir": (
            str((root / "analytics").relative_to(root))
            if (root / "analytics").exists() and layout.layout == "asset_set"
            else None
        ),
        "images": [
            {
                "placement": image.placement,
                "method": image.method,
                "file_path": _safe_relative_path(image.file_path, root),
                "alt_text": image.alt_text,
            }
            for image in getattr(pipeline_result, "images", [])
        ],
        "pipeline": {
            "total_cost": getattr(pipeline_result, "total_cost", 0.0),
            "total_rounds": getattr(pipeline_result, "total_rounds", 0),
            "converged": getattr(pipeline_result, "converged", False),
            "budget": budget_summary,
        },
    }

    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def build_content_calendar(
    project_name: str,
    project_slug: str,
    *,
    content_ops: dict[str, Any],
    seo: dict[str, Any],
    start_date: date,
    days: int,
) -> dict[str, Any]:
    pillars = [pillar for pillar in content_ops.get("pillars", []) if isinstance(pillar, dict)]
    if not pillars:
        pillars = [{"key": "general", "label": "General", "goals": ["awareness"], "default_formats": ["blog"]}]

    cadence = content_ops.get("cadence", {}) if isinstance(content_ops.get("cadence"), dict) else {}
    schedule = [item for item in cadence.get("default_schedule", []) if isinstance(item, dict)]
    if not schedule:
        schedule = [{"day_offset": 0, "formats": ["blog"]}]

    timezone = str(cadence.get("timezone") or "America/Los_Angeles")
    default_formats = [fmt for fmt in content_ops.get("default_formats", []) if isinstance(fmt, str)]
    fallback_topics = _build_topic_pool(seo)

    entries: list[dict[str, Any]] = []
    for day_index in range(days):
        current_day = start_date + timedelta(days=day_index)
        pillar = pillars[day_index % len(pillars)]
        pillar_key = slugify_token(str(pillar.get("key") or pillar.get("label") or "general"), max_length=32)
        pillar_label = str(pillar.get("label") or pillar.get("key") or "General")
        topic = _pick_topic(
            pillar,
            fallback_topics,
            project_name=project_name,
            day_index=day_index,
        )
        formats = [fmt for fmt in pillar.get("default_formats", []) if isinstance(fmt, str)] or default_formats or ["blog"]
        goal_list = [str(goal).strip() for goal in pillar.get("goals", []) if str(goal).strip()]
        outcome = goal_list[day_index % len(goal_list)] if goal_list else "awareness"

        entry = {
            "id": f"{current_day.isoformat()}-{pillar_key}-{slugify_token(topic, max_length=36)}",
            "date": current_day.isoformat(),
            "pillar": pillar_key,
            "pillar_label": pillar_label,
            "outcome": outcome,
            "arc": f"{pillar_key}-week-{((day_index // 7) + 1)}",
            "topic": topic,
            "hook_angles": _build_hook_angles(topic, pillar_label),
            "asset_set": formats,
            "distribution_slots": _build_distribution_slots(
                current_day=current_day,
                schedule=schedule,
                formats=formats,
            ),
            "status": "planned",
        }
        entries.append(entry)

    return {
        "project": {"name": project_name, "slug": project_slug},
        "generated_at": datetime.now().isoformat(),
        "timezone": timezone,
        "start_date": start_date.isoformat(),
        "days": days,
        "entries": entries,
    }


def write_content_calendar(
    output_root: str | Path,
    calendar_data: dict[str, Any],
    *,
    filename: str = "content-calendar.json",
) -> Path:
    calendar_dir = Path(output_root) / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    destination = calendar_dir / filename
    destination.write_text(json.dumps(calendar_data, indent=2) + "\n", encoding="utf-8")
    return destination


def load_content_calendar(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ensure_unique_dir(base_dir: Path) -> Path:
    candidate = base_dir
    suffix = 2
    while candidate.exists():
        candidate = base_dir.with_name(f"{base_dir.name}-{suffix}")
        suffix += 1
    return candidate


def _safe_relative_path(path: str, root: Path) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except Exception:
        return path


def _build_topic_pool(seo: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for key in ("long_tail_topics", "expansion_seeds", "seed_keywords"):
        raw = seo.get(key, [])
        if isinstance(raw, list):
            topics.extend(str(item).strip() for item in raw if str(item).strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in topics:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
    return deduped


def _pick_topic(
    pillar: dict[str, Any],
    fallback_topics: list[str],
    *,
    project_name: str,
    day_index: int,
) -> str:
    for key in ("topic_seeds", "topics"):
        raw = pillar.get(key, [])
        if isinstance(raw, list):
            options = [str(item).strip() for item in raw if str(item).strip()]
            if options:
                return options[day_index % len(options)]
    if fallback_topics:
        return fallback_topics[day_index % len(fallback_topics)]
    label = str(pillar.get("label") or pillar.get("key") or "General")
    return f"{label} topic for {project_name}"


def _build_hook_angles(topic: str, pillar_label: str) -> list[str]:
    return [
        f"The common mistake behind {topic}",
        f"What most teams miss about {topic}",
        f"How {pillar_label.lower()} content turns {topic} into an actionable system",
    ]


def _build_distribution_slots(
    *,
    current_day: date,
    schedule: list[dict[str, Any]],
    formats: list[str],
) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    allowed_formats = {fmt.lower() for fmt in formats}
    for item in schedule:
        day_offset = int(item.get("day_offset", 0) or 0)
        slot_day = current_day + timedelta(days=day_offset)
        item_formats = [str(fmt).strip() for fmt in item.get("formats", []) if str(fmt).strip()]
        for fmt in item_formats:
            fmt_key = fmt.lower()
            if fmt_key not in allowed_formats:
                continue
            slot_time = _parse_clock(str(item.get("time") or DEFAULT_FORMAT_TIME.get(fmt_key, "09:00")))
            publish_at = datetime.combine(slot_day, slot_time).isoformat()
            slots.append(
                {
                    "platform": str(item.get("platform") or DEFAULT_FORMAT_PLATFORM.get(fmt_key, fmt_key)),
                    "format": fmt_key,
                    "publish_at": publish_at,
                }
            )
    return slots


def _parse_clock(value: str) -> time:
    raw = value.strip() or "09:00"
    try:
        hour, minute = raw.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except Exception:
        return time(hour=9, minute=0)
