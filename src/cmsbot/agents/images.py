"""Image Director — plans visuals, routes photo vs. diagram generation, and judges raster candidates."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from cmsbot.agents.image_judge import ImageJudge
from cmsbot.agents.svg_diagrams import SvgDiagramGenerator, SvgSectionContext
from cmsbot.budget import BudgetTracker
from cmsbot.models import ContentType, ImageCandidate, ImageSpec, LLMResponse
from cmsbot.providers.base import LLMProvider
from cmsbot.providers.image_providers import ImageProvider

SYSTEM = """\
You are an art director for digital content. Given finalized content, decide what visuals it needs.

For a blog post, typically:
- 1 hero image (wide, atmospheric, sets the tone, always photographic)
- 1-3 section visuals

For social media:
- 1 hero image (square or 4:5 ratio, eye-catching, shareable, always photographic)

For each visual, provide:
- "description": What the viewer should understand at a glance
- "alt_text": Accessible alt text for screen readers
- "placement": "hero", "section-1", "section-2", etc.
- "method": One of "fetch", "generate", or "diagram"
- "search_query": A concise photo-search query for real photography when method is "fetch"
- "gen_prompt": A detailed prompt for AI image generation when method is "generate"

Routing rules:
- "fetch" for concrete real-world scenes that can be satisfied by a photo search
- "generate" for concrete custom photography with no visible wording
- "diagram" for workflows, comparisons, architectures, timelines, frameworks, taxonomies, or abstract concepts that would need multiple words or labels

Hard rules:
- Hero must never use "diagram"
- Keep photographic prompts visual and brand-safe
- Photo prompts must forbid visible words, numbers, labels, dashboards, screenshots, whiteboards, posters, book covers, packaging fronts, documents, signage, charts, tables, and UI chrome
- search_query must stay simple, concrete, and photo-searchable
- Never describe text overlays or watermarks in an image
- Output ONLY a JSON array of visual objects
"""

BLOG_HERO_CAMERA_SUFFIX = (
    " Shot on a Sony A7 IV with a 35mm f/1.8 lens on Kodak Portra 400 film stock."
    " Editorial photography for a well-designed online magazine. Natural window light,"
    " slightly warm color temperature with gentle Rembrandt falloff. Lifted shadows,"
    " gentle highlight rolloff with slight desaturation in the highlights, warm midtones."
    " Shallow depth of field with soft bokeh. Candid, honest framing — not perfectly"
    " centered. Muted, natural color palette. The scene should feel captured, not assembled."
)

BLOG_SECTION_CAMERA_SUFFIX = (
    " Shot on a Canon EOS R5 or high-end phone camera, color palette reminiscent of"
    " Fujifilm Superia. Mix of editorial and product photography. Natural lighting with"
    " slight warm cast and mixed color temperatures. Lifted shadows, gentle highlights,"
    " warm midtones. Muted, natural tones — not oversaturated. Clean background,"
    " professional but approachable. Authentic feel with slight imperfections and"
    " natural texture detail."
)

PHOTO_NEGATIVE_SUFFIX = (
    " No stock photo poses, no stock photo cliches, no perfect symmetry, no text, no"
    " numbers, no labels, no charts, no dashboards, no screenshots, no whiteboards,"
    " no posters, no packaging fronts, no documents, no signage, no tables, no UI"
    " chrome, no watermarks, no logos. No plastic skin, no airbrushed look, no poreless"
    " faces, no waxy skin, no mannequin-like features, no oversaturation, no hyper-clean"
    " CGI sharpness, no 3D render aesthetic, no extra fingers or deformed hands."
)

ANTI_AI_REALISM_VARIANTS = (
    (
        " Subtle sensor grain like Kodak Portra 400 pushed one stop, faint chromatic"
        " aberration on a few high-contrast edges, and slightly imperfect clarity like a"
        " lightly upscaled 720p editorial web photo. Natural film halation around bright"
        " highlights. Keep it natural, not degraded."
    ),
    (
        " Preserve visible skin texture and pores when people are present — real pores,"
        " freckles, sun damage, laugh lines. A few stray hairs or flyaways, natural fabric"
        " wrinkles, and small facial asymmetries. Weathered, honest, authentic. Nothing"
        " airbrushed, glamorized, or overly polished."
    ),
    (
        " Add minor handheld realism: tiny focus falloff, slightly imperfect framing,"
        " candid timing caught mid-stride, and micro-variations in posture or object"
        " placement. Natural contact shadows, realistic reflections in surfaces. Avoid"
        " posed stock-photo stiffness — the scene should feel captured, not staged."
    ),
    (
        " Keep real-world surface imperfections where plausible: faint dust, tiny"
        " scuffs, mild reflection irregularities, lived-in surfaces, worn materials,"
        " and subtle edge softness rather than hyper-clean CGI sharpness. Muted, natural"
        " color palette — not oversaturated or HDR."
    ),
    (
        " Let the scene retain small capture flaws: gentle grain in the midtones like"
        " Fujifilm Superia, minimal lens softness, slight exposure inconsistency, and"
        " realistic material texture in skin, hair, wood, fabric, or metal. Slight"
        " highlight desaturation as values approach clipping, like real sensor behavior."
    ),
    (
        " Color palette reminiscent of Ilford HP5 cross-processed — muted tones, natural"
        " white balance with a slight warm cast, not perfectly neutral. Visible fabric"
        " weave, wood grain, surface imperfections. Everything should have unique"
        " environmental details with natural variation, never repeated patterns."
    ),
    (
        " Authentic mixed lighting: window light combined with overhead fixtures at"
        " slightly different color temperatures. Natural depth of field with foreground"
        " or background softly out of focus. Slight film halation around specular"
        " highlights. The image should feel grounded and real, not assembled from concepts."
    ),
)

TEXT_RISK_KEYWORDS = (
    "abstract",
    "architecture",
    "chart",
    "comparison",
    "dashboard",
    "diagram",
    "document",
    "flowchart",
    "framework",
    "graph",
    "interface",
    "labels",
    "poster",
    "screen",
    "screenshot",
    "signage",
    "steps",
    "table",
    "taxonomy",
    "text",
    "timeline",
    "ui",
    "workflow",
)

PHOTO_UNSAFE_KEYWORDS = (
    "book cover",
    "chart",
    "dashboard",
    "diagram",
    "document",
    "flowchart",
    "graph",
    "labels",
    "numbers",
    "packaging",
    "poster",
    "screen",
    "screenshot",
    "signage",
    "table",
    "text",
    "ui",
    "whiteboard",
    "words",
)


def _enhance_prompt_for_realism(description: str, placement: str, variant_index: int) -> str:
    """Append camera-specific language and rotating anti-AI realism cues."""
    suffix = BLOG_HERO_CAMERA_SUFFIX if placement == "hero" else BLOG_SECTION_CAMERA_SUFFIX
    realism_variant = ANTI_AI_REALISM_VARIANTS[variant_index % len(ANTI_AI_REALISM_VARIANTS)]
    return description + suffix + realism_variant + PHOTO_NEGATIVE_SUFFIX


class ImageDirector:
    """Plans visuals, routes generation, and picks the safest usable asset."""

    def __init__(
        self,
        director_provider: LLMProvider,
        raster_image_providers: list[ImageProvider],
        fetch_provider: ImageProvider | None,
        judge: ImageJudge,
        budget: BudgetTracker,
        svg_provider: LLMProvider | None = None,
        svg_fallback_provider: LLMProvider | None = None,
    ):
        self.director = director_provider
        self.raster_image_providers = raster_image_providers
        self.fetch_provider = fetch_provider
        self.judge = judge
        self.budget = budget
        self.svg_generator = SvgDiagramGenerator(
            primary_provider=svg_provider,
            fallback_provider=svg_fallback_provider,
        )

    async def generate_images(
        self,
        task_prompt: str,
        final_content: str,
        content_type: ContentType,
        output_dir: Path,
        log_fn=None,
    ) -> tuple[list[ImageSpec], list[LLMResponse]]:
        """Plan and generate visuals with method-aware routing."""
        log = log_fn or (lambda msg: None)
        responses: list[LLMResponse] = []

        user_prompt = f"""\
CONTENT TYPE: {content_type.value}

ORIGINAL TASK:
{task_prompt}

FINAL CONTENT:
{final_content}

What visuals does this content need? Output a JSON array."""

        director_response = await self.director.generate_simple(
            user_prompt=user_prompt,
            system_prompt=SYSTEM,
            max_tokens=2000,
            temperature=0.6,
        )
        responses.append(director_response)

        specs = _apply_text_risk_guard(
            _parse_image_specs(director_response.content),
            content_type=content_type,
        )

        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        generated_specs: list[ImageSpec] = []
        generated_slot_index = 0
        for spec in specs:
            if _should_skip_text_heavy_hero(spec):
                log(f"  [SKIP] {spec.placement}: hero visual would require text-heavy treatment")
                continue

            estimated_cost = self._estimate_slot_cost(spec)
            if not self.budget.can_afford(estimated_cost):
                log(f"  Budget low — skipping {spec.placement}")
                continue

            if spec.method == "diagram":
                try:
                    filename = f"{spec.placement}.svg"
                    output_path = images_dir / filename
                    section_context = _extract_section_context(final_content, spec.placement)
                    svg_markup, svg_responses = await self.svg_generator.generate(spec, section_context)
                    output_path.write_text(svg_markup, encoding="utf-8")
                    spec.file_path = f"images/{filename}"
                    generated_specs.append(spec)
                    responses.extend(svg_responses)
                    log(f"  {spec.placement}: generated SVG diagram")
                except Exception as exc:
                    log(f"  [WARN] Diagram generation failed for {spec.placement}: {exc}")
                    spec.file_path = ""
                continue

            if await self._render_raster(spec, images_dir, log, generated_slot_index):
                generated_specs.append(spec)
                generated_slot_index += 1

        # A blog needs a hero for its thumbnail and social cards. When the planned one was
        # skipped (text-heavy concept) or never planned, fall back to a plain photograph.
        if content_type == ContentType.BLOG and not any(s.placement == "hero" for s in generated_specs):
            hero = _fallback_hero_spec(final_content)
            if self.budget.can_afford(self._estimate_slot_cost(hero)):
                log("  hero: none planned or usable; generating a fallback photograph")
                if await self._render_raster(hero, images_dir, log, generated_slot_index):
                    generated_specs.insert(0, hero)

        return generated_specs, responses

    async def _render_raster(self, spec: ImageSpec, images_dir: Path, log, variant_index: int) -> bool:
        """Generate, judge and post-process one raster slot; sets spec.file_path on success."""
        try:
            filename = f"{spec.placement}.jpg"
            image_bytes = await self._resolve_raster_bytes(spec, log, variant_index=variant_index)
            if image_bytes is None:
                log(f"  [SKIP] {spec.placement}: no acceptable raster candidate")
                return False

            from cmsbot.postprocess import postprocess_blog_image_bytes

            postprocess_blog_image_bytes(image_bytes, images_dir / filename, placement=spec.placement)
            spec.file_path = f"images/{filename}"
            return True
        except Exception as exc:
            log(f"  [WARN] Image generation failed for {spec.placement}: {exc}")
            spec.file_path = ""
            return False

    def _estimate_slot_cost(self, spec: ImageSpec) -> float:
        """Estimate the cost of generating a single visual."""
        if spec.method == "diagram":
            return 0.03
        if spec.method == "fetch" and self.fetch_provider is not None:
            return 0.01
        return max(0.02, len(self.raster_image_providers) * 0.10 + 0.02)

    async def _resolve_raster_bytes(
        self,
        spec: ImageSpec,
        log,
        variant_index: int,
    ) -> bytes | None:
        """Resolve a raster image via fetch or generation."""
        size = "1792x1024" if spec.placement == "hero" else "1024x1024"

        if spec.method == "fetch":
            image_bytes = await self._fetch_photo(spec, size=size, log=log)
            if image_bytes is not None:
                return image_bytes
            log(f"    fetch fallback: degrading {spec.placement} to generated photography")

        gen_prompt = _enhance_prompt_for_realism(
            spec.description or spec.alt_text,
            spec.placement,
            variant_index,
        )
        return await self._generate_and_judge(
            gen_prompt=gen_prompt,
            search_query=spec.search_query or spec.alt_text,
            size=size,
            placement=spec.placement,
            log=log,
        )

    async def _fetch_photo(
        self,
        spec: ImageSpec,
        size: str,
        log,
    ) -> bytes | None:
        """Fetch a real photograph without invoking the judge."""
        if self.fetch_provider is None:
            return None

        query = spec.search_query or _fallback_search_query(spec)
        if not query:
            return None

        try:
            result = await self.fetch_provider.generate(prompt=query, size=size)
            self.budget.image_cost += result.cost
            log(f"    {self.fetch_provider.name}: fetched ({result.model})")
            return result.image_bytes
        except Exception as exc:
            log(f"    {self.fetch_provider.name}: failed ({exc})")
            return None

    async def _generate_and_judge(
        self,
        gen_prompt: str,
        search_query: str,
        size: str,
        placement: str,
        log,
    ) -> bytes | None:
        """Generate raster candidates from configured providers, then judge them."""

        async def _try_provider(provider: ImageProvider) -> ImageCandidate | None:
            try:
                result = await provider.generate(prompt=gen_prompt, size=size)
                self.budget.image_cost += result.cost
                log(f"    {provider.name}: generated ({result.model})")
                return ImageCandidate(
                    image_bytes=result.image_bytes,
                    provider=result.provider,
                    model=result.model,
                    cost=result.cost,
                    placement=placement,
                )
            except Exception as exc:
                log(f"    {provider.name}: failed ({exc})")
                return None

        if not self.raster_image_providers:
            raise RuntimeError("No raster image providers configured")

        results = await asyncio.gather(*[_try_provider(provider) for provider in self.raster_image_providers])
        candidates = [candidate for candidate in results if candidate is not None]
        if not candidates:
            raise RuntimeError(f"All raster providers failed for '{search_query[:60]}'")

        log(f"    Judging {len(candidates)} raster candidate(s)...")
        judgment, judge_response = await self.judge.evaluate(candidates)
        self.budget.record(
            "image_judge",
            judge_response.provider,
            judge_response.model,
            judge_response.input_tokens,
            judge_response.output_tokens,
        )

        if judgment.winner_index is None:
            log(f"    No acceptable raster winner ({judgment.reasoning})")
            return None

        winner = candidates[judgment.winner_index]
        log(
            f"    Winner: {winner.provider} "
            f"(scores: {judgment.scores}, reason: {judgment.reasoning[:80]})"
        )
        return winner.image_bytes


def _parse_image_specs(text: str) -> list[ImageSpec]:
    """Parse image specifications from LLM output."""
    text = text.strip()

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                specs = []
                for item in data:
                    if isinstance(item, dict):
                        method = str(item.get("method", "generate")).strip().lower() or "generate"
                        if method not in {"fetch", "generate", "diagram"}:
                            method = "generate"
                        spec = ImageSpec(
                            description=item.get("description", item.get("gen_prompt", "")),
                            alt_text=item.get("alt_text", ""),
                            placement=item.get("placement", f"section-{len(specs) + 1}"),
                            method=method,
                            search_query=item.get("search_query", ""),
                        )
                        specs.append(spec)
                return specs
        except json.JSONDecodeError:
            pass

    return [
        ImageSpec(
            description="An atmospheric, evocative image related to the content theme",
            alt_text="Article hero image",
            placement="hero",
            method="generate",
        )
    ]


def _fallback_hero_spec(final_content: str) -> ImageSpec:
    """A photographic hero that doesn't depend on rendering words, built from the article title."""
    title_match = re.search(r"^#\s+(.+)$", final_content, re.MULTILINE)
    topic = title_match.group(1).strip() if title_match else "the article's subject"
    return ImageSpec(
        description=(
            f"Candid editorial photograph of an everyday scene that fits an article titled \"{topic}\": "
            "a real home or daily-routine setting with natural light. No visible text, labels, packaging, "
            "screens, charts or signage."
        ),
        alt_text=f"Photo for {topic}",
        placement="hero",
        method="generate",
        search_query=topic,
    )


def _apply_text_risk_guard(
    specs: list[ImageSpec],
    content_type: ContentType,
) -> list[ImageSpec]:
    """Force text-heavy blog sections into diagrams and keep hero visuals photographic."""
    guarded_specs: list[ImageSpec] = []
    for spec in specs:
        method = spec.method.strip().lower() or "generate"
        if method not in {"fetch", "generate", "diagram"}:
            method = "generate"

        if content_type != ContentType.BLOG and method == "diagram":
            method = "generate"

        if spec.placement == "hero" and method == "diagram":
            method = "generate"

        if _is_text_heavy_visual(spec):
            if spec.placement != "hero" and content_type == ContentType.BLOG:
                method = "diagram"
            elif method == "diagram":
                method = "generate"

        spec.method = method
        guarded_specs.append(spec)

    return guarded_specs


def _is_text_heavy_visual(spec: ImageSpec) -> bool:
    """Return True when the visual concept is better served as a diagram."""
    haystack = " ".join(
        [
            spec.description.lower(),
            spec.alt_text.lower(),
            spec.search_query.lower(),
        ]
    )
    return any(keyword in haystack for keyword in TEXT_RISK_KEYWORDS)


def _should_skip_text_heavy_hero(spec: ImageSpec) -> bool:
    """Skip hero visuals whose concept still relies on wording-heavy imagery."""
    if spec.placement != "hero":
        return False
    return _is_text_heavy_visual(spec) or _contains_photo_unsafe_terms(spec.description)


def _contains_photo_unsafe_terms(text: str) -> bool:
    haystack = text.lower()
    return any(keyword in haystack for keyword in PHOTO_UNSAFE_KEYWORDS)


def _fallback_search_query(spec: ImageSpec) -> str:
    """Derive a simple concrete photo-search query from alt text or description."""
    source = spec.search_query or spec.alt_text or spec.description
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", source).lower()
    words = [word for word in cleaned.split() if len(word) > 2]
    return " ".join(words[:5])


def _extract_section_context(final_content: str, placement: str) -> SvgSectionContext:
    """Extract the relevant markdown section for a diagram visual."""
    match = re.match(r"section-(\d+)", placement)
    sections = _split_markdown_sections(final_content)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(sections):
            return sections[index]

    if sections:
        return sections[0]

    excerpt = "\n".join(line.strip() for line in final_content.splitlines() if line.strip())[:800]
    return SvgSectionContext(heading="Article summary", body=excerpt)


def _split_markdown_sections(final_content: str) -> list[SvgSectionContext]:
    """Split markdown-ish blog content into top-level sections matching renderer image placement."""
    sections: list[SvgSectionContext] = []
    heading: str | None = None
    body_lines: list[str] = []

    for raw_line in final_content.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            if heading is not None:
                sections.append(
                    SvgSectionContext(
                        heading=heading,
                        body="\n".join(body_lines).strip(),
                    )
                )
            heading = line[3:].strip()
            body_lines = []
            continue

        if heading is not None:
            body_lines.append(raw_line)

    if heading is not None:
        sections.append(
            SvgSectionContext(
                heading=heading,
                body="\n".join(body_lines).strip(),
            )
        )

    return sections
