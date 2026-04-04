from __future__ import annotations

import json

from cmsbot.screenshots import (
    build_manifest,
    coerce_screenshot_refs,
    describe_screenshots,
    resolve_screenshot_ref,
    save_manifest,
    validate_manifest,
)


def test_resolve_screenshot_ref_matches_manifest_key(tmp_path):
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    (screenshot_dir / "IMG_0626.PNG").write_bytes(b"png")
    manifest = build_manifest(
        {
            "stack_overview_screen": {
                "file": "IMG_0626.PNG",
                "screen_name": "Stack overview",
                "description": "stack screen with daily log and streak",
                "tags": ["stack", "supplements", "daily"],
                "features": ["supplement tracking", "consistency streak"],
                "visible_elements": ["daily log", "streak counter"],
                "variant_group": "stack_overview",
            }
        }
    )
    save_manifest(screenshot_dir, manifest)

    resolved = resolve_screenshot_ref(
        "stack_overview_screen",
        screenshot_dir=screenshot_dir,
    )

    assert resolved is not None
    assert resolved.key == "stack_overview_screen"
    assert resolved.strategy == "direct"


def test_resolve_screenshot_ref_fuzzy_matches_legacy_text(tmp_path):
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    (screenshot_dir / "IMG_0627.PNG").write_bytes(b"png")
    manifest = build_manifest(
        {
            "labs_dashboard_screen": {
                "file": "IMG_0627.PNG",
                "screen_name": "Labs dashboard",
                "description": "dashboard showing biomarkers and trend charts",
                "tags": ["labs", "biomarkers", "dashboard"],
                "features": ["lab results", "trend analysis"],
                "visible_elements": ["charts", "biomarker cards"],
                "variant_group": "labs",
            }
        }
    )
    save_manifest(screenshot_dir, manifest)

    resolved = resolve_screenshot_ref(
        "protocol dashboard showing biomarkers",
        screenshot_dir=screenshot_dir,
    )

    assert resolved is not None
    assert resolved.key == "labs_dashboard_screen"
    assert resolved.strategy == "fuzzy"


def test_coerce_screenshot_refs_falls_back_to_ai_when_manifest_missing(tmp_path):
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    specs = {
        "slideshows": [
            {
                "slides": [
                    {
                        "slide_num": 1,
                        "image_type": "app_screenshot",
                        "screenshot_ref": "dashboard",
                    }
                ],
                "cta_slide": {
                    "image_type": "app_screenshot",
                    "screenshot_ref": "overview",
                },
            }
        ]
    }

    coerced = coerce_screenshot_refs(specs, screenshot_dir=screenshot_dir)

    slide = coerced["slideshows"][0]["slides"][0]
    cta = coerced["slideshows"][0]["cta_slide"]
    assert slide["image_type"] == "ai_lifestyle"
    assert "screenshot_ref" not in slide
    assert cta["image_type"] == "ai_lifestyle"
    assert "screenshot_ref" not in cta


def test_validate_manifest_reports_missing_file(tmp_path):
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-04-03T00:00:00+00:00",
        "updated_at": "2026-04-03T00:00:00+00:00",
        "entries": {
            "chat_screen": {
                "file": "missing.png",
                "description": "chat screen",
            }
        },
    }
    (screenshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    issues = validate_manifest(screenshot_dir)

    assert issues == ["chat_screen: file not found -> missing.png"]


def test_describe_screenshots_target_file_preserves_other_entries(tmp_path, monkeypatch):
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    first = screenshot_dir / "IMG_0626.PNG"
    second = screenshot_dir / "IMG_0627.PNG"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    save_manifest(
        screenshot_dir,
        build_manifest(
            {
                "stack_screen": {
                    "file": "IMG_0626.PNG",
                    "screen_name": "Stack screen",
                    "description": "stack screen",
                    "checksum_sha256": "",
                },
                "chat_screen": {
                    "file": "IMG_0627.PNG",
                    "screen_name": "Chat screen",
                    "description": "chat screen",
                    "checksum_sha256": "",
                },
            }
        ),
    )

    async def _fake_describe(*args, **kwargs):
        return {
            "screen_name": "Chat screen",
            "description": "assistant chat response screen",
            "tags": ["chat"],
            "features": ["assistant"],
            "visible_elements": ["messages"],
            "variant_group": "chat",
        }

    monkeypatch.setattr("cmsbot.screenshots.describe_screenshot_file", _fake_describe)

    manifest, stats = describe_screenshots(screenshot_dir, target_files=[second], force=True)

    assert stats["described"] == 1
    assert set(manifest["entries"]) == {"stack_screen", "chat_screen"}
