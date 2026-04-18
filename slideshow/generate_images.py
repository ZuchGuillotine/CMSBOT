#!/usr/bin/env python3
"""
generate_images.py — Generate AI lifestyle images via Nano Banana Pro (Google Gemini).

Reads slideshow specs and generates images for slides that need AI-generated content.
App screenshots are skipped (you provide those manually).

Usage:
  python generate_images.py --batch output/batch_2026-04-01/
  python generate_images.py --spec output/batch_2026-04-01/stack-mistakes-v1_slideshows.json
  python generate_images.py --prompt-only  # Just output prompts, don't call API

Requires: GOOGLE_API_KEY environment variable for Gemini API access.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional
from shutil import copy2

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cmsbot.screenshots import load_manifest, resolve_screenshot_ref
from cmsbot.config import Config
from cmsbot.providers.image_providers import (
    GPTImageProvider,
    GrokImageProvider,
    ImageProvider,
    NanobananProvider,
)

CONFIG_PATH = SCRIPT_DIR / "config.json"
TEMPLATES_DIR = SCRIPT_DIR / "prompts" / "image_json_templates"
SCREENSHOTS_DIR = Path(
    os.getenv("CMSBOT_SLIDESHOW_SCREENSHOTS_DIR", str(SCRIPT_DIR / "screenshots"))
).expanduser()


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# JSON Color Grade Templates (Nano Banana Pro)
# ---------------------------------------------------------------------------

# Base template that all scene-specific templates inherit from
BASE_COLOR_GRADE = {
    "style": "candid phone photo, not professional photography",
    "composition": "slightly off-center, casual framing, portrait orientation 9:16",
    "lighting": {
        "source": "natural window light",
        "direction": "soft side light",
        "quality": "natural, slightly warm, no studio lighting"
    },
    "color_palette": {
        "restriction": "warm neutral tones, slight golden cast, muted earth tones",
        "sensor_simulation": "iPhone 15 front camera in natural light",
        "grade": "slight lift in shadows, gentle roll-off in highlights, warm midtones"
    },
    "camera_or_lens": "iPhone 15 selfie camera, 12mm equivalent, slight barrel distortion",
    "mood": "authentic, relatable, not aspirational",
    "negative_constraints": [
        "no perfect skin smoothing",
        "no studio backdrop",
        "no stock photo poses",
        "no text in image",
        "no brand logos",
        "no extra fingers",
        "no uncanny valley expressions",
        "no watermarks",
        "no borders or frames"
    ]
}

# Scene-specific templates
SCENE_TEMPLATES = {
    "kitchen_supplements": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person at kitchen counter with supplement bottles",
            "expression": "focused, curious",
            "clothing": "casual athleisure, muted tones"
        },
        "context": "morning routine, natural home kitchen, not staged",
        "background": "blurred kitchen interior, realistic depth of field, f/1.9"
    },
    "person_frustrated": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person looking frustrated or confused at supplement bottles",
            "expression": "mild frustration, questioning look",
            "clothing": "casual everyday wear"
        },
        "context": "home environment, relatable moment of confusion",
        "background": "blurred living space, natural clutter, f/2.0"
    },
    "person_surprised": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person looking at phone screen with surprised expression",
            "expression": "genuine surprise, eyes slightly wider",
            "clothing": "casual, comfortable"
        },
        "context": "discovered something unexpected on their phone, natural reaction",
        "background": "blurred home or office, f/1.9"
    },
    "supplement_flat_lay": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "top-down view of supplement bottles, pill organizer, and notebook on table",
            "expression": "n/a - product shot",
            "clothing": "n/a"
        },
        "context": "organized supplement routine, morning prep",
        "background": "light wood or marble surface, natural light from above"
    },
    "gym_recovery": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person post-workout with shaker bottle and supplement containers",
            "expression": "focused, satisfied",
            "clothing": "workout gear, slightly sweaty"
        },
        "context": "just finished working out, taking post-workout supplements",
        "background": "gym or home gym, slightly blurred, f/2.0"
    },
    "lab_results": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person holding lab results paper, looking at it thoughtfully",
            "expression": "thoughtful, analytical",
            "clothing": "casual smart"
        },
        "context": "reviewing blood work results, health-conscious decision making",
        "background": "desk or kitchen table, natural light, f/1.9"
    },
    "sleep_routine": {
        "goal": "tiktok_slideshow_slide",
        "subject": {
            "description": "person on bed or bedside table with evening supplements",
            "expression": "calm, settled",
            "clothing": "comfortable loungewear"
        },
        "context": "evening wind-down routine, taking sleep supplements",
        "background": "bedroom, warm low light, slightly dim, f/1.8"
    },
}


def build_full_prompt(scene_key: str, custom_subject: str = None) -> dict:
    """Build a complete Nano Banana Pro JSON prompt from a scene template."""
    if scene_key not in SCENE_TEMPLATES:
        # Default to kitchen supplements
        scene_key = "kitchen_supplements"

    template = {**BASE_COLOR_GRADE, **SCENE_TEMPLATES[scene_key]}

    if custom_subject:
        template["subject"]["description"] = custom_subject

    return template


def sanitize_filename_fragment(text: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in text.strip().lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:40] or fallback


def image_note_to_scene(image_note: str) -> tuple[str, Optional[str]]:
    """Map a slide's image_note to a scene template key and optional custom subject."""
    note_lower = image_note.lower()

    scene_map = {
        "frustrated": "person_frustrated",
        "confused": "person_frustrated",
        "mistake": "person_frustrated",
        "wrong": "person_frustrated",
        "surprised": "person_surprised",
        "shocked": "person_surprised",
        "data": "person_surprised",
        "phone": "person_surprised",
        "kitchen": "kitchen_supplements",
        "morning": "kitchen_supplements",
        "routine": "kitchen_supplements",
        "supplement": "supplement_flat_lay",
        "bottles": "supplement_flat_lay",
        "pills": "supplement_flat_lay",
        "organize": "supplement_flat_lay",
        "gym": "gym_recovery",
        "workout": "gym_recovery",
        "exercise": "gym_recovery",
        "recovery": "gym_recovery",
        "lab": "lab_results",
        "blood": "lab_results",
        "results": "lab_results",
        "biomarker": "lab_results",
        "sleep": "sleep_routine",
        "night": "sleep_routine",
        "evening": "sleep_routine",
        "bed": "sleep_routine",
    }

    for keyword, scene in scene_map.items():
        if keyword in note_lower:
            return scene, image_note

    return "kitchen_supplements", image_note


def build_generation_prompt(
    show: dict,
    slide: dict,
    prompt_json: dict,
) -> str:
    """Build a slide-aware prompt so each generated image matches the slideshow context."""
    hook = show.get("hook", "").strip()
    format_id = show.get("format", "").strip()
    text_overlay = slide.get("text_overlay", "").strip()
    image_note = slide.get("image_note", "").strip()
    slide_num = slide.get("slide_num", "")
    cta_note = ""
    if slide.get("is_cta"):
        cta_note = "This is the CTA slide. Keep the scene visually inviting and action-oriented."

    directive = f"""Generate a photorealistic vertical slideshow image for social media.
The image should look like a candid phone photo, not polished studio photography and not an AI render.

Slideshow context:
- Slideshow ID: {show.get('id', '')}
- Format: {format_id}
- Hook: {hook}
- Slide number: {slide_num}
- Overlay text that will be added later in the app: {text_overlay}
- Visual direction: {image_note}
- {cta_note or "This is an editorial content slide, not a CTA graphic."}

Follow every detail in this JSON scene specification exactly:
{json.dumps(prompt_json, indent=2)}

Constraints:
- Portrait orientation, 9:16 framing
- Leave practical negative space for later text overlay, but do not render any text in-image
- No UI chrome, dashboards, screenshots, labels, logos, watermarks, or borders
- The scene must feel specific to this slide, not generic stock content"""
    return directive


# ---------------------------------------------------------------------------
# Gemini API Integration (Nano Banana Pro)
# ---------------------------------------------------------------------------

def build_raster_image_providers() -> list[ImageProvider]:
    """Build slideshow image providers in priority order, matching the main pipeline."""
    config = Config.from_env()
    providers: list[ImageProvider] = []

    if config.xai_key:
        providers.append(GrokImageProvider(api_key=config.xai_key))

    if config.gemini:
        providers.append(
            NanobananProvider(
                api_key=config.gemini.api_key,
                model=os.environ.get("CMSBOT_SLIDESHOW_IMAGE_MODEL", "gemini-3-pro-image-preview"),
            )
        )

    if len(providers) < 2 and config.openai:
        providers.append(GPTImageProvider(api_key=config.openai.api_key, quality="high"))

    return providers


def generate_image(prompt_text: str, output_path: Path) -> tuple[bool, str | None]:
    """Generate a slideshow image using the first available working provider."""
    providers = build_raster_image_providers()
    if not providers:
        print("[ERROR] No image providers configured. Set GEMINI_API_KEY, OPENAI_API_KEY, or XAI_API_KEY.")
        return False, None

    for provider in providers:
        try:
            result = asyncio.run(provider.generate(prompt_text, size="1024x1792"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(result.image_bytes)
            return True, result.provider
        except Exception as e:
            print(f"  [WARN] {provider.name} failed: {e}")

    print("  [ERROR] Image generation failed across all configured providers")
    return False, None


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------

def process_spec_file(spec_path: Path, prompt_only: bool = False) -> dict:
    """Process a slideshow spec file and generate images for AI slides."""
    with open(spec_path) as f:
        specs = json.load(f)

    manifest = load_manifest(SCREENSHOTS_DIR)
    results = {"generated": 0, "resolved_screenshot": 0, "fallback_ai": 0, "errors": 0, "prompts": []}

    for show in specs.get("slideshows", []):
        show_id = show["id"]
        batch_dir = spec_path.parent
        images_dir = batch_dir / show_id / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n  Slideshow: {show_id}")

        all_slides = list(show.get("slides", []))
        if "cta_slide" in show:
            cta = show["cta_slide"].copy()
            cta["slide_num"] = len(all_slides) + 1
            cta["image_note"] = cta.get("image_note") or cta.get("screenshot_ref", "app overview")
            cta["is_cta"] = True
            all_slides.append(cta)

        for slide in all_slides:
            num = slide["slide_num"]
            img_type = slide.get("image_type", "ai_lifestyle")
            img_note = slide.get("image_note", "")
            filename = f"{num:02d}_{sanitize_filename_fragment(img_note or slide.get('text_overlay', ''), 'slide')}.jpg"
            output_path = images_dir / filename

            if img_type == "app_screenshot":
                ref = slide.get("screenshot_ref", "")
                resolved = resolve_screenshot_ref(ref, screenshot_dir=SCREENSHOTS_DIR, manifest=manifest)
                if resolved and resolved.file.exists():
                    screenshot_name = img_note.replace(" ", "_")[:30] or resolved.key
                    screenshot_output = images_dir / f"{num:02d}_{screenshot_name}{resolved.file.suffix.lower()}"
                    copy2(resolved.file, screenshot_output)
                    print(
                        f"    Slide {num}: copied screenshot '{resolved.key}' "
                        f"({resolved.strategy}, {resolved.file.name})"
                    )
                    results["resolved_screenshot"] += 1
                    continue

                fallback_note = img_note or ref or "product in use"
                print(
                    f"    Slide {num}: ⚠ screenshot unresolved for '{ref}'. "
                    f"Falling back to AI image."
                )
                img_note = fallback_note
                results["fallback_ai"] += 1

            # AI image generation
            scene_key, custom_subject = image_note_to_scene(img_note)
            prompt_json = build_full_prompt(scene_key, custom_subject)
            prompt_text = build_generation_prompt(show, slide, prompt_json)
            results["prompts"].append({
                "slideshow": show_id,
                "slide": num,
                "scene": scene_key,
                "prompt_json": prompt_json,
                "prompt_text": prompt_text,
                "output": str(output_path),
            })

            if prompt_only:
                print(f"    Slide {num}: [PROMPT] scene={scene_key}")
                continue

            print(f"    Slide {num}: generating ({scene_key})...", end=" ", flush=True)
            success, provider_name = generate_image(prompt_text, output_path)
            if success:
                provider_label = provider_name or "image-provider"
                print(f"✓ ({provider_label})")
                results["generated"] += 1
            else:
                print("✗")
                results["errors"] += 1

    return results


def process_batch(batch_dir: Path, prompt_only: bool = False):
    """Process all spec files in a batch directory."""
    batch_path = Path(batch_dir)
    spec_files = sorted(batch_path.glob("*_slideshows.json"))

    if not spec_files:
        print(f"[WARN] No slideshow spec files found in {batch_path}")
        return

    print(f"Found {len(spec_files)} spec files in {batch_path.name}")

    total = {"generated": 0, "resolved_screenshot": 0, "fallback_ai": 0, "errors": 0, "prompts": []}

    for spec_file in spec_files:
        print(f"\nProcessing: {spec_file.name}")
        result = process_spec_file(spec_file, prompt_only=prompt_only)
        for key in total:
            if key == "prompts":
                total["prompts"].extend(result["prompts"])
            else:
                total[key] += result[key]

    print(f"\n{'='*40}")
    print(f"Generated: {total['generated']} images")
    print(f"Screenshots copied: {total['resolved_screenshot']}")
    print(f"Screenshot AI fallbacks: {total['fallback_ai']}")
    print(f"Errors: {total['errors']}")

    if prompt_only:
        prompts_path = batch_path / "all_prompts.json"
        with open(prompts_path, "w") as f:
            json.dump(total["prompts"], f, indent=2)
        print(f"Prompts saved to: {prompts_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate AI images for slideshow slides")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", help="Process all spec files in a batch directory")
    group.add_argument("--spec", help="Process a single spec file")
    parser.add_argument("--prompt-only", action="store_true",
                        help="Only output prompts (don't call API)")

    args = parser.parse_args()

    if args.spec:
        process_spec_file(Path(args.spec), prompt_only=args.prompt_only)
    elif args.batch:
        process_batch(args.batch, prompt_only=args.prompt_only)


if __name__ == "__main__":
    main()
