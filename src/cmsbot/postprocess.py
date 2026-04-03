"""Blog image post-processing — transforms raw DALL-E output into natural-looking photos.

Adapts techniques from the slideshow pipeline (tools/slideshow-pipeline/postprocess.py)
with parameters tuned for editorial blog photography rather than TikTok/Instagram social.

Transform chain:
    1. Resize to blog dimensions (hero: 1500px, section: 1000px wide)
    2. Color grade (lift blacks, warm midtones, gentle highlight rolloff)
    3. Film grain (2.5% gaussian noise)
    4. Vignette (8% corner darkening)
    5. Chromatic aberration (1.2px subtle lens fringing)
    6. Micro-rotation (0.3-0.5 degrees)
    7. Metadata strip (clean EXIF)
    8. JPEG compression (quality 87)
"""

from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Blog-specific defaults
# ---------------------------------------------------------------------------

HERO_WIDTH = 1500
SECTION_WIDTH = 1000

FILM_GRAIN_INTENSITY = 2.5  # percentage
JPEG_QUALITY = 87
MICRO_ROTATION_MIN = 0.3  # degrees
MICRO_ROTATION_MAX = 0.5
VIGNETTE_INTENSITY = 0.08  # 8% darkening at corners
COLOR_LIFT_BLACKS = 8  # levels (0-255)
CHROMATIC_ABERRATION_PX = 1.2  # max pixel shift for CA simulation
COLOR_WARM_R = 3  # midtone red shift
COLOR_WARM_G = 1  # midtone green shift
HIGHLIGHT_THRESHOLD = 230  # start rolloff above this


# ---------------------------------------------------------------------------
# Transform functions
# ---------------------------------------------------------------------------

def resize_for_blog(img: Image.Image, placement: str) -> Image.Image:
    """Resize to blog-appropriate width, maintaining aspect ratio. Never upscales."""
    target_w = HERO_WIDTH if placement == "hero" else SECTION_WIDTH

    if img.width <= target_w:
        return img

    ratio = target_w / img.width
    target_h = int(img.height * ratio)
    return img.resize((target_w, target_h), Image.LANCZOS)


def apply_color_grade(img: Image.Image) -> Image.Image:
    """Lift blacks, warm midtones, gentle highlight rolloff. Mimics real camera color science."""
    arr = np.array(img, dtype=np.float32)

    # Lift blacks: add flat offset so shadows never hit pure black
    arr += COLOR_LIFT_BLACKS

    # Warm midtones: apply warmth proportional to a bell curve around midtones
    luminance = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    # Bell curve centered at 128, sigma ~60
    midtone_mask = np.exp(-((luminance - 128) ** 2) / (2 * 60 ** 2))

    arr[:, :, 0] += COLOR_WARM_R * midtone_mask  # red
    arr[:, :, 1] += COLOR_WARM_G * midtone_mask  # green

    # Highlight rolloff: compress values above threshold toward 245
    highlight_mask = arr > HIGHLIGHT_THRESHOLD
    arr[highlight_mask] = HIGHLIGHT_THRESHOLD + (arr[highlight_mask] - HIGHLIGHT_THRESHOLD) * 0.4

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def add_film_grain(img: Image.Image, intensity: float = FILM_GRAIN_INTENSITY) -> Image.Image:
    """Add gaussian noise simulating camera sensor grain."""
    arr = np.array(img, dtype=np.float32)
    noise_strength = (intensity / 100.0) * 255
    noise = np.random.normal(0, noise_strength, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


def add_vignette(img: Image.Image, intensity: float = VIGNETTE_INTENSITY) -> Image.Image:
    """Radial darkening at corners, mimicking lens optics."""
    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    # Distance from center, normalized to [0, 1] at corners
    y, x = np.ogrid[:h, :w]
    cy, cx = h / 2, w / 2
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max_dist

    # Power curve for accelerating darkening toward edges
    vignette = 1.0 - intensity * (dist ** 1.5)
    vignette = vignette[:, :, np.newaxis]  # broadcast across RGB

    return Image.fromarray(np.clip(arr * vignette, 0, 255).astype(np.uint8))


def micro_rotate(
    img: Image.Image,
    min_deg: float = MICRO_ROTATION_MIN,
    max_deg: float = MICRO_ROTATION_MAX,
) -> Image.Image:
    """Slight random rotation to break AI-perfect alignment."""
    angle = random.uniform(min_deg, max_deg) * random.choice([-1, 1])
    return img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(0, 0, 0))


def add_chromatic_aberration(
    img: Image.Image,
    max_shift: float = CHROMATIC_ABERRATION_PX,
) -> Image.Image:
    """Simulate subtle lens chromatic aberration by shifting R and B channels outward.

    Real lenses produce slight color fringing at edges — this breaks the
    too-clean look of AI-generated images.
    """
    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    # Build a radial distance map (0 at center, 1 at corners)
    y, x = np.ogrid[:h, :w]
    cy, cx = h / 2, w / 2
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max_dist

    # Shift amount increases with distance from center (like real lenses)
    shift_map = dist * max_shift

    # Apply sub-pixel shifts to R (outward) and B (inward) channels
    from scipy.ndimage import map_coordinates

    yy, xx = np.mgrid[:h, :w].astype(np.float32)

    # Direction vectors from center
    dy = (yy - cy) / (max_dist + 1e-6)
    dx = (xx - cx) / (max_dist + 1e-6)

    # Red channel: shift outward
    r_y = yy + dy * shift_map
    r_x = xx + dx * shift_map
    arr[:, :, 0] = map_coordinates(arr[:, :, 0], [r_y, r_x], order=1, mode='reflect')

    # Blue channel: shift inward
    b_y = yy - dy * shift_map * 0.7  # asymmetric for realism
    b_x = xx - dx * shift_map * 0.7
    arr[:, :, 2] = map_coordinates(arr[:, :, 2], [b_y, b_x], order=1, mode='reflect')

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def strip_metadata(img: Image.Image) -> Image.Image:
    """Strip all EXIF/metadata by creating a clean copy."""
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))
    return clean


def compress_jpeg(img: Image.Image, quality: int = JPEG_QUALITY) -> Image.Image:
    """Apply JPEG compression artifacts by round-tripping through a buffer."""
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    buffer.seek(0)
    return Image.open(buffer).copy()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def postprocess_blog_image(
    raw_path: Path,
    output_path: Path,
    placement: str = "section",
) -> dict:
    """Full post-processing pipeline for a single blog image.

    Args:
        raw_path: Path to raw DALL-E PNG.
        output_path: Path for the final JPEG output.
        placement: "hero" or "section-N" — determines target dimensions.

    Returns:
        Dict with processing steps applied (for logging).
    """
    img: Image.Image | None = None
    try:
        img = Image.open(raw_path).convert("RGB")
        original_size = img.size
        steps = []

        # 1. Resize
        img = resize_for_blog(img, placement)
        if img.size != original_size:
            steps.append(f"resized {img.size[0]}x{img.size[1]}")

        # 2. Color grade
        img = apply_color_grade(img)
        steps.append("color graded")

        # 3. Film grain
        img = add_film_grain(img)
        steps.append(f"grain {FILM_GRAIN_INTENSITY}%")

        # 4. Vignette
        img = add_vignette(img)
        steps.append(f"vignette {int(VIGNETTE_INTENSITY * 100)}%")

        # 5. Chromatic aberration
        try:
            img = add_chromatic_aberration(img)
            steps.append(f"CA {CHROMATIC_ABERRATION_PX}px")
        except ImportError:
            pass  # scipy not installed — skip CA

        # 6. Micro-rotation
        img = micro_rotate(img)
        steps.append("micro-rotated")

        # 7. Strip metadata
        img = strip_metadata(img)
        steps.append("metadata stripped")

        # 8. JPEG compression
        img = compress_jpeg(img)
        steps.append(f"JPEG q={JPEG_QUALITY}")

        # Save final
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)

        return {"steps": steps, "output": str(output_path)}

    except Exception as e:
        # Graceful fallback: preserve the expected output path when possible.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if img is not None:
            img.save(output_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return {"steps": [f"FALLBACK JPEG (error: {e})"], "output": str(output_path)}

        import shutil
        fallback_path = output_path.with_suffix(".png")
        shutil.copy2(raw_path, fallback_path)
        return {"steps": [f"FALLBACK PNG (error: {e})"], "output": str(fallback_path)}


def postprocess_blog_image_bytes(
    image_bytes: bytes,
    output_path: Path,
    placement: str = "section",
) -> dict:
    """Post-process raw image bytes (from any provider) and save as JPEG.

    Same pipeline as postprocess_blog_image but accepts bytes instead of a file path.
    Used by the multi-candidate ImageDirector.
    """
    img: Image.Image | None = None
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        original_size = img.size
        steps = []

        img = resize_for_blog(img, placement)
        if img.size != original_size:
            steps.append(f"resized {img.size[0]}x{img.size[1]}")

        img = apply_color_grade(img)
        steps.append("color graded")

        img = add_film_grain(img)
        steps.append(f"grain {FILM_GRAIN_INTENSITY}%")

        img = add_vignette(img)
        steps.append(f"vignette {int(VIGNETTE_INTENSITY * 100)}%")

        try:
            img = add_chromatic_aberration(img)
            steps.append(f"CA {CHROMATIC_ABERRATION_PX}px")
        except ImportError:
            pass

        img = micro_rotate(img)
        steps.append("micro-rotated")

        img = strip_metadata(img)
        steps.append("metadata stripped")

        img = compress_jpeg(img)
        steps.append(f"JPEG q={JPEG_QUALITY}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)

        return {"steps": steps, "output": str(output_path)}

    except Exception as e:
        # Preserve the expected output path when PIL can still decode the image.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if img is not None:
            img.save(output_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return {"steps": [f"FALLBACK JPEG (error: {e})"], "output": str(output_path)}

        fallback_path = output_path.with_suffix(".png")
        fallback_path.write_bytes(image_bytes)
        return {"steps": [f"FALLBACK PNG (error: {e})"], "output": str(fallback_path)}
