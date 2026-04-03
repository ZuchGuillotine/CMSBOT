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
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
TEMPLATES_DIR = SCRIPT_DIR / "prompts" / "image_json_templates"
SCREENSHOTS_DIR = SCRIPT_DIR / "screenshots"


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


# ---------------------------------------------------------------------------
# Gemini API Integration (Nano Banana Pro)
# ---------------------------------------------------------------------------

def generate_image_gemini(prompt_json: dict, output_path: Path) -> bool:
    """Generate an image using Google Gemini's image generation API."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_API_KEY not set")
        return False

    try:
        import google.generativeai as genai
    except ImportError:
        print("[ERROR] Install google-generativeai: pip install google-generativeai")
        return False

    genai.configure(api_key=api_key)

    # Convert JSON prompt to a text prompt for Gemini
    text_prompt = json.dumps(prompt_json, indent=2)
    full_prompt = f"""Generate a photorealistic image based on this specification.
The image should look like it was taken with a phone camera, not AI-generated.
Follow every detail in the JSON exactly:

{text_prompt}

CRITICAL: The image must look like a candid phone photo, NOT an AI render.
No text, watermarks, or logos in the image. Portrait orientation (9:16 aspect ratio)."""

    try:
        # Use Gemini with image generation capability
        model = genai.GenerativeModel("gemini-2.0-flash-exp")  # Or latest image-capable model
        response = model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="image/jpeg",
            ),
        )

        # Save the generated image
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    image_data = base64.b64decode(part.inline_data.data)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(image_data)
                    return True

        print(f"  [WARN] No image in response for {output_path.name}")
        return False

    except Exception as e:
        print(f"  [ERROR] Image generation failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------

def process_spec_file(spec_path: Path, prompt_only: bool = False) -> dict:
    """Process a slideshow spec file and generate images for AI slides."""
    with open(spec_path) as f:
        specs = json.load(f)

    results = {"generated": 0, "skipped_screenshot": 0, "errors": 0, "prompts": []}

    for show in specs.get("slideshows", []):
        show_id = show["id"]
        batch_dir = spec_path.parent
        images_dir = batch_dir / show_id / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n  Slideshow: {show_id}")

        all_slides = show.get("slides", [])
        if "cta_slide" in show:
            cta = show["cta_slide"].copy()
            cta["slide_num"] = len(all_slides) + 1
            cta["image_note"] = cta.get("screenshot_ref", "app overview")
            all_slides.append(cta)

        for slide in all_slides:
            num = slide["slide_num"]
            img_type = slide.get("image_type", "ai_lifestyle")
            img_note = slide.get("image_note", "")
            filename = f"{num:02d}_{img_note.replace(' ', '_')[:30]}.jpg"
            output_path = images_dir / filename

            if img_type == "app_screenshot":
                # Check if screenshot exists in library
                ref = slide.get("screenshot_ref", "")
                screenshot_path = SCREENSHOTS_DIR / f"{ref}.png"
                if screenshot_path.exists():
                    # Copy screenshot to output
                    from shutil import copy2
                    copy2(screenshot_path, output_path)
                    print(f"    Slide {num}: copied screenshot '{ref}'")
                else:
                    print(f"    Slide {num}: ⚠ SCREENSHOT NEEDED: '{ref}' → {SCREENSHOTS_DIR}/")
                results["skipped_screenshot"] += 1
                continue

            # AI image generation
            scene_key, custom_subject = image_note_to_scene(img_note)
            prompt = build_full_prompt(scene_key, custom_subject)
            results["prompts"].append({
                "slideshow": show_id,
                "slide": num,
                "scene": scene_key,
                "prompt": prompt,
                "output": str(output_path),
            })

            if prompt_only:
                print(f"    Slide {num}: [PROMPT] scene={scene_key}")
                continue

            print(f"    Slide {num}: generating ({scene_key})...", end=" ", flush=True)
            success = generate_image_gemini(prompt, output_path)
            if success:
                print("✓")
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

    total = {"generated": 0, "skipped_screenshot": 0, "errors": 0, "prompts": []}

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
    print(f"Screenshots needed: {total['skipped_screenshot']}")
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
