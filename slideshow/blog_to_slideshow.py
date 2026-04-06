#!/usr/bin/env python3
"""
blog_to_slideshow.py — Extract slideshow specs from blog content using Claude API.

Takes a blog post (from the database or a JSON/HTML file) and generates
multiple slideshow specifications ready for image generation and assembly.

Usage:
  python blog_to_slideshow.py --blog-slug "how-to-build-a-supplement-stack"
  python blog_to_slideshow.py --blog-file ./content/my-post.html
  python blog_to_slideshow.py --brief-file ./briefs/q2-launch.md
  python blog_to_slideshow.py --batch  # Process all unprocessed published posts
"""

import argparse
import asyncio
import json
import os
import sys
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cmsbot.project import load_project_profile
from cmsbot.config import Config
from cmsbot.providers import build_provider
from cmsbot.screenshots import (
    coerce_screenshot_refs,
    load_manifest,
    resolve_screenshot_dir,
    summarize_manifest_for_prompt,
)

CONFIG_PATH = SCRIPT_DIR / "config.json"
HOOK_PROMPT_PATH = SCRIPT_DIR / "prompts" / "hook_generation.md"
OUTPUT_DIR = Path(os.getenv("CMSBOT_SLIDESHOW_OUTPUT_DIR", str(SCRIPT_DIR / "output")))
PROCESSED_LOG = SCRIPT_DIR / ".processed_slugs.json"


def load_json_file(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def optional_override_path(env_var: str) -> Path | None:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return None

    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{env_var} does not exist: {path}")
    return path


def load_config():
    config = load_json_file(CONFIG_PATH)
    override_path = optional_override_path("CMSBOT_SLIDESHOW_CONFIG_FILE")
    if override_path is not None:
        config = merge_dicts(config, load_json_file(override_path))
    return config


def merge_dicts(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_from_project(project_file: str | None) -> dict:
    config = load_config()
    if not project_file:
        return config

    profile = load_project_profile(project_file)
    slideshow = profile.section("slideshow")
    content = profile.section("content")

    overrides = {
        "brand": {
            "name": profile.name,
            "tagline": slideshow.get("tagline") or profile.data.get("description", ""),
            "urls": {
                "website": profile.website,
                "blog": f"{profile.website.rstrip('/')}{profile.section('seo').get('blog_path_prefix', '/blog/')}" if profile.website else "",
            },
            "cta_variants": slideshow.get("cta_variants", []),
        },
        "content_rules": {
            "voice": slideshow.get("voice") or ", ".join(profile.data.get("voice", [])),
            "banned_words": slideshow.get("banned_words", []),
            "medical_disclaimer": "; ".join(content.get("disallowed_claims", [])) or config["content_rules"]["medical_disclaimer"],
        },
    }
    return merge_dicts(config, overrides)

def load_hook_prompt():
    hook_path = optional_override_path("CMSBOT_SLIDESHOW_HOOK_PROMPT_FILE") or HOOK_PROMPT_PATH
    with open(hook_path) as f:
        return f.read()

# ---------------------------------------------------------------------------
# Blog Content Loading
# ---------------------------------------------------------------------------

def load_blog_from_file(filepath: str) -> dict:
    """Load blog content from a local HTML or JSON file."""
    path = Path(filepath)
    if path.suffix == ".json":
        with open(path) as f:
            payload = json.load(f)
            payload.setdefault("source_type", "blog")
            payload.setdefault("slug", path.stem)
            payload.setdefault("title", path.stem.replace("-", " ").title())
            return payload
    elif path.suffix in (".html", ".htm", ".md"):
        with open(path) as f:
            content = f.read()
        return {
            "title": path.stem.replace("-", " ").title(),
            "slug": path.stem,
            "content": content,
            "category": "general",
            "tags": [],
            "source_type": "blog",
        }
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")


def load_brief_from_file(filepath: str) -> dict:
    """Load a standalone slideshow brief from a local text or JSON file."""
    path = Path(filepath)
    if path.suffix == ".json":
        with open(path) as f:
            payload = json.load(f)
        title = payload.get("title") or path.stem.replace("-", " ").title()
        source_id = payload.get("source_id") or payload.get("slug") or path.stem
        content = payload.get("content") or payload.get("brief") or ""
        return {
            "title": title,
            "slug": source_id,
            "content": content,
            "category": payload.get("category", "project"),
            "tags": payload.get("tags", []),
            "source_type": payload.get("source_type", "brief"),
        }

    if path.suffix not in (".md", ".txt"):
        raise ValueError(f"Unsupported brief type: {path.suffix}")

    with open(path) as f:
        content = f.read()
    return {
        "title": path.stem.replace("-", " ").title(),
        "slug": path.stem,
        "content": content,
        "category": "project",
        "tags": [],
        "source_type": "brief",
    }


def load_blog_from_api(slug: str, api_base: str) -> dict:
    """Fetch blog content from the web API."""
    import urllib.request
    url = f"{api_base}/api/blog/{slug}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            payload = data.get("post", data)
            if isinstance(payload, dict):
                payload.setdefault("source_type", "blog")
            return payload
    except Exception as e:
        print(f"[WARN] Could not fetch from API: {e}")
        return None


def strip_html(html: str) -> str:
    """Basic HTML tag stripping for content extraction."""
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def source_id_for(payload: dict[str, Any]) -> str:
    return str(payload.get("slug") or payload.get("source_id") or "slideshow-source")


def source_type_for(payload: dict[str, Any]) -> str:
    return str(payload.get("source_type") or "blog")

# ---------------------------------------------------------------------------
# LLM Integration
# ---------------------------------------------------------------------------

async def call_llm(
    system_prompt: str,
    user_prompt: str,
    provider_name: str,
    model_override: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Call the configured CMSBOT provider and return response text."""
    config = Config.from_env()
    provider = build_provider(provider_name, config)
    if model_override:
        provider.model = model_override

    response = await provider.generate_simple(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.content


def _strip_json_fences(raw: str) -> str:
    json_str = raw.strip()
    if json_str.startswith("```"):
        json_str = re.sub(r"^```\w*\n?", "", json_str)
        json_str = re.sub(r"\n?```$", "", json_str)
    return json_str.strip()


def _recover_truncated_specs(raw: str, source_id: str, source_type: str = "blog") -> dict | None:
    """Recover complete slideshow objects from a truncated JSON response."""
    json_str = _strip_json_fences(raw)
    decoder = json.JSONDecoder()

    start = json_str.find("{")
    if start != -1:
        try:
            payload, _ = decoder.raw_decode(json_str[start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    slideshows_key = json_str.find('"slideshows"')
    if slideshows_key == -1:
        return None

    array_start = json_str.find("[", slideshows_key)
    if array_start == -1:
        return None

    slideshows = []
    cursor = array_start + 1
    while cursor < len(json_str):
        while cursor < len(json_str) and json_str[cursor] in " \r\n\t,":
            cursor += 1
        if cursor >= len(json_str) or json_str[cursor] == "]":
            break

        try:
            item, end = decoder.raw_decode(json_str, cursor)
        except json.JSONDecodeError:
            break

        if isinstance(item, dict):
            slideshows.append(item)
        cursor = end

    if not slideshows:
        return None

    source_blog_match = re.search(r'"source_blog"\s*:\s*"([^"]+)"', json_str)
    source_id_match = re.search(r'"source_id"\s*:\s*"([^"]+)"', json_str)
    source_type_match = re.search(r'"source_type"\s*:\s*"([^"]+)"', json_str)
    generated_match = re.search(r'"generated_at"\s*:\s*"([^"]+)"', json_str)

    recovered = {
        "source_id": source_id_match.group(1) if source_id_match else source_id,
        "source_type": source_type_match.group(1) if source_type_match else source_type,
        "generated_at": generated_match.group(1) if generated_match else datetime.now().isoformat(),
        "slideshows": slideshows,
        "recovered_from_truncation": True,
    }
    if recovered["source_type"] == "blog":
        recovered["source_blog"] = source_blog_match.group(1) if source_blog_match else recovered["source_id"]
    return recovered


def generate_slideshow_specs(
    source: dict,
    config: dict,
    hook_prompt: str,
    provider_name: str,
    model_override: str | None = None,
    max_tokens: int = 4096,
    screenshot_prompt_block: str | None = None,
) -> dict:
    """Use an LLM provider to extract slideshow specs from source material."""

    formats_json = json.dumps(config["formats"], indent=2)
    banned_words = ", ".join(config["content_rules"]["banned_words"])
    cta_variants = "\n".join(f"  - {v}" for v in config["brand"]["cta_variants"])

    brand_name = config["brand"]["name"]
    tagline = config["brand"].get("tagline", "")
    website = config["brand"].get("urls", {}).get("website", "")
    source_id = source_id_for(source)
    source_type = source_type_for(source)
    source_label = "blog post" if source_type == "blog" else "project brief"

    system_prompt = f"""You are a TikTok/Instagram slideshow content strategist for {brand_name}.
Brand summary: {tagline}
Website: {website}

{hook_prompt}

IMPORTANT RULES:
- Never use these words: {banned_words}
- Each slide overlay text must be under {config['content_rules']['max_words_per_slide']} words
- {config['content_rules']['medical_disclaimer']}
- Voice: {config['content_rules']['voice']}

CTA variants to choose from (rotate these):
{cta_variants}

Available slideshow formats:
{formats_json}

Available hashtag pools:
{json.dumps(config['hashtag_pools'], indent=2)}

SCREENSHOT RULES:
{screenshot_prompt_block or "No validated screenshot manifest is available. Use ai_lifestyle and never invent screenshot_ref values."}
"""

    user_prompt = f"""Analyze this {source_label} and generate slideshow specifications.

SOURCE TYPE: {source_type}
SOURCE ID: {source_id}
SOURCE TITLE: {source['title']}
SOURCE CATEGORY: {source.get('category', 'general')}
SOURCE TAGS: {json.dumps(source.get('tags', []))}

SOURCE CONTENT (stripped):
{strip_html(source['content'])[:6000]}

---

TASK:
1. Identify 3-5 distinct angles/subtopics from this blog post
2. For each angle, pick the best slideshow format from the available formats
3. Generate complete slideshow specs

For each slideshow, output:
- A unique ID (kebab-case, descriptive)
- The format ID being used
- Hook text (first slide — must stop the scroll)
- 5-8 slides with overlay text and image direction
- A CTA slide
- Caption with 5-8 hashtags (mix from the pools)
- Platform notes (any differences for TikTok vs Instagram)

Output as a valid JSON object with this structure:
{{
  "source_id": "{source_id}",
  "source_type": "{source_type}",
  "generated_at": "{datetime.now().isoformat()}",
  "slideshows": [
    {{
      "id": "descriptive-kebab-id",
      "format": "format_id",
      "hook": "first slide text",
      "slides": [
        {{
          "slide_num": 1,
          "text_overlay": "the text for this slide",
          "image_type": "ai_lifestyle or app_screenshot",
          "image_note": "brief description of what the image should show",
          "screenshot_ref": "manifest key only when image_type is app_screenshot"
        }}
      ],
      "cta_slide": {{
        "text_overlay": "natural CTA text",
        "image_type": "ai_lifestyle or app_screenshot",
        "image_note": "brief description of what the image should show",
        "screenshot_ref": "manifest key only when image_type is app_screenshot"
      }},
      "caption": "post caption with #hashtags",
      "target_platforms": ["tiktok", "instagram"]
    }}
  ]
}}

Return ONLY valid JSON, no markdown code fences."""

    response = asyncio.run(
        call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            provider_name=provider_name,
            model_override=model_override,
            max_tokens=max_tokens,
        )
    )

    # Try to parse JSON from response
    try:
        json_str = _strip_json_fences(response)
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        recovered = _recover_truncated_specs(response, source_id, source_type)
        if recovered is not None:
            print(
                "[WARN] Recovered "
                f"{len(recovered.get('slideshows', []))} slideshow(s) from a truncated JSON response"
            )
            return recovered

        print(f"[WARN] Failed to parse provider response as JSON: {e}")
        print(f"[DEBUG] Raw response:\n{response[:500]}")
        parsed = {"source_id": source_id, "source_type": source_type, "slideshows": [], "error": str(e), "raw": response}

    parsed.setdefault("source_id", source_id)
    parsed.setdefault("source_type", source_type)
    if parsed["source_type"] == "blog":
        parsed.setdefault("source_blog", parsed["source_id"])
    return parsed

# ---------------------------------------------------------------------------
# Quality Checks
# ---------------------------------------------------------------------------

def validate_slideshow(slideshow: dict, config: dict) -> list[str]:
    """Run automated quality checks on a slideshow spec. Returns list of warnings."""
    warnings = []
    banned = config["content_rules"]["banned_words"]
    max_words = config["content_rules"]["max_words_per_slide"]

    # Check all text fields
    all_texts = [slideshow.get("hook", "")]
    for slide in slideshow.get("slides", []):
        all_texts.append(slide.get("text_overlay", ""))
    if "cta_slide" in slideshow:
        all_texts.append(slideshow["cta_slide"].get("text_overlay", ""))

    for i, text in enumerate(all_texts):
        # Word count check
        word_count = len(text.split())
        if word_count > max_words:
            warnings.append(f"Slide {i}: {word_count} words (max {max_words})")

        # Banned words check
        text_lower = text.lower()
        for word in banned:
            if word.lower() in text_lower:
                warnings.append(f"Slide {i}: contains banned word '{word}'")

    # Slide count check
    slide_count = len(slideshow.get("slides", []))
    if slide_count < 4:
        warnings.append(f"Only {slide_count} slides (recommend 5-8)")
    elif slide_count > 10:
        warnings.append(f"{slide_count} slides may be too many (recommend 5-8)")

    return warnings

# ---------------------------------------------------------------------------
# Output Management
# ---------------------------------------------------------------------------

def save_batch(specs: dict, config: dict) -> Path:
    """Save slideshow specs to the output directory."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    batch_dir = OUTPUT_DIR / f"batch_{date_str}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Save full specs
    source_id = specs.get("source_id") or specs.get("source_blog") or "slideshow-source"
    source_type = specs.get("source_type") or ("blog" if specs.get("source_blog") else "brief")
    output_path = batch_dir / f"{source_id}_slideshows.json"
    with open(output_path, "w") as f:
        json.dump(specs, f, indent=2)

    # Save individual slideshow folders with scripts
    for show in specs.get("slideshows", []):
        show_dir = batch_dir / show["id"]
        show_dir.mkdir(exist_ok=True)
        (show_dir / "images").mkdir(exist_ok=True)

        # Write the slide script (text to type natively in TikTok)
        script_lines = []
        for slide in show.get("slides", []):
            script_lines.append(f"SLIDE {slide['slide_num']}: {slide['text_overlay']}")
            script_lines.append(f"  [Image: {slide.get('image_type', 'ai_lifestyle')} — {slide.get('image_note', '')}]")
            script_lines.append("")
        if "cta_slide" in show:
            script_lines.append(f"CTA SLIDE: {show['cta_slide']['text_overlay']}")
            script_lines.append(f"  [Image: {show['cta_slide'].get('image_type', 'app_screenshot')} — {show['cta_slide'].get('screenshot_ref', '')}]")

        with open(show_dir / "script.txt", "w") as f:
            f.write("\n".join(script_lines))

        # Write caption
        with open(show_dir / "caption.txt", "w") as f:
            f.write(show.get("caption", ""))

        # Write metadata
        metadata = {
            "id": show["id"],
            "format": show.get("format", ""),
            "source_id": source_id,
            "source_type": source_type,
            "generated_at": specs.get("generated_at", datetime.now().isoformat()),
            "platforms": show.get("target_platforms", ["tiktok", "instagram"]),
            "status": "generated",
            "warnings": validate_slideshow(show, config),
        }
        if source_type == "blog":
            metadata["source_blog"] = specs.get("source_blog") or source_id
        with open(show_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

    return batch_dir


def load_processed_slugs() -> set:
    if PROCESSED_LOG.exists():
        with open(PROCESSED_LOG) as f:
            return set(json.load(f))
    return set()


def save_processed_slug(slug: str):
    slugs = load_processed_slugs()
    slugs.add(slug)
    with open(PROCESSED_LOG, "w") as f:
        json.dump(sorted(slugs), f, indent=2)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_source(
    source: dict,
    config: dict,
    hook_prompt: str,
    provider_name: str,
    model_override: str | None = None,
    max_tokens: int = 4096,
    screenshot_dir: str | Path | None = None,
    screenshot_manifest: dict | None = None,
    screenshot_refs: list[str] | None = None,
) -> Path:
    """Full pipeline: source material → slideshow specs → saved output."""
    print(f"\n{'='*60}")
    print(f"Processing: {source['title']}")
    print(f"{'='*60}")

    # Generate specs
    provider_label = model_override or provider_name
    print(f"[1/3] Generating slideshow specs with {provider_label} (max_tokens={max_tokens})...")
    specs = generate_slideshow_specs(
        source,
        config,
        hook_prompt,
        provider_name=provider_name,
        model_override=model_override,
        max_tokens=max_tokens,
        screenshot_prompt_block=summarize_manifest_for_prompt(
            {
                key: value
                for key, value in (screenshot_manifest or {}).get("entries", {}).items()
                if not screenshot_refs or key in screenshot_refs
            }
        ),
    )
    if screenshot_dir:
        specs = coerce_screenshot_refs(
            specs,
            screenshot_dir=screenshot_dir,
            manifest=screenshot_manifest,
            allowed_refs=screenshot_refs,
        )

    slideshow_count = len(specs.get("slideshows", []))
    print(f"[2/3] Generated {slideshow_count} slideshows")

    # Validate
    total_warnings = 0
    for show in specs.get("slideshows", []):
        warnings = validate_slideshow(show, config)
        total_warnings += len(warnings)
        if warnings:
            print(f"  ⚠ {show['id']}: {len(warnings)} warnings")
            for w in warnings:
                print(f"    - {w}")

    # Save
    batch_dir = save_batch(specs, config)
    print(f"[3/3] Saved to {batch_dir}")

    if total_warnings == 0:
        print("✓ All quality checks passed")

    if source_type_for(source) == "blog":
        save_processed_slug(source_id_for(source))
    return batch_dir


def main():
    parser = argparse.ArgumentParser(description="Convert blog posts to TikTok/Instagram slideshow specs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--blog-slug", help="Fetch blog by slug from API")
    group.add_argument("--blog-file", help="Load blog from local file (JSON, HTML, or MD)")
    group.add_argument("--brief-file", help="Load a standalone slideshow brief (JSON, MD, or TXT)")
    group.add_argument("--batch", action="store_true", help="Process all unprocessed published posts")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--project-file", default=None, help="Path to a CMSBOT project profile JSON file")
    parser.add_argument("--force", action="store_true", help="Re-process even if already done")
    parser.add_argument("--provider", default="anthropic", help="LLM provider for spec generation")
    parser.add_argument("--model", default=None, help="Model override for the provider")
    parser.add_argument("--max-tokens", type=int, default=16384, help="Max output tokens")

    args = parser.parse_args()
    config = config_from_project(args.project_file)
    hook_prompt = load_hook_prompt()
    api_base = args.api_base or config["brand"].get("urls", {}).get("website", "")
    screenshot_dir = resolve_screenshot_dir(
        project_file=args.project_file,
        default_dir=SCRIPT_DIR / "screenshots",
    )
    screenshot_manifest = load_manifest(screenshot_dir)
    screenshot_refs: list[str] | None = None
    if args.project_file:
        project_profile = load_project_profile(args.project_file)
        screenshot_refs = list(project_profile.section("slideshow").get("screenshot_refs", []))

    if args.blog_file:
        blog = load_blog_from_file(args.blog_file)
        process_source(
            blog,
            config,
            hook_prompt,
            provider_name=args.provider,
            model_override=args.model,
            max_tokens=args.max_tokens,
            screenshot_dir=screenshot_dir,
            screenshot_manifest=screenshot_manifest,
            screenshot_refs=screenshot_refs,
        )

    elif args.brief_file:
        brief = load_brief_from_file(args.brief_file)
        process_source(
            brief,
            config,
            hook_prompt,
            provider_name=args.provider,
            model_override=args.model,
            max_tokens=args.max_tokens,
            screenshot_dir=screenshot_dir,
            screenshot_manifest=screenshot_manifest,
            screenshot_refs=screenshot_refs,
        )

    elif args.blog_slug:
        if not api_base:
            print("[ERROR] Provide --api-base or set project website in --project-file")
            sys.exit(1)
        blog = load_blog_from_api(args.blog_slug, api_base)
        if blog:
            process_source(
                blog,
                config,
                hook_prompt,
                provider_name=args.provider,
                model_override=args.model,
                max_tokens=args.max_tokens,
                screenshot_dir=screenshot_dir,
                screenshot_manifest=screenshot_manifest,
                screenshot_refs=screenshot_refs,
            )
        else:
            print(f"[ERROR] Could not load blog: {args.blog_slug}")
            sys.exit(1)

    elif args.batch:
        # Fetch all published posts from API
        if not api_base:
            print("[ERROR] Provide --api-base or set project website in --project-file")
            sys.exit(1)
        import urllib.request
        try:
            url = f"{api_base}/api/blog"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                posts = data.get("posts", data) if isinstance(data, dict) else data
        except Exception as e:
            print(f"[ERROR] Could not fetch blog list: {e}")
            sys.exit(1)

        processed = load_processed_slugs() if not args.force else set()
        to_process = [p for p in posts if p.get("slug") not in processed]

        print(f"Found {len(posts)} total posts, {len(to_process)} unprocessed")

        for post in to_process:
            # Fetch full content
            full = load_blog_from_api(post["slug"], api_base)
            if full:
                process_source(
                    full,
                    config,
                    hook_prompt,
                    provider_name=args.provider,
                    model_override=args.model,
                    max_tokens=args.max_tokens,
                    screenshot_dir=screenshot_dir,
                    screenshot_manifest=screenshot_manifest,
                    screenshot_refs=screenshot_refs,
                )


if __name__ == "__main__":
    main()
