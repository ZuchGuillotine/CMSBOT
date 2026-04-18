from __future__ import annotations

import json

from cmsbot.project import make_project_profile, write_project_profile
from cmsbot.social_derivatives import export_carousel, export_video_script


def test_export_carousel_writes_distribution_files_and_updates_manifest(tmp_path, monkeypatch):
    asset_set_dir = tmp_path / "output" / "asset-sets" / "2026" / "2026-04" / "2026-04-18-sample"
    source_dir = asset_set_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text(
        "# Sample Title\n\n## First Section\nThis is a long enough section body to drive the derivative generator. " * 4,
        encoding="utf-8",
    )
    (asset_set_dir / "manifest.json").write_text(json.dumps({"asset_set_id": "2026-04-18-sample"}), encoding="utf-8")

    project_path = tmp_path / "project.json"
    write_project_profile(project_path, make_project_profile("Acme Cloud"))

    monkeypatch.setattr(
        "cmsbot.social_derivatives.build_provider_from_config",
        lambda **kwargs: (None, object()),
    )
    monkeypatch.setattr(
        "cmsbot.social_derivatives._run_generation",
        lambda provider, system_prompt, prompt: json.dumps(
            {
                "section_title": "First Section",
                "summary": "Carousel summary",
                "slides": [
                    {"index": 1, "title": "Hook", "body": "Start with the problem.", "asset_note": "Bold headline"},
                    {"index": 2, "title": "Problem", "body": "Explain the mistake.", "asset_note": "Diagram"},
                    {"index": 3, "title": "Fix", "body": "Show the right approach.", "asset_note": "Checklist"},
                    {"index": 4, "title": "CTA", "body": "Tell them what to do next.", "asset_note": "Brand CTA"},
                ],
                "caption": "Useful caption copy",
                "cta": "Book a demo",
                "visual_direction": "Clean diagrams with product callouts",
            }
        ),
    )

    payload, json_path, md_path, caption_path = export_carousel(
        asset_set_dir,
        project_file=project_path,
        platform="instagram",
    )

    assert payload["platform"] == "instagram"
    assert len(payload["slides"]) == 4
    assert json_path == asset_set_dir / "distribution" / "instagram" / "carousel.json"
    assert md_path.exists()
    assert caption_path.exists()

    manifest = json.loads((asset_set_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["distribution_artifacts"]["instagram"]["carousel_json"] == "distribution/instagram/carousel.json"


def test_export_video_script_writes_distribution_files_and_updates_manifest(tmp_path, monkeypatch):
    asset_set_dir = tmp_path / "output" / "asset-sets" / "2026" / "2026-04" / "2026-04-18-sample"
    source_dir = asset_set_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text(
        "# Sample Title\n\n## First Section\nThis is a long enough section body to drive the derivative generator. " * 4,
        encoding="utf-8",
    )
    (asset_set_dir / "manifest.json").write_text(json.dumps({"asset_set_id": "2026-04-18-sample"}), encoding="utf-8")

    project_path = tmp_path / "project.json"
    write_project_profile(project_path, make_project_profile("Acme Cloud"))

    monkeypatch.setattr(
        "cmsbot.social_derivatives.build_provider_from_config",
        lambda **kwargs: (None, object()),
    )
    monkeypatch.setattr(
        "cmsbot.social_derivatives._run_generation",
        lambda provider, system_prompt, prompt: json.dumps(
            {
                "section_title": "First Section",
                "summary": "Video summary",
                "hook": "Most teams do this too late",
                "target_duration_seconds": 30,
                "beats": [
                    {"index": 1, "narration": "Hook the viewer", "visual": "Fast cut intro", "overlay_text": "Start here"},
                    {"index": 2, "narration": "Explain the mistake", "visual": "Show the broken process", "overlay_text": "The mistake"},
                    {"index": 3, "narration": "Show the fix", "visual": "Show the better workflow", "overlay_text": "The fix"},
                    {"index": 4, "narration": "Close with CTA", "visual": "End card", "overlay_text": "See it live"},
                ],
                "caption": "Caption copy",
                "cta": "Start a trial",
            }
        ),
    )

    payload, json_path, script_path, storyboard_path = export_video_script(
        asset_set_dir,
        project_file=project_path,
        platform="tiktok",
    )

    assert payload["platform"] == "tiktok"
    assert payload["target_duration_seconds"] == 30
    assert json_path == asset_set_dir / "distribution" / "tiktok" / "video_script.json"
    assert script_path.exists()
    assert storyboard_path.exists()

    manifest = json.loads((asset_set_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["distribution_artifacts"]["tiktok"]["video_script_json"] == "distribution/tiktok/video_script.json"
