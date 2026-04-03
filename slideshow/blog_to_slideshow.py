#!/usr/bin/env python3
"""
blog_to_slideshow.py — Extract slideshow specs from blog content using Claude API.

Takes a blog post (from the database or a JSON/HTML file) and generates
multiple slideshow specifications ready for image generation and assembly.

Usage:
  python blog_to_slideshow.py --blog-slug "how-to-build-a-supplement-stack"
  python blog_to_slideshow.py --blog-file ./content/my-post.html
  python blog_to_slideshow.py --batch  # Process all unprocessed published posts
"""

import argparse
import json
import os
import sys
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cmsbot.project import load_project_profile

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
            return json.load(f)
    elif path.suffix in (".html", ".htm", ".md"):
        with open(path) as f:
            content = f.read()
        return {
            "title": path.stem.replace("-", " ").title(),
            "slug": path.stem,
            "content": content,
            "category": "general",
            "tags": [],
        }
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")


def load_blog_from_api(slug: str, api_base: str) -> dict:
    """Fetch blog content from the web API."""
    import urllib.request
    url = f"{api_base}/api/blog/{slug}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("post", data)
    except Exception as e:
        print(f"[WARN] Could not fetch from API: {e}")
        return None


def strip_html(html: str) -> str:
    """Basic HTML tag stripping for content extraction."""
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

# ---------------------------------------------------------------------------
# Claude API Integration
# ---------------------------------------------------------------------------

def call_claude(system_prompt: str, user_prompt: str, model: str = "claude-sonnet-4-6") -> str:
    """Call the Anthropic Claude API. Requires ANTHROPIC_API_KEY env var."""
    try:
        import anthropic
    except ImportError:
        print("[ERROR] Install anthropic SDK: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY from env

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def generate_slideshow_specs(blog: dict, config: dict, hook_prompt: str) -> dict:
    """Use Claude to extract slideshow specs from a blog post."""

    formats_json = json.dumps(config["formats"], indent=2)
    banned_words = ", ".join(config["content_rules"]["banned_words"])
    cta_variants = "\n".join(f"  - {v}" for v in config["brand"]["cta_variants"])

    brand_name = config["brand"]["name"]
    tagline = config["brand"].get("tagline", "")
    website = config["brand"].get("urls", {}).get("website", "")

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
"""

    user_prompt = f"""Analyze this blog post and generate slideshow specifications.

BLOG TITLE: {blog['title']}
BLOG CATEGORY: {blog.get('category', 'general')}
BLOG TAGS: {json.dumps(blog.get('tags', []))}

BLOG CONTENT (stripped):
{strip_html(blog['content'])[:6000]}

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
  "source_blog": "{blog['slug']}",
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
          "image_note": "brief description of what the image should show"
        }}
      ],
      "cta_slide": {{
        "text_overlay": "natural CTA text",
        "image_type": "app_screenshot",
        "screenshot_ref": "which app screen to show"
      }},
      "caption": "post caption with #hashtags",
      "target_platforms": ["tiktok", "instagram"]
    }}
  ]
}}

Return ONLY valid JSON, no markdown code fences."""

    response = call_claude(system_prompt, user_prompt)

    # Try to parse JSON from response
    try:
        # Handle potential markdown fences
        json_str = response.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```\w*\n?", "", json_str)
            json_str = re.sub(r"\n?```$", "", json_str)
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[WARN] Failed to parse Claude response as JSON: {e}")
        print(f"[DEBUG] Raw response:\n{response[:500]}")
        return {"source_blog": blog["slug"], "slideshows": [], "error": str(e), "raw": response}

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
    slug = specs["source_blog"]
    output_path = batch_dir / f"{slug}_slideshows.json"
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
            "source_blog": slug,
            "generated_at": specs.get("generated_at", datetime.now().isoformat()),
            "platforms": show.get("target_platforms", ["tiktok", "instagram"]),
            "status": "generated",
            "warnings": validate_slideshow(show, config),
        }
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

def process_blog(blog: dict, config: dict, hook_prompt: str) -> Path:
    """Full pipeline: blog → slideshow specs → saved output."""
    print(f"\n{'='*60}")
    print(f"Processing: {blog['title']}")
    print(f"{'='*60}")

    # Generate specs
    print("[1/3] Generating slideshow specs with Claude...")
    specs = generate_slideshow_specs(blog, config, hook_prompt)

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

    save_processed_slug(blog["slug"])
    return batch_dir


def main():
    parser = argparse.ArgumentParser(description="Convert blog posts to TikTok/Instagram slideshow specs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--blog-slug", help="Fetch blog by slug from API")
    group.add_argument("--blog-file", help="Load blog from local file (JSON, HTML, or MD)")
    group.add_argument("--batch", action="store_true", help="Process all unprocessed published posts")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--project-file", default=None, help="Path to a CMSBOT project profile JSON file")
    parser.add_argument("--force", action="store_true", help="Re-process even if already done")

    args = parser.parse_args()
    config = config_from_project(args.project_file)
    hook_prompt = load_hook_prompt()
    api_base = args.api_base or config["brand"].get("urls", {}).get("website", "")

    if args.blog_file:
        blog = load_blog_from_file(args.blog_file)
        process_blog(blog, config, hook_prompt)

    elif args.blog_slug:
        if not api_base:
            print("[ERROR] Provide --api-base or set project website in --project-file")
            sys.exit(1)
        blog = load_blog_from_api(args.blog_slug, api_base)
        if blog:
            process_blog(blog, config, hook_prompt)
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
                process_blog(full, config, hook_prompt)


if __name__ == "__main__":
    main()
