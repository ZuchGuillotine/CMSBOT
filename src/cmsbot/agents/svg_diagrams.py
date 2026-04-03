"""SVG diagram generation and validation for blog section visuals."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from cmsbot.models import ImageSpec, LLMResponse
from cmsbot.providers.base import LLMProvider

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SAFE_TAGS = {
    "svg",
    "g",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "defs",
    "marker",
    "linearGradient",
    "radialGradient",
    "stop",
}
SAFE_ATTRS = {
    "aria-label",
    "class",
    "cx",
    "cy",
    "d",
    "dominant-baseline",
    "fill",
    "fill-opacity",
    "font-family",
    "font-size",
    "font-weight",
    "height",
    "id",
    "marker-end",
    "marker-height",
    "marker-mid",
    "marker-start",
    "marker-width",
    "markerHeight",
    "markerWidth",
    "offset",
    "opacity",
    "orient",
    "points",
    "preserveAspectRatio",
    "r",
    "refX",
    "refY",
    "role",
    "rx",
    "ry",
    "stop-color",
    "stop-opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-opacity",
    "stroke-width",
    "text-anchor",
    "transform",
    "viewBox",
    "width",
    "x",
    "x1",
    "x2",
    "y",
    "y1",
    "y2",
}
REJECTED_TAGS = {"script", "foreignObject", "image", "use", "style"}
REJECTED_VALUE_SNIPPETS = (
    "http://",
    "https://",
    "javascript:",
    "url(",
    "@import",
    "data:image",
)

SVG_SYSTEM = """\
You generate compact standalone SVG diagrams for editorial blog posts.

Return ONLY raw SVG markup. Do not use markdown fences. Do not explain anything.

The SVG must be brand-safe, clean, and minimal. Favor simple shapes, arrows, containers,
and short labels over dense text.

Hard constraints:
- Standalone <svg> root only
- No scripts, styles, foreignObject, image tags, use tags, masks, filters, or external assets
- No href attributes or remote references
- Max 8 labeled elements
- Max 4 words per label
- Max 24 visible words total
- Use only safe SVG primitives such as rect, line, path, circle, text, and marker
- Keep the diagram legible on a white blog background
- Use restrained colors and a professional editorial feel
"""

SVG_REPAIR_SYSTEM = """\
You repair invalid SVG diagrams.

Return ONLY corrected raw SVG markup with a standalone <svg> root.
Preserve the intended meaning while making the SVG safe and valid.
Do not add scripts, styles, foreignObject, image tags, use tags, or external references.
Respect these limits exactly:
- Max 8 labeled elements
- Max 4 words per label
- Max 24 visible words total
"""


@dataclass
class SvgSectionContext:
    """Relevant markdown section content for a diagram."""
    heading: str
    body: str


class SvgValidationError(ValueError):
    """Raised when generated SVG fails safety or structure checks."""


class SvgDiagramGenerator:
    """Generate safe SVG diagrams using a primary provider and one repair fallback."""

    def __init__(
        self,
        primary_provider: LLMProvider | None,
        fallback_provider: LLMProvider | None = None,
    ):
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider

    async def generate(
        self,
        spec: ImageSpec,
        section_context: SvgSectionContext,
    ) -> tuple[str, list[LLMResponse]]:
        """Generate and validate a section SVG, allowing one repair/fallback call."""
        providers = [p for p in (self.primary_provider, self.fallback_provider) if p is not None]
        if not providers:
            raise RuntimeError("No SVG-capable providers configured")

        prompt = _build_generation_prompt(spec, section_context)
        responses: list[LLMResponse] = []

        primary = providers[0]
        try:
            response = await primary.generate_simple(
                user_prompt=prompt,
                system_prompt=SVG_SYSTEM,
                max_tokens=2200,
                temperature=0.2,
            )
            responses.append(response)
            return sanitize_svg(response.content), responses
        except Exception as exc:
            primary_error = str(exc)
            primary_raw = responses[0].content if responses else ""
            repair_provider = providers[1] if len(providers) > 1 else primary
            repair_prompt = _build_repair_prompt(spec, section_context, primary_raw, primary_error)
            repair_response = await repair_provider.generate_simple(
                user_prompt=repair_prompt,
                system_prompt=SVG_REPAIR_SYSTEM,
                max_tokens=2200,
                temperature=0.1,
            )
            responses.append(repair_response)
            return sanitize_svg(repair_response.content), responses


def sanitize_svg(raw_svg: str) -> str:
    """Validate and return a sanitized SVG string."""
    svg_markup = extract_svg(raw_svg)
    try:
        root = ET.fromstring(svg_markup)
    except ET.ParseError as exc:
        raise SvgValidationError(f"Invalid XML: {exc}") from exc

    if _local_name(root.tag) != "svg":
        raise SvgValidationError("Root element must be <svg>")

    ET.register_namespace("", SVG_NAMESPACE)

    label_texts: list[str] = []
    for elem in root.iter():
        tag = _local_name(elem.tag)
        if tag in REJECTED_TAGS:
            raise SvgValidationError(f"Disallowed SVG tag: <{tag}>")
        if tag not in SAFE_TAGS:
            raise SvgValidationError(f"Unsupported SVG tag: <{tag}>")

        for attr_name, attr_value in list(elem.attrib.items()):
            local_attr = _local_name(attr_name)
            value = attr_value.strip()
            lowered = value.lower()

            if local_attr in {"href", "xlink:href"}:
                raise SvgValidationError("External references are not allowed")
            if local_attr not in SAFE_ATTRS:
                raise SvgValidationError(f"Unsupported SVG attribute: {local_attr}")
            if _uses_external_resource(value):
                raise SvgValidationError(f"Unsafe SVG attribute value in {local_attr}")

        if tag == "text":
            label = _normalize_text(" ".join(elem.itertext()))
            if label:
                label_texts.append(label)

    _validate_text_limits(label_texts)

    if not root.tag.startswith("{"):
        root.set("xmlns", SVG_NAMESPACE)

    sanitized = ET.tostring(root, encoding="unicode")
    if not sanitized.lstrip().startswith("<svg"):
        raise SvgValidationError("Sanitized output is not a standalone SVG")
    return sanitized


def extract_svg(text: str) -> str:
    """Extract raw SVG markup from an LLM response."""
    stripped = text.strip()
    code_fence_match = re.search(r"```(?:svg)?\s*(<svg.*?</svg>)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if code_fence_match:
        return code_fence_match.group(1).strip()

    svg_match = re.search(r"(<svg\b.*?</svg>)", stripped, re.DOTALL | re.IGNORECASE)
    if svg_match:
        return svg_match.group(1).strip()

    raise SvgValidationError("Response did not contain SVG markup")


def _build_generation_prompt(spec: ImageSpec, section_context: SvgSectionContext) -> str:
    return f"""Create a standalone SVG diagram for this blog section.

Section heading:
{section_context.heading or "Untitled section"}

Section body:
{section_context.body or spec.alt_text}

Desired visual:
{spec.description}

Alt text:
{spec.alt_text}

Output requirements:
- 1200x720 viewBox
- Clean editorial diagram, not a UI mockup
- Short labels only
- No screenshots, no icons that require text, no decorative clutter
- Return SVG markup only
"""


def _build_repair_prompt(
    spec: ImageSpec,
    section_context: SvgSectionContext,
    prior_svg: str,
    error: str,
) -> str:
    previous_svg = prior_svg.strip() or "<svg><!-- no valid SVG to repair --></svg>"
    return f"""Repair this SVG so it becomes safe, valid, and policy-compliant.

Validation failure:
{error}

Section heading:
{section_context.heading or "Untitled section"}

Section body:
{section_context.body or spec.alt_text}

Desired visual:
{spec.description}

Previous SVG:
{previous_svg}
"""


def _validate_text_limits(label_texts: list[str]) -> None:
    if len(label_texts) > 8:
        raise SvgValidationError("SVG contains too many labeled elements")

    total_words = 0
    for label in label_texts:
        word_count = len(label.split())
        if word_count > 4:
            raise SvgValidationError(f"SVG label is too long: '{label}'")
        total_words += word_count

    if total_words > 24:
        raise SvgValidationError("SVG contains too many visible words")


def _local_name(name: str) -> str:
    return name.split("}", 1)[-1] if "}" in name else name


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _uses_external_resource(value: str) -> bool:
    lowered = value.lower()
    if lowered.startswith("url(#") and lowered.endswith(")"):
        return False
    return any(snippet in lowered for snippet in REJECTED_VALUE_SNIPPETS)
