#!/usr/bin/env python3
"""
postprocess.py — Image post-processing for TikTok/Instagram authenticity.

Applies the playbook's anti-detection techniques:
  1. Film grain (2-3% gaussian noise)
  2. 1080p resolution enforcement (NOT 4K)
  3. EXIF/metadata stripping
  4. Light JPEG compression (quality 85-90)
  5. Micro-rotation (0.3-0.8 degrees) for AI images
  6. Review thumbnail grid generation

Usage:
  python postprocess.py --batch output/batch_2026-04-01/
  python postprocess.py --image output/batch_2026-04-01/stack-v1/images/01_hook.jpg --type ai
  python postprocess.py --batch output/batch_2026-04-01/ --review-only
"""

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np
except ImportError:
    print("[ERROR] Install dependencies: pip install Pillow numpy")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Post-Processing Functions
# ---------------------------------------------------------------------------

def add_film_grain(img: Image.Image, intensity: float = 2.5) -> Image.Image:
    """Add film grain noise to an image. Intensity is percentage (1-5 typical)."""
    arr = np.array(img, dtype=np.float32)
    noise_strength = (intensity / 100.0) * 255
    noise = np.random.normal(0, noise_strength, arr.shape).astype(np.float32)
    noisy = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(noisy)


def micro_rotate(img: Image.Image, min_deg: float = 0.3, max_deg: float = 0.8) -> Image.Image:
    """Apply a very slight random rotation to break AI-perfect alignment."""
    angle = random.uniform(min_deg, max_deg) * random.choice([-1, 1])
    # Expand=False crops to original size (natural look)
    rotated = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(0, 0, 0))
    return rotated


def strip_metadata(img: Image.Image) -> Image.Image:
    """Strip all EXIF/metadata by creating a clean copy."""
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))
    return clean


def ensure_resolution(img: Image.Image, target_w: int = 1080, target_h: int = 1920) -> Image.Image:
    """Resize/crop image to exact target resolution. Maintains aspect ratio and center-crops."""
    target_ratio = target_w / target_h
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # Image is wider — fit height, crop width
        new_h = target_h
        new_w = int(img_ratio * target_h)
    else:
        # Image is taller — fit width, crop height
        new_w = target_w
        new_h = int(target_w / img_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))

    return img


def compress_jpeg(img: Image.Image, quality: int = 87) -> Image.Image:
    """Apply JPEG compression artifacts by saving and reloading."""
    from io import BytesIO
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    buffer.seek(0)
    return Image.open(buffer).copy()


def process_image(
    input_path: Path,
    output_path: Path,
    image_type: str = "ai_lifestyle",
    config: dict = None,
) -> dict:
    """
    Full post-processing pipeline for a single image.

    image_type: "ai_lifestyle" (full treatment) or "app_screenshot" (light treatment)
    """
    if config is None:
        config = load_config()

    pp = config["image_generation"]["post_processing"]
    platform = config["platforms"]["tiktok"]  # Default to TikTok dimensions

    img = Image.open(input_path).convert("RGB")
    original_size = img.size
    steps_applied = []

    # 1. Ensure correct resolution
    target_w, target_h = platform["resolution"]
    if img.size != (target_w, target_h):
        img = ensure_resolution(img, target_w, target_h)
        steps_applied.append(f"resized to {target_w}x{target_h}")

    # 2. AI-specific processing
    if image_type == "ai_lifestyle":
        # Film grain
        grain = pp.get("film_grain_percent", 2.5)
        img = add_film_grain(img, intensity=grain)
        steps_applied.append(f"film grain {grain}%")

        # Micro-rotation
        rot_range = pp.get("micro_rotation_range", [0.3, 0.8])
        img = micro_rotate(img, rot_range[0], rot_range[1])
        steps_applied.append("micro-rotation")

    # 3. Strip metadata (all images)
    if pp.get("strip_metadata", True):
        img = strip_metadata(img)
        steps_applied.append("metadata stripped")

    # 4. JPEG compression
    quality = pp.get("jpeg_quality", 87)
    img = compress_jpeg(img, quality=quality)
    steps_applied.append(f"JPEG q={quality}")

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=quality, optimize=True)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "original_size": original_size,
        "final_size": img.size,
        "image_type": image_type,
        "steps": steps_applied,
    }


# ---------------------------------------------------------------------------
# Review Thumbnail Grid
# ---------------------------------------------------------------------------

def generate_review_grid(slideshow_dir: Path, output_path: Path = None) -> Path:
    """Generate a thumbnail grid of all slides for quick visual review."""
    images_dir = slideshow_dir / "images"
    if not images_dir.exists():
        return None

    image_files = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    if not image_files:
        return None

    # Load script text
    script_path = slideshow_dir / "script.txt"
    script_lines = []
    if script_path.exists():
        with open(script_path) as f:
            script_lines = f.read().strip().split("\n")

    # Grid layout
    thumb_w, thumb_h = 270, 480  # Quarter of 1080x1920
    padding = 20
    text_height = 60
    cols = min(len(image_files), 4)
    rows = math.ceil(len(image_files) / cols)

    grid_w = cols * (thumb_w + padding) + padding
    grid_h = rows * (thumb_h + text_height + padding) + padding + 40  # +40 for title

    grid = Image.new("RGB", (grid_w, grid_h), (30, 30, 30))
    draw = ImageDraw.Draw(grid)

    # Title
    title = slideshow_dir.name
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except (OSError, IOError):
        font = ImageFont.load_default()
        font_small = font

    draw.text((padding, 10), title, fill=(255, 255, 255), font=font)

    for i, img_path in enumerate(image_files):
        row = i // cols
        col = i % cols
        x = padding + col * (thumb_w + padding)
        y = 40 + padding + row * (thumb_h + text_height + padding)

        # Thumbnail
        try:
            thumb = Image.open(img_path).convert("RGB")
            thumb = thumb.resize((thumb_w, thumb_h), Image.LANCZOS)
            grid.paste(thumb, (x, y))
        except Exception:
            draw.rectangle([x, y, x + thumb_w, y + thumb_h], fill=(60, 60, 60))
            draw.text((x + 10, y + thumb_h // 2), "Error", fill=(255, 0, 0), font=font_small)

        # Slide number
        draw.text((x + 5, y + 5), f"#{i+1}", fill=(255, 255, 0), font=font_small)

        # Caption text below thumbnail (truncated)
        # Find matching script line
        slide_text = ""
        for line in script_lines:
            if line.startswith(f"SLIDE {i+1}:") or line.startswith(f"CTA SLIDE:"):
                slide_text = line.split(":", 1)[-1].strip()[:80]
                break
        if slide_text:
            # Word wrap at thumb_w
            words = slide_text.split()
            lines_wrapped = []
            current_line = ""
            for word in words:
                test = f"{current_line} {word}".strip()
                if len(test) > 35:
                    lines_wrapped.append(current_line)
                    current_line = word
                else:
                    current_line = test
            if current_line:
                lines_wrapped.append(current_line)

            for j, line in enumerate(lines_wrapped[:3]):
                draw.text(
                    (x + 5, y + thumb_h + 5 + j * 16),
                    line,
                    fill=(200, 200, 200),
                    font=font_small,
                )

    if output_path is None:
        output_path = slideshow_dir / "review_grid.jpg"

    grid.save(output_path, format="JPEG", quality=92)
    return output_path


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------

def process_batch(batch_dir: Path, config: dict = None, review_only: bool = False):
    """Process all images in a batch directory."""
    if config is None:
        config = load_config()

    batch_path = Path(batch_dir)
    if not batch_path.exists():
        print(f"[ERROR] Batch directory not found: {batch_path}")
        return

    slideshow_dirs = [d for d in batch_path.iterdir() if d.is_dir() and (d / "images").exists()]

    if not slideshow_dirs:
        print(f"[WARN] No slideshow directories found in {batch_path}")
        return

    print(f"Processing {len(slideshow_dirs)} slideshows in {batch_path.name}")

    for show_dir in sorted(slideshow_dirs):
        print(f"\n--- {show_dir.name} ---")

        if not review_only:
            # Load metadata to determine image types
            meta_path = show_dir / "metadata.json"
            specs_files = list(batch_path.glob("*_slideshows.json"))

            image_types = {}  # filename -> type
            if specs_files:
                with open(specs_files[0]) as f:
                    all_specs = json.load(f)
                for show in all_specs.get("slideshows", []):
                    if show["id"] == show_dir.name:
                        for slide in show.get("slides", []):
                            fname = f"{slide['slide_num']:02d}_{slide.get('image_note', 'slide').replace(' ', '_')[:30]}.jpg"
                            image_types[fname] = slide.get("image_type", "ai_lifestyle")

            # Process each image
            images_dir = show_dir / "images"
            processed_dir = show_dir / "processed"

            for img_path in sorted(images_dir.glob("*")):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    continue

                img_type = image_types.get(img_path.name, "ai_lifestyle")
                out_path = processed_dir / img_path.with_suffix(".jpg").name

                result = process_image(img_path, out_path, image_type=img_type, config=config)
                print(f"  ✓ {img_path.name} [{img_type}] → {', '.join(result['steps'])}")

        # Generate review grid
        review_path = generate_review_grid(show_dir)
        if review_path:
            print(f"  📋 Review grid: {review_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post-process slideshow images for TikTok/Instagram")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", help="Process all images in a batch directory")
    group.add_argument("--image", help="Process a single image file")
    parser.add_argument("--type", choices=["ai_lifestyle", "app_screenshot"], default="ai_lifestyle",
                        help="Image type (determines processing intensity)")
    parser.add_argument("--output", help="Output path (default: alongside input)")
    parser.add_argument("--review-only", action="store_true", help="Only generate review grids")

    args = parser.parse_args()
    config = load_config()

    if args.image:
        input_path = Path(args.image)
        output_path = Path(args.output) if args.output else input_path.parent / f"processed_{input_path.name}"
        result = process_image(input_path, output_path, image_type=args.type, config=config)
        print(f"Processed: {result['input']}")
        print(f"  → {result['output']}")
        print(f"  Steps: {', '.join(result['steps'])}")

    elif args.batch:
        process_batch(args.batch, config=config, review_only=args.review_only)


if __name__ == "__main__":
    main()
