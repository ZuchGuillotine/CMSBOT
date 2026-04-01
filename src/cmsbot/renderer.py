"""HTML renderer — converts final draft + images into production-ready HTML."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cmsbot.models import ContentType, ImageSpec

TEMPLATES_DIR = Path(__file__).parent / "templates"


class Renderer:
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )

    def render(
        self,
        content: str,
        images: list[ImageSpec],
        content_type: ContentType,
        task_prompt: str,
    ) -> str:
        template_name = f"{content_type.value}.html"
        template = self.env.get_template(template_name)

        # Convert markdown-ish content to HTML
        html_body = _markdown_to_html(content, images)

        # Extract a title from the content (first # or ## heading, or first line)
        title = _extract_title(content) or task_prompt[:80]

        # Extract description for meta tags
        description = _extract_description(content)

        # Find hero image
        hero = next((img for img in images if img.placement == "hero" and img.file_path), None)

        return template.render(
            title=title,
            description=description,
            body=html_body,
            hero_image=hero,
            images=images,
            task_prompt=task_prompt,
        )


def _markdown_to_html(content: str, images: list[ImageSpec]) -> str:
    """Convert markdown-style content to HTML with image insertion."""
    lines = content.split("\n")
    html_parts: list[str] = []
    in_list = False
    in_code_block = False
    section_count = 0

    for line in lines:
        stripped = line.strip()

        # Code blocks
        if stripped.startswith("```"):
            if in_code_block:
                html_parts.append("</code></pre>")
                in_code_block = False
            else:
                lang = stripped[3:].strip()
                html_parts.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                in_code_block = True
            continue

        if in_code_block:
            html_parts.append(_escape_html(line))
            continue

        # Close list if needed
        if in_list and not stripped.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            html_parts.append("</ul>")
            in_list = False

        # Empty line
        if not stripped:
            continue

        # Headers
        if stripped.startswith("### "):
            html_parts.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            section_count += 1
            html_parts.append(f"<h2>{_inline_format(stripped[3:])}</h2>")
            # Insert section image if one exists for this section
            section_img = next(
                (img for img in images if img.placement == f"section-{section_count}" and img.file_path),
                None,
            )
            if section_img:
                html_parts.append(
                    f'<figure class="section-image">'
                    f'<img src="{section_img.file_path}" alt="{_escape_html(section_img.alt_text)}" loading="lazy">'
                    f'<figcaption>{_escape_html(section_img.alt_text)}</figcaption>'
                    f'</figure>'
                )
            continue
        if stripped.startswith("# "):
            html_parts.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
            continue

        # Horizontal rule / separator
        if stripped in ("---", "***", "___"):
            html_parts.append("<hr>")
            continue

        # Unordered list
        if stripped.startswith(("- ", "* ")):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_format(stripped[2:])}</li>")
            continue

        # Ordered list
        ol_match = re.match(r'^(\d+)\.\s+(.*)', stripped)
        if ol_match:
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_format(ol_match.group(2))}</li>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            html_parts.append(f"<blockquote><p>{_inline_format(stripped[2:])}</p></blockquote>")
            continue

        # Regular paragraph
        html_parts.append(f"<p>{_inline_format(stripped)}</p>")

    if in_list:
        html_parts.append("</ul>")
    if in_code_block:
        html_parts.append("</code></pre>")

    return "\n".join(html_parts)


def _inline_format(text: str) -> str:
    """Handle bold, italic, code, and links."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    # Links
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    return text


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _extract_title(content: str) -> str:
    """Extract the first heading as the title."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
        if stripped.startswith("## "):
            return stripped.lstrip("# ").strip()
    # Fallback: first non-empty line
    for line in content.split("\n"):
        if line.strip():
            return line.strip()[:100]
    return "Untitled"


def _extract_description(content: str) -> str:
    """Extract a meta description from the first paragraph."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""
