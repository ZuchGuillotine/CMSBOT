"""Screenshot manifest helpers for slideshow project packs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cmsbot.config import Config

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = 1
STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "for",
    "from",
    "in",
    "of",
    "on",
    "screen",
    "showing",
    "shows",
    "that",
    "the",
    "to",
    "view",
    "with",
}


@dataclass(frozen=True)
class ScreenshotResolution:
    key: str
    file: Path
    score: float
    strategy: str
    entry: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS


def manifest_path_for_dir(screenshot_dir: str | Path) -> Path:
    return Path(screenshot_dir) / MANIFEST_FILENAME


def resolve_screenshot_dir(
    directory: str | Path | None = None,
    *,
    project_file: str | Path | None = None,
    default_dir: str | Path | None = None,
) -> Path:
    if directory:
        return Path(directory).expanduser().resolve()

    env_dir = (Path.cwd() / ".").resolve()
    raw_env = (os.getenv("CMSBOT_SLIDESHOW_SCREENSHOTS_DIR") or "").strip()
    if raw_env:
        return Path(raw_env).expanduser().resolve()

    if project_file:
        from cmsbot.project import load_project_profile

        profile = load_project_profile(project_file)
        slideshow = profile.section("slideshow")
        configured = str(slideshow.get("screenshot_dir") or "").strip()
        if configured:
            return (profile.path.parent / configured).expanduser().resolve()

    if default_dir:
        default_path = Path(default_dir)
        return default_path if default_path.is_absolute() else (env_dir / default_path).resolve()

    return (Path.cwd() / "slideshow" / "screenshots").resolve()


def list_screenshot_files(screenshot_dir: str | Path) -> list[Path]:
    directory = Path(screenshot_dir)
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if is_image_file(path))


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify_ref(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "app_screen"


def tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return {token for token in tokens if token not in STOPWORDS and len(token) > 1}


def _entry_search_blob(key: str, entry: dict[str, Any]) -> str:
    parts = [
        key,
        str(entry.get("screen_name") or ""),
        str(entry.get("description") or ""),
        str(entry.get("variant_group") or ""),
    ]
    for field in ("tags", "features", "visible_elements"):
        parts.extend(str(value) for value in entry.get(field, []))
    return " ".join(parts)


def load_manifest(screenshot_dir: str | Path) -> dict[str, Any] | None:
    manifest_path = manifest_path_for_dir(screenshot_dir)
    if not manifest_path.is_file():
        return None
    with manifest_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a JSON object: {manifest_path}")
    entries = data.get("entries")
    if entries is None:
        legacy_entries = {
            key: value
            for key, value in data.items()
            if key not in {"schema_version", "generated_at", "updated_at"}
        }
        data = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": data.get("generated_at") or utc_now_iso(),
            "updated_at": data.get("updated_at") or utc_now_iso(),
            "entries": legacy_entries,
        }
    if not isinstance(data.get("entries"), dict):
        raise ValueError(f"Manifest entries must be an object: {manifest_path}")
    return data


def save_manifest(screenshot_dir: str | Path, manifest: dict[str, Any]) -> Path:
    manifest_path = manifest_path_for_dir(screenshot_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def build_manifest(entries: dict[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    timestamp = generated_at or utc_now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp,
        "updated_at": utc_now_iso(),
        "entries": entries,
    }


def validate_manifest(screenshot_dir: str | Path, manifest: dict[str, Any] | None = None) -> list[str]:
    directory = Path(screenshot_dir)
    data = manifest or load_manifest(directory)
    if data is None:
        return [f"Missing manifest: {manifest_path_for_dir(directory)}"]

    issues: list[str] = []
    seen_files: dict[str, str] = {}
    for key, entry in sorted(data.get("entries", {}).items()):
        if not isinstance(entry, dict):
            issues.append(f"{key}: entry must be an object")
            continue

        file_name = str(entry.get("file") or "").strip()
        if not file_name:
            issues.append(f"{key}: missing file")
            continue

        file_path = directory / file_name
        if not file_path.is_file():
            issues.append(f"{key}: file not found -> {file_name}")

        checksum = str(entry.get("checksum_sha256") or "").strip()
        if checksum and file_path.is_file():
            actual = compute_sha256(file_path)
            if actual != checksum:
                issues.append(f"{key}: checksum mismatch for {file_name}")

        previous_key = seen_files.get(file_name.lower())
        if previous_key and previous_key != key:
            issues.append(f"{key}: duplicates file {file_name} already used by {previous_key}")
        seen_files[file_name.lower()] = key

    return issues


def filter_manifest_entries(
    manifest: dict[str, Any] | None,
    *,
    allowed_refs: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if manifest is None:
        return {}
    entries = manifest.get("entries", {})
    if not allowed_refs:
        return dict(entries)
    allow = {ref.strip() for ref in allowed_refs if ref and ref.strip()}
    return {key: value for key, value in entries.items() if key in allow}


def summarize_manifest_for_prompt(entries: dict[str, dict[str, Any]]) -> str:
    if not entries:
        return (
            "No validated screenshot manifest is available. "
            "Do not use `app_screenshot`; use `ai_lifestyle` instead."
        )

    lines = [
        "Validated app screenshots. Use `app_screenshot` only when one of these keys clearly fits.",
        "If none fit, use `ai_lifestyle` and omit `screenshot_ref`.",
        "Allowed screenshot_ref values: " + ", ".join(sorted(entries)),
    ]
    for key, entry in sorted(entries.items()):
        summary = str(entry.get("description") or "").strip()
        screen_name = str(entry.get("screen_name") or "").strip()
        tags = ", ".join(entry.get("tags", []))
        features = ", ".join(entry.get("features", []))
        detail_parts = []
        if screen_name:
            detail_parts.append(screen_name)
        if summary:
            detail_parts.append(summary)
        if tags:
            detail_parts.append(f"tags: {tags}")
        if features:
            detail_parts.append(f"features: {features}")
        lines.append(f"- {key}: {' | '.join(detail_parts)}")
    return "\n".join(lines)


def coerce_screenshot_refs(
    specs: dict[str, Any],
    *,
    screenshot_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    allowed_refs: list[str] | None = None,
) -> dict[str, Any]:
    filtered_entries = filter_manifest_entries(manifest or load_manifest(screenshot_dir), allowed_refs=allowed_refs)
    if not filtered_entries:
        for show in specs.get("slideshows", []):
            for slide in show.get("slides", []):
                if slide.get("image_type") == "app_screenshot":
                    slide["image_type"] = "ai_lifestyle"
                    slide["image_note"] = slide.get("image_note") or slide.get("screenshot_ref") or "product in use"
                    slide.pop("screenshot_ref", None)
            cta = show.get("cta_slide")
            if isinstance(cta, dict) and cta.get("image_type") == "app_screenshot":
                cta["image_type"] = "ai_lifestyle"
                cta["image_note"] = cta.get("image_note") or cta.get("screenshot_ref") or "app overview"
                cta.pop("screenshot_ref", None)
        return specs

    for show in specs.get("slideshows", []):
        for slide in list(show.get("slides", [])) + ([show.get("cta_slide")] if show.get("cta_slide") else []):
            if not isinstance(slide, dict) or slide.get("image_type") != "app_screenshot":
                continue
            ref = str(slide.get("screenshot_ref") or "").strip()
            image_note = str(slide.get("image_note") or "").strip()
            resolved = resolve_screenshot_ref(
                ref or image_note,
                screenshot_dir=screenshot_dir,
                manifest=build_manifest(filtered_entries),
                min_score=0.15,
            )
            if resolved is None:
                slide["image_type"] = "ai_lifestyle"
                slide["image_note"] = image_note or ref or "product in use"
                slide.pop("screenshot_ref", None)
                continue
            slide["screenshot_ref"] = resolved.key
            if not slide.get("image_note"):
                slide["image_note"] = resolved.entry.get("description") or resolved.key
    return specs


def resolve_screenshot_ref(
    ref: str,
    *,
    screenshot_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    min_score: float = 0.25,
) -> ScreenshotResolution | None:
    if not ref.strip():
        return None

    directory = Path(screenshot_dir)
    raw_ref = ref.strip()
    normalized_ref = slugify_ref(raw_ref).replace("_", "")

    data = manifest or load_manifest(directory)
    if data is not None:
        entries = data.get("entries", {})
        if raw_ref in entries:
            entry = entries[raw_ref]
            return ScreenshotResolution(
                key=raw_ref,
                file=directory / entry["file"],
                score=1.0,
                strategy="direct",
                entry=entry,
            )

        for key, entry in entries.items():
            file_name = str(entry.get("file") or "")
            if Path(file_name).stem.lower() == raw_ref.lower():
                return ScreenshotResolution(
                    key=key,
                    file=directory / file_name,
                    score=0.98,
                    strategy="file-stem",
                    entry=entry,
                )
            if slugify_ref(key).replace("_", "") == normalized_ref:
                return ScreenshotResolution(
                    key=key,
                    file=directory / file_name,
                    score=0.96,
                    strategy="normalized-key",
                    entry=entry,
                )

        query_tokens = tokenize(raw_ref)
        best: ScreenshotResolution | None = None
        for key, entry in entries.items():
            haystack_tokens = tokenize(_entry_search_blob(key, entry))
            if not haystack_tokens:
                continue
            overlap = query_tokens & haystack_tokens
            if not overlap:
                continue
            score = len(overlap) / max(len(query_tokens), 1)
            if key in raw_ref or raw_ref.lower() in _entry_search_blob(key, entry).lower():
                score += 0.2
            candidate = ScreenshotResolution(
                key=key,
                file=directory / entry["file"],
                score=score,
                strategy="fuzzy",
                entry=entry,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        if best and best.score >= min_score:
            return best

    direct_candidates = []
    for suffix in IMAGE_EXTENSIONS:
        candidate = directory / f"{raw_ref}{suffix}"
        if candidate.is_file():
            direct_candidates.append(candidate)
    if direct_candidates:
        candidate = sorted(direct_candidates)[0]
        return ScreenshotResolution(
            key=candidate.stem,
            file=candidate,
            score=0.9,
            strategy="legacy-file",
            entry={"file": candidate.name, "description": candidate.stem},
        )
    return None


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


async def _describe_with_openai(image_path: Path, *, model: str, api_key: str) -> dict[str, Any]:
    import openai

    client = openai.AsyncOpenAI(api_key=api_key)
    prompt = (
        "Describe this app screenshot for a local manifest. "
        "Return JSON with keys: screen_name, description, tags, features, visible_elements, variant_group. "
        "Use short lowercase tags and features. Focus on what screen is visible, what user task it supports, "
        "and any notable charts, lists, chat, onboarding, settings, or biomarker details."
    )
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_completion_tokens=500,
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


async def _describe_with_anthropic(image_path: Path, *, model: str, api_key: str) -> dict[str, Any]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=500,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mimetypes.guess_type(image_path.name)[0] or "image/png",
                            "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe this app screenshot for a local manifest. "
                            "Return JSON with keys: screen_name, description, tags, features, visible_elements, variant_group. "
                            "Use short lowercase tags and features."
                        ),
                    },
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return json.loads(text or "{}")


async def _describe_with_gemini(image_path: Path, *, model: str, api_key: str) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=model,
        contents=[
            types.Part.from_text(
                text=(
                    "Describe this app screenshot for a local manifest. "
                    "Return JSON with keys: screen_name, description, tags, features, visible_elements, variant_group. "
                    "Use short lowercase tags and features."
                )
            ),
            types.Part.from_bytes(
                data=image_path.read_bytes(),
                mime_type=mimetypes.guess_type(image_path.name)[0] or "image/png",
            ),
        ],
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=500,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text or "{}")


def _normalize_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in result:
            result.append(text)
    return result[:8]


def _normalize_description(raw: dict[str, Any], image_path: Path, checksum: str, existing_key: str | None) -> tuple[str, dict[str, Any]]:
    screen_name = str(raw.get("screen_name") or image_path.stem.replace("_", " ").replace("-", " ")).strip()
    description = str(raw.get("description") or screen_name).strip()
    tags = _normalize_list(raw.get("tags"))
    features = _normalize_list(raw.get("features"))
    visible_elements = _normalize_list(raw.get("visible_elements"))
    variant_group = slugify_ref(str(raw.get("variant_group") or screen_name))

    base_key_parts = [screen_name]
    if features:
        base_key_parts.append(features[0])
    if tags:
        base_key_parts.append(tags[0])
    ref_key = slugify_ref("_".join(base_key_parts))
    if existing_key:
        ref_key = existing_key

    entry = {
        "file": image_path.name,
        "screen_name": screen_name,
        "description": description,
        "tags": tags,
        "features": features,
        "visible_elements": visible_elements,
        "variant_group": variant_group,
        "checksum_sha256": checksum,
        "updated_at": utc_now_iso(),
    }
    return ref_key, entry


def _pick_describer(config: Config, requested_provider: str | None) -> tuple[str, str, str]:
    if requested_provider:
        provider_name = requested_provider
        provider_config = config.get_provider_config(provider_name)
        return provider_name, provider_config.model, provider_config.api_key

    for provider_name in ("openai", "anthropic", "gemini"):
        provider_config = getattr(config, provider_name)
        if provider_config is not None:
            return provider_name, provider_config.model, provider_config.api_key
    raise ValueError("No vision-capable provider configured. Set OpenAI, Anthropic, or Gemini credentials.")


async def describe_screenshot_file(
    image_path: Path,
    *,
    env_file: str | Path | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    from cmsbot.config import Config

    config = Config.from_env(env_file)
    provider_name, default_model, api_key = _pick_describer(config, provider)
    chosen_model = model or default_model

    if provider_name == "openai":
        return await _describe_with_openai(image_path, model=chosen_model, api_key=api_key)
    if provider_name == "anthropic":
        return await _describe_with_anthropic(image_path, model=chosen_model, api_key=api_key)
    if provider_name == "gemini":
        return await _describe_with_gemini(image_path, model=chosen_model, api_key=api_key)
    raise ValueError(f"Unsupported screenshot describer provider: {provider_name}")


def describe_screenshots(
    screenshot_dir: str | Path,
    *,
    env_file: str | Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    target_files: list[Path] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    directory = Path(screenshot_dir)
    directory.mkdir(parents=True, exist_ok=True)

    existing = load_manifest(directory) or build_manifest({})
    existing_entries = dict(existing.get("entries", {}))
    files = target_files or list_screenshot_files(directory)
    targeted_names = {path.name for path in files}
    by_file = {str(entry.get("file")): key for key, entry in existing_entries.items()}
    refreshed_entries: dict[str, Any] = {}
    stats = {"described": 0, "reused": 0, "removed": 0}

    for image_path in files:
        checksum = compute_sha256(image_path)
        existing_key = by_file.get(image_path.name)
        existing_entry = existing_entries.get(existing_key, {}) if existing_key else {}
        if (
            existing_key
            and not force
            and existing_entry.get("checksum_sha256") == checksum
        ):
            refreshed_entries[existing_key] = existing_entry
            stats["reused"] += 1
            continue

        raw = asyncio.run(
            describe_screenshot_file(
                image_path,
                env_file=env_file,
                provider=provider,
                model=model,
            )
        )
        key, entry = _normalize_description(raw, image_path, checksum, existing_key)
        final_key = key
        suffix = 2
        while final_key in refreshed_entries and refreshed_entries[final_key].get("file") != image_path.name:
            final_key = f"{key}_{suffix}"
            suffix += 1
        refreshed_entries[final_key] = entry
        stats["described"] += 1

    for key, entry in existing_entries.items():
        file_name = str(entry.get("file"))
        if file_name in targeted_names:
            continue
        file_path = directory / file_name
        if target_files is not None:
            refreshed_entries[key] = entry
            continue
        if file_path.is_file():
            refreshed_entries[key] = entry
            continue
        stats["removed"] += 1

    manifest = build_manifest(refreshed_entries, generated_at=existing.get("generated_at"))
    save_manifest(directory, manifest)
    return manifest, stats


def copy_screenshot_into_dir(source_path: str | Path, screenshot_dir: str | Path) -> Path:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not is_image_file(source):
        raise ValueError(f"Unsupported image file: {source}")
    directory = Path(screenshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / source.name
    if destination.resolve() != source:
        shutil.copy2(source, destination)
    return destination
