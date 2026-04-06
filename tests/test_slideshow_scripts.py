from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from cmsbot.screenshots import build_manifest, save_manifest


def _load_module(name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / relative_path
    if "dotenv" not in sys.modules:
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *args, **kwargs: False
        sys.modules["dotenv"] = fake_dotenv
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_save_batch_persists_standalone_brief_metadata(tmp_path, monkeypatch):
    module = _load_module("slideshow_blog_to_slideshow", "slideshow/blog_to_slideshow.py")
    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path)

    specs = {
        "source_id": "q2-launch-brief",
        "source_type": "brief",
        "generated_at": "2026-04-05T12:00:00",
        "slideshows": [
            {
                "id": "launch-story",
                "format": "contrarian",
                "hook": "Most launches feel interchangeable",
                "slides": [
                    {
                        "slide_num": 1,
                        "text_overlay": "Make the first claim sharper",
                        "image_type": "ai_lifestyle",
                        "image_note": "founder planning launch sequence",
                    }
                ],
                "cta_slide": {
                    "text_overlay": "Follow for the rollout",
                    "image_type": "ai_lifestyle",
                    "image_note": "product teaser at desk",
                },
            }
        ],
    }

    batch_dir = module.save_batch(
        specs,
        {
            "content_rules": {"banned_words": [], "max_words_per_slide": 12},
        },
    )

    spec_path = batch_dir / "q2-launch-brief_slideshows.json"
    metadata_path = batch_dir / "launch-story" / "metadata.json"

    assert spec_path.exists()
    metadata = json.loads(metadata_path.read_text())
    assert metadata["source_id"] == "q2-launch-brief"
    assert metadata["source_type"] == "brief"
    assert "source_blog" not in metadata


def test_process_spec_file_generates_ai_slides_and_copies_screenshots(tmp_path, monkeypatch):
    module = _load_module("slideshow_generate_images", "slideshow/generate_images.py")

    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    screenshot_file = screenshots_dir / "overview.png"
    screenshot_file.write_bytes(b"png")
    save_manifest(
        screenshots_dir,
        build_manifest(
            {
                "product_overview_screen": {
                    "file": "overview.png",
                    "screen_name": "Product overview",
                    "description": "overview screen",
                }
            }
        ),
    )
    monkeypatch.setattr(module, "SCREENSHOTS_DIR", screenshots_dir)

    captured_prompts: list[str] = []

    def _fake_generate(prompt_text: str, output_path: Path) -> tuple[bool, str | None]:
        captured_prompts.append(prompt_text)
        output_path.write_bytes(b"jpeg")
        return True, "stub-provider"

    monkeypatch.setattr(module, "generate_image", _fake_generate)

    batch_dir = tmp_path / "batch_2026-04-05"
    show_dir = batch_dir / "story-sequence"
    show_dir.mkdir(parents=True)
    spec_path = batch_dir / "story-sequence_slideshows.json"
    spec_path.write_text(
        json.dumps(
            {
                "source_id": "story-sequence",
                "source_type": "brief",
                "slideshows": [
                    {
                        "id": "story-sequence",
                        "format": "before-after",
                        "hook": "This is where launches stall",
                        "slides": [
                            {
                                "slide_num": 1,
                                "text_overlay": "Most teams start too broad",
                                "image_type": "ai_lifestyle",
                                "image_note": "team debating campaign priorities",
                            },
                            {
                                "slide_num": 2,
                                "text_overlay": "Show the live product",
                                "image_type": "app_screenshot",
                                "screenshot_ref": "product_overview_screen",
                                "image_note": "product overview screen",
                            },
                        ],
                        "cta_slide": {
                            "text_overlay": "Follow for launch breakdowns",
                            "image_type": "ai_lifestyle",
                            "image_note": "founder reviewing launch analytics",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = module.process_spec_file(spec_path)

    assert result["generated"] == 2
    assert result["resolved_screenshot"] == 1
    assert len(captured_prompts) == 2
    assert "This is where launches stall" in captured_prompts[0]
    assert "Most teams start too broad" in captured_prompts[0]
    assert (show_dir / "images" / "02_product_overview_screen.png").exists()
