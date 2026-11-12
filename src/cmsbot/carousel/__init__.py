"""Carousel slides: a slide spec plus a project's brand kit in, 1080x1350 HTML and PNG out.

Text is laid out as real type and screenshotted with headless Chrome; an image model never draws
words. The same HTML is used for the live preview in the Hub review page and for the final PNGs.

Slide spec (the body of a `carousel` item):
  {"topic_tag": "...", "eyebrow": "...", "sources": "...", "theme": "<name in brand visual.themes>",
   "slides": [{"type": "cover|point|mechanism|evidence|fit|verdict|cta", "headline": ..., "body": ...,
               "background": "<optional image URL>"}], "caption": "..."}
"""

from __future__ import annotations

import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

WIDTH, HEIGHT = 1080, 1350
DEFAULT_THEME = {
    "glow": "#1f2a44", "base": "#0c0f18", "accent": "#8fb4ff", "accent2": "#f2c46b",
    "ink": "#eef1f8", "muted": "#a6adc2",
}
DEFAULT_FONTS = {
    "headline": '"Newsreader", Georgia, serif',
    "body": '"Hanken Grotesk", "Helvetica Neue", Arial, sans-serif',
    "label": '"IBM Plex Mono", Menlo, monospace',
}
DEFAULT_FONT_CSS = (
    "https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500"
    "&family=IBM+Plex+Mono:wght@400;500&family=Hanken+Grotesk:wght@400;500;600&display=block"
)
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_GAP_NOTE = re.compile(r"\s*\[[^\]\n]{6,}\]")
_SAFE_URL = re.compile(r"^(https?://|/media/)[^\s\"'()<>]+$")


class CarouselError(RuntimeError):
    pass


def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def available_styles() -> list[str]:
    return sorted(p.name[:-4] for p in resources.files("cmsbot.carousel").joinpath("styles").iterdir() if p.name.endswith(".css"))


def strip_gap_notes(value: Any) -> Any:
    """[Bracketed] reviewer notes stay on the item as gaps; they never appear on an image."""
    if isinstance(value, str):
        return _GAP_NOTE.sub("", value).strip()
    if isinstance(value, list):
        return [strip_gap_notes(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_gap_notes(v) for k, v in value.items()}
    return value


def normalize(spec: dict[str, Any], *, cta: str = "") -> dict[str, Any]:
    """Accepts the typed spec, or a plain [{title, body}] list (cover + points + closing slide)."""
    slides = [s for s in (spec.get("slides") or []) if isinstance(s, dict)]
    if not slides:
        raise CarouselError("The carousel has no slides.")
    if all("type" in s for s in slides):
        return {**spec, "slides": slides}
    out: list[dict[str, Any]] = []
    for index, slide in enumerate(slides):
        headline = slide.get("headline") or slide.get("title") or ""
        text = slide.get("body") or slide.get("dek") or ""
        extra = {k: slide[k] for k in ("background",) if slide.get(k)}
        out.append({"type": "cover", "headline": headline, "dek": text, **extra} if index == 0
                   else {"type": "point", "headline": headline, "body": text, **extra})
    if cta or spec.get("cta"):
        out.append({"type": "cta", "headline": spec.get("cta_headline") or spec.get("cta") or cta, "button": cta or spec.get("cta")})
    return {**spec, "slides": out}


def slides_hash(spec: dict[str, Any], style: str = "editorial") -> str:
    """Identifies the slide text a render was made from, so stale images are detectable."""
    relevant = {k: spec.get(k) for k in ("slides", "topic_tag", "eyebrow", "sources", "theme")}
    return hashlib.sha256(json.dumps([style, relevant], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def resolve_theme(brand: dict[str, Any] | None, name: str | None = None) -> dict[str, Any]:
    """Colours, fonts and wordmark from the brand kit's `visual` section, with safe defaults."""
    visual = (brand or {}).get("visual") or {}
    themes = visual.get("themes") or {}
    chosen = themes.get(name) if name else None
    base_colors = visual.get("colors") if isinstance(visual.get("colors"), dict) else {}
    merged = {**base_colors, **(chosen if isinstance(chosen, dict) else {})}
    colors = {k: v for k, v in merged.items() if k in DEFAULT_THEME and isinstance(v, str) and _COLOR.match(v)}
    custom_fonts = visual.get("fonts") if isinstance(visual.get("fonts"), dict) else {}
    fonts = {**DEFAULT_FONTS, **{k: re.sub(r"[;{}<>]", "", str(v)) for k, v in custom_fonts.items() if k in DEFAULT_FONTS}}
    font_css = visual.get("font_css_url") or DEFAULT_FONT_CSS
    logo = visual.get("logo_url") or ""
    return {
        "colors": {**DEFAULT_THEME, **colors}, "fonts": fonts,
        "font_css": font_css if _SAFE_URL.match(font_css) else DEFAULT_FONT_CSS,
        "wordmark": visual.get("wordmark") or (brand or {}).get("name") or "",
        "logo": logo if _SAFE_URL.match(logo) else "",
        "footer": visual.get("footer") or "",
        "tag": (chosen or {}).get("tag", "") if isinstance(chosen, dict) else "",
    }


def _dots(strength: Any) -> str:
    try:
        level = max(0, min(3, int(strength or 0)))
    except (TypeError, ValueError):
        level = 0
    return "".join(f'<span class="dot{" on" if i < level else ""}"></span>' for i in range(3))


def _opt(tag: str, cls: str, value: Any) -> str:
    return f'<{tag} class="{cls}">{esc(value)}</{tag}>' if value else ""


def _body(slide: dict[str, Any], spec: dict[str, Any]) -> str:
    kind = slide.get("type", "point")
    headline = slide.get("headline")
    if kind == "cover":
        return (f'<div class="cover">{_opt("div", "eyebrow", slide.get("eyebrow") or spec.get("eyebrow"))}'
                f'<h1 class="headline xl">{esc(headline)}</h1>{_opt("p", "dek", slide.get("dek") or slide.get("body"))}</div>'
                '<div class="swipe">SWIPE <span class="arrow">&#8594;</span></div>')
    if kind == "mechanism":
        chain = '<span class="link"></span>'.join(f'<span class="node">{esc(n)}</span>' for n in slide.get("nodes") or [])
        return (f'<div class="point">{_opt("div", "eyebrow", slide.get("label") or "HOW IT WORKS")}{_opt("h2", "headline", headline)}'
                f'<div class="chain">{chain}</div>{_opt("p", "body", slide.get("note") or slide.get("body"))}</div>')
    if kind == "evidence":
        rows = "".join(
            f'<div class="ev-row"><div class="ev-claim">{esc(r.get("claim"))}</div><div class="ev-dots">{_dots(r.get("strength"))}</div>'
            f'<div class="ev-note">{esc(r.get("note"))}</div></div>' for r in slide.get("rows") or [] if isinstance(r, dict))
        return (f'<div class="point">{_opt("div", "eyebrow", slide.get("label") or "WHAT THE EVIDENCE SUPPORTS")}'
                f'{_opt("h2", "headline", headline)}<div class="evidence">{rows}</div></div>')
    if kind == "fit":
        def column(title: str, items: list[Any], cls: str) -> str:
            lis = "".join(
                f'<li><b>{esc(i.get("title") if isinstance(i, dict) else i)}</b>'
                f'{_opt("span", "", i.get("note")) if isinstance(i, dict) else ""}</li>' for i in items)
            return f'<div class="fit-col {cls}"><div class="eyebrow">{esc(title)}</div><ul>{lis}</ul></div>'
        return (f'<div class="point">{_opt("h2", "headline", headline)}<div class="fit">'
                f'{column(slide.get("good_label") or "A GOOD FIT", slide.get("good_fit") or [], "good")}'
                f'{column(slide.get("caution_label") or "CHECK FIRST", slide.get("cautions") or [], "caution")}</div></div>')
    if kind == "verdict":
        return (f'<div class="point verdict-wrap">{_opt("div", "eyebrow", slide.get("label") or "THE VERDICT")}'
                f'<div class="verdict"><h2 class="headline">{esc(headline)}</h2>{_opt("p", "body", slide.get("body"))}</div></div>')
    if kind == "cta":
        return (f'<div class="point cta"><h2 class="headline xl">{esc(headline)}</h2>{_opt("p", "dek", slide.get("body"))}'
                f'{_opt("div", "button", slide.get("button"))}</div>')
    strength = slide.get("strength")
    meter = (f'<div class="strength"><span class="mono-label">EVIDENCE</span>{_dots(strength)}'
             f'<span class="note">{esc(slide.get("strength_note"))}</span></div>') if strength is not None else ""
    return (f'<div class="point">{_opt("div", "eyebrow", slide.get("label"))}<h2 class="headline">{esc(headline)}</h2>'
            f'{_opt("p", "body", slide.get("body"))}{meter}</div>')


def slide_html(spec: dict[str, Any], index: int, theme: dict[str, Any], *, style: str = "editorial") -> str:
    """One complete HTML document for slide `index` (0-based)."""
    if style not in available_styles():
        raise CarouselError(f"Unknown carousel style '{style}'. Available: {', '.join(available_styles())}")
    css = resources.files("cmsbot.carousel").joinpath("styles", f"{style}.css").read_text(encoding="utf-8")
    slide = spec["slides"][index]
    variables = ";".join([*(f"--{k}:{v}" for k, v in theme["colors"].items()),
                          *(f"--font-{k}:{v}" for k, v in theme["fonts"].items())])
    background = str(slide.get("background") or "")
    has_bg = bool(_SAFE_URL.match(background))
    ground = f' style="background-image:url(&quot;{esc(background)}&quot;)"' if has_bg else ""
    logo = f'<img src="{esc(theme["logo"])}" alt="">' if theme["logo"] else ""
    tag = spec.get("topic_tag") or theme["tag"]
    footer = spec.get("sources") or theme["footer"]
    kind = re.sub(r"[^a-z]", "", str(slide.get("type", "point")))
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="{esc(theme['font_css'])}" rel="stylesheet">
<style>:root{{{variables}}}{css}</style></head>
<body><main class="slide kind-{kind}{' has-bg' if has_bg else ''}"{ground}>
  <header><div class="brand">{logo}{esc(theme['wordmark']).upper()}</div><div class="tag">{esc(tag)}</div></header>
  <section class="content">{_body(slide, spec)}</section>
  <footer><div class="sources">{esc(footer)}</div><div class="fig">{index + 1:02d} / {len(spec['slides']):02d}</div></footer>
</main></body></html>"""


def resolve_style(body: dict[str, Any], brand: dict[str, Any] | None = None, style: str | None = None) -> str:
    return style or body.get("style") or ((brand or {}).get("visual") or {}).get("carousel_style") or "editorial"


def build_slides(body: dict[str, Any], brand: dict[str, Any] | None = None, *, style: str | None = None) -> dict[str, Any]:
    """Everything needed to show or render a carousel: per-slide HTML plus the hash of what it was made from."""
    style = resolve_style(body, brand, style)
    cta = (((brand or {}).get("ctas") or [""])[0]) if isinstance((brand or {}).get("ctas"), list) else ""
    spec = strip_gap_notes(normalize(body, cta=cta))
    theme = resolve_theme(brand, body.get("theme") if isinstance(body.get("theme"), str) else None)
    return {"style": style, "hash": slides_hash(body, style), "width": WIDTH, "height": HEIGHT,
            "html": [slide_html(spec, i, theme, style=style) for i in range(len(spec["slides"]))]}


def find_chrome() -> str | None:
    shells = "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"
    full = "chromium-*/chrome-linux*/chrome"
    homes = ["/opt/pw-browsers", os.path.expanduser("~/.cache/ms-playwright"), os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")]
    candidates = [
        os.environ.get("CMSBOT_CHROME", ""),
        # The headless-shell build is preferred: it is small and exits reliably after a screenshot.
        *(p for home in homes if home for p in sorted(glob.glob(f"{home}/{shells}"), reverse=True)),
        *(shutil.which(name) or "" for name in ("chrome-headless-shell", "google-chrome", "chromium", "chromium-browser", "chrome")),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        *(p for home in homes if home for p in sorted(glob.glob(f"{home}/{full}"), reverse=True)),
    ]
    return next((path for path in candidates if path and Path(path).exists()), None)


def can_render() -> bool:
    return find_chrome() is not None


def screenshot(chrome: str, html_path: Path, png_path: Path, *, width: int = WIDTH, height: int = HEIGHT,
               transparent: bool = False) -> None:
    html_path, png_path = Path(html_path).resolve(), Path(png_path).resolve()
    last = ""
    for _ in range(2):  # headless Chrome occasionally stalls on a font fetch; retry once
        with tempfile.TemporaryDirectory() as profile:
            command = [
                chrome, *([] if "headless-shell" in Path(chrome).name else ["--headless=new"]), "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={profile}", "--force-device-scale-factor=1",
                f"--window-size={width},{height}", "--virtual-time-budget=4000", f"--screenshot={png_path}",
                *(["--default-background-color=00000000"] if transparent else []),
                html_path.as_uri(),
            ]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=45)
                last = result.stderr[-400:]
            except subprocess.TimeoutExpired:
                last = "timed out"
        if png_path.exists() and png_path.stat().st_size > 0:
            return
    raise CarouselError(f"Chrome did not produce {png_path.name}: {last}")


def render_pngs(body: dict[str, Any], brand: dict[str, Any] | None, out_dir: Path, *, style: str | None = None,
                media_root: Path | None = None) -> dict[str, Any]:
    """Writes slide-NN.png (and .html) to out_dir. `/media/...` backgrounds resolve against media_root."""
    chrome = find_chrome()
    if not chrome:
        raise CarouselError("No Chrome or Chromium found. Install one or set CMSBOT_CHROME to its path.")
    built = build_slides(body, brand, style=style)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("slide-*.*"):
        stale.unlink()
    files = []
    for number, document in enumerate(built["html"], start=1):
        if media_root is not None:
            document = document.replace("url(&quot;/media/", f"url(&quot;{media_root.resolve().as_uri()}/")
        html_path, png_path = out_dir / f"slide-{number:02d}.html", out_dir / f"slide-{number:02d}.png"
        html_path.write_text(document, encoding="utf-8")
        screenshot(chrome, html_path, png_path)
        files.append(png_path)
    return {"style": built["style"], "hash": built["hash"], "files": files}
