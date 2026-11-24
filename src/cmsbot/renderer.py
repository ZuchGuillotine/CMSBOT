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
    in_table = False
    section_count = 0
    placed: set[int] = set()

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

        # Table rows (lines starting and ending with |)
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]

            # Separator row (|---|---|) — skip it, we already emitted <thead>
            if all(re.match(r'^:?-+:?$', c) for c in cells):
                continue

            if not in_table:
                # First row of a new table — treat as header
                in_table = True
                html_parts.append('<table>')
                html_parts.append('<thead><tr>')
                for cell in cells:
                    html_parts.append(f'<th>{_inline_format(cell)}</th>')
                html_parts.append('</tr></thead>')
                html_parts.append('<tbody>')
            else:
                # Body row
                html_parts.append('<tr>')
                for cell in cells:
                    html_parts.append(f'<td>{_inline_format(cell)}</td>')
                html_parts.append('</tr>')
            continue

        # Close table if we were in one and this line isn't a table row
        if in_table:
            html_parts.append('</tbody></table>')
            in_table = False

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
                html_parts.append(_figure_html(section_img))
                placed.add(id(section_img))
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
    if in_table:
        html_parts.append('</tbody></table>')
    if in_code_block:
        html_parts.append("</code></pre>")

    _place_leftover_section_images(html_parts, images, placed)
    return "\n".join(html_parts)


def _figure_html(img: ImageSpec) -> str:
    alt = _escape_html(img.alt_text)
    return (
        f'<figure class="section-image">'
        f'<img src="{img.file_path}" alt="{alt}" loading="lazy">'
        f'<figcaption>{alt}</figcaption>'
        f'</figure>'
    )


def _section_number(placement: str) -> int | None:
    match = re.fullmatch(r"section-(\d+)", placement or "")
    return int(match.group(1)) if match else None


def _place_leftover_section_images(html_parts: list[str], images: list[ImageSpec], placed: set[int]) -> None:
    """Spread section images that had no matching ## heading evenly between paragraphs.

    Drafts with fewer ## headings than planned section images (multipass essays often have none)
    would otherwise drop images that were already generated and paid for.
    """
    leftover = sorted(
        (img for img in images
         if img.file_path and id(img) not in placed and _section_number(img.placement) is not None),
        key=lambda img: _section_number(img.placement),
    )
    if not leftover:
        return
    # Skip lead-ins ending in ":" so an image never separates them from the list they introduce
    paragraphs = [i for i, part in enumerate(html_parts) if part.startswith("<p>") and not part.endswith(":</p>")]
    if not paragraphs:
        html_parts.extend(_figure_html(img) for img in leftover)
        return
    n, count = len(leftover), len(paragraphs)
    # After paragraph k*count/(n+1) for k = 1..n, so images sit between the intro and the close
    slots = [paragraphs[max(0, min(count - 1, round(k * count / (n + 1)) - 1))] for k in range(1, n + 1)]
    # slots never decrease, so inserting back to front keeps earlier indices and image order intact
    for slot, img in reversed(list(zip(slots, leftover))):
        html_parts.insert(slot + 1, _figure_html(img))


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
