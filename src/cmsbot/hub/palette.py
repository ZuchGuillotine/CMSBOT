"""Colours for a brand kit: read them from a picture or a site's stylesheets, and turn a handful of
colours into the six roles the renderers use (background, glow, text, muted text, two accents)."""

from __future__ import annotations

import colorsys
import io
import re
from collections import Counter
from typing import Any

ROLES = ("base", "glow", "ink", "muted", "accent", "accent2")
HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def normalise(value: str) -> str:
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if not re.fullmatch(r"#[0-9a-f]{6}", value):
        raise ValueError(f"'{value}' is not a colour like #1a2b3c.")
    return value


def _rgb(value: str) -> tuple[float, float, float]:
    value = normalise(value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    x, y = _rgb(a), _rgb(b)
    return _hex(tuple(x[i] + (y[i] - x[i]) * amount for i in range(3)))  # type: ignore[arg-type]


def luminance(value: str) -> float:
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(value)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _saturation(value: str) -> float:
    return colorsys.rgb_to_hsv(*_rgb(value))[1]


def _hue(value: str) -> float:
    return colorsys.rgb_to_hsv(*_rgb(value))[0] * 360


def from_image(data: bytes, count: int = 8) -> list[dict[str, Any]]:
    """The main colours of a picture, most common first. Flat swatches come back exactly; photographs
    come back as the average of each cluster of similar pixels."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGBA")
        flat = Image.new("RGBA", image.size, (255, 255, 255, 255))
        flat.alpha_composite(image)
        small = flat.convert("RGB")
        scale = max(small.width, small.height) / 140
        if scale > 1:  # nearest-neighbour keeps swatch colours pure instead of blending their edges
            small = small.resize((max(1, round(small.width / scale)), max(1, round(small.height / scale))), Image.Resampling.NEAREST)
        pixels = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    bins: dict[tuple[int, int, int], list[int]] = {}
    for r, g, b in pixels:
        entry = bins.setdefault((r >> 4, g >> 4, b >> 4), [0, 0, 0, 0])
        entry[0] += 1
        entry[1] += r
        entry[2] += g
        entry[3] += b
    found = [{"hex": f"#{t[1] // t[0]:02x}{t[2] // t[0]:02x}{t[3] // t[0]:02x}", "share": t[0] / len(pixels)}
             for t in sorted(bins.values(), reverse=True)[:40]]
    merged: list[dict[str, Any]] = []
    for colour in found:  # fold near-duplicates (gradients, compression noise) into the commoner one
        near = next((m for m in merged if sum(abs(x - y) for x, y in zip(_rgb(m["hex"]), _rgb(colour["hex"]))) < 0.16), None)
        if near:
            near["share"] += colour["share"]
        else:
            merged.append(colour)
    kept = [c for c in merged if c["share"] >= 0.01] or merged[:1]
    return [{"hex": c["hex"], "share": round(c["share"], 3)} for c in sorted(kept, key=lambda c: -c["share"])[:count]]


def from_css(text: str, count: int = 8) -> list[dict[str, Any]]:
    tally = Counter(normalise(match) for match in HEX.findall(text))
    total = sum(tally.values()) or 1
    return [{"hex": value, "share": round(hits / total, 3)} for value, hits in tally.most_common(count)]


def suggest_theme(colours: list[dict[str, Any]]) -> dict[str, str]:
    """Assign found colours to roles. A starting point for a person to adjust, not a verdict."""
    values = [c["hex"] for c in colours] or ["#0c0f18", "#eef1f8", "#8fb4ff"]
    by_light = sorted(values, key=luminance)
    dominant_is_light = luminance(values[0]) > 0.45
    base, ink = (by_light[-1], by_light[0]) if dominant_is_light else (by_light[0], by_light[-1])
    if contrast(base, ink) < 4.5:
        ink = "#14161a" if luminance(base) > 0.4 else "#f4f5f7"
    vivid = sorted((v for v in values if v not in (base, ink) and _saturation(v) > 0.2 and contrast(v, base) >= 2),
                   key=lambda v: -_saturation(v))
    accent = vivid[0] if vivid else mix(ink, base, 0.25)
    second = next((v for v in vivid[1:] if min(abs(_hue(v) - _hue(accent)), 360 - abs(_hue(v) - _hue(accent))) > 30), None)
    if second is None:
        h, s, v = colorsys.rgb_to_hsv(*_rgb(accent))
        second = _hex(colorsys.hsv_to_rgb((h + 0.42) % 1, max(0.35, s * 0.8), max(0.6, v)))
    return {"base": base, "glow": mix(base, accent, 0.22), "ink": ink, "muted": mix(ink, base, 0.38), "accent": accent, "accent2": second}
