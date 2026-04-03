"""Image Director — plans images, generates candidates from multiple providers, and judges them.

Flow per image slot:
    1. Director LLM decides what images the content needs (same as before)
    2. For each image slot, generate up to 3 candidates in parallel:
       - GrokImagine (xAI)
       - Nano Banana (Gemini)
       - Unsplash (real photos) OR DALL-E (fallback)
    3. ImageJudge picks the most natural-looking candidate
    4. Winner is post-processed and saved
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from cmsbot.agents.image_judge import ImageJudge
from cmsbot.budget import BudgetTracker
from cmsbot.models import ContentType, ImageCandidate, ImageSpec, LLMResponse
from cmsbot.providers.base import LLMProvider
from cmsbot.providers.image_providers import ImageProvider

SYSTEM = """\
You are an art director for digital content. Given finalized content, decide what images it needs.

For a blog post, typically:
- 1 hero image (wide, atmospheric, sets the tone)
- 1-3 section images (illustrate key points)

For social media:
- 1 hero image (square or 4:5 ratio, eye-catching, shareable)

For each image, provide:
- "description": A detailed visual description (what the viewer sees — composition, mood, colors, style)
- "alt_text": Accessible alt text for screen readers
- "placement": Where it goes — "hero", "section-1", "section-2", etc.
- "search_query": A concise Unsplash-style search query (2-5 words, concrete subjects)
- "gen_prompt": A detailed prompt for AI image generation, written as PHOTOGRAPHIC direction

RULES:
- Output ONLY a JSON array of image objects
- Keep gen_prompt visual — avoid text in images
- Hero images should be evocative, not literal
- Be tasteful — no cheesy stock photo vibes
- Write gen_prompt as editorial/documentary photography direction:
  mention camera type, lens, focal length, aperture, lighting setup, and depth of field
- Use photographic language: "shot on", "natural light", "shallow depth of field", "f/1.8"
- Avoid "illustration", "digital art", "concept art", "3D render" — always lean photographic
- Describe slight imperfections: slightly off-center framing, natural lighting variation
- Never describe text overlays or watermarks in the image
- search_query should be simple and concrete — "morning supplements kitchen", not long descriptions"""


# ---------------------------------------------------------------------------
# Camera-realistic prompt suffixes
# ---------------------------------------------------------------------------

BLOG_HERO_CAMERA_SUFFIX = (
    " Shot on a mirrorless camera (Sony A7 IV, 35mm f/1.8 lens). Editorial photography"
    " style. Natural window light, slightly warm color temperature. Slight lift in"
    " shadows, gentle highlight rolloff, warm midtones. Shallow depth of field with"
    " soft background blur. Clean composition, professional but not sterile. No stock"
    " photo poses, no perfect symmetry, no text, no watermarks, no logos. Slight"
    " natural imperfections. The image should feel like it belongs in a well-designed"
    " online health magazine."
)

BLOG_SECTION_CAMERA_SUFFIX = (
    " Shot on a mirrorless camera or high-end phone camera. Mix of editorial and"
    " product photography. Natural lighting, slightly warm tones. Lifted shadows,"
    " gentle highlights, warm midtones. Clean background, professional but"
    " approachable. No stock photo cliches, no text in image, no watermarks, no"
    " logos. Authentic feel with slight imperfections."
)


def _enhance_prompt_for_realism(description: str, placement: str) -> str:
    """Append camera-specific language to push generation toward photorealistic output."""
    suffix = BLOG_HERO_CAMERA_SUFFIX if placement == "hero" else BLOG_SECTION_CAMERA_SUFFIX
    return description + suffix


class ImageDirector:
    """Plans images, generates candidates from multiple providers, and picks the best."""

    def __init__(
        self,
        director_provider: LLMProvider,
        image_providers: list[ImageProvider],
        judge: ImageJudge,
        budget: BudgetTracker,
    ):
        self.director = director_provider
        self.image_providers = image_providers
        self.judge = judge
        self.budget = budget

    async def generate_images(
        self,
        task_prompt: str,
        final_content: str,
        content_type: ContentType,
        output_dir: Path,
        log_fn=None,
    ) -> tuple[list[ImageSpec], list[LLMResponse]]:
        """Plan and generate images with multi-candidate judging."""
        log = log_fn or (lambda msg: None)
        responses: list[LLMResponse] = []

        # Step 1: Have the director decide what images are needed
        user_prompt = f"""\
CONTENT TYPE: {content_type.value}

ORIGINAL TASK:
{task_prompt}

FINAL CONTENT:
{final_content}

What images does this content need? Output a JSON array."""

        director_response = await self.director.generate_simple(
            user_prompt=user_prompt,
            system_prompt=SYSTEM,
            max_tokens=2000,
            temperature=0.6,
        )
        responses.append(director_response)

        specs = _parse_image_specs(director_response.content)

        # Step 2: For each spec, generate candidates and judge
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        generated_specs: list[ImageSpec] = []
        for spec in specs:
            # Budget check — estimate worst-case cost for candidates + judge
            estimated_cost = len(self.image_providers) * 0.10 + 0.02  # images + judge call
            if not self.budget.can_afford(estimated_cost):
                log(f"  Budget low — skipping {spec.placement}")
                break

            filename = f"{spec.placement}.jpg"
            output_path = images_dir / filename
            spec.file_path = f"images/{filename}"

            size = "1792x1024" if spec.placement == "hero" else "1024x1024"
            gen_prompt = _enhance_prompt_for_realism(spec.description, spec.placement)

            try:
                winner_bytes = await self._generate_and_judge(
                    gen_prompt=gen_prompt,
                    search_query=spec.search_query or spec.alt_text,
                    size=size,
                    placement=spec.placement,
                    log=log,
                )

                # Post-process and save the winner
                from cmsbot.postprocess import postprocess_blog_image_bytes
                postprocess_blog_image_bytes(
                    winner_bytes, output_path, placement=spec.placement
                )

            except Exception as e:
                log(f"  [WARN] Image generation failed for {spec.placement}: {e}")
                spec.file_path = ""
                continue

            generated_specs.append(spec)

        return generated_specs, responses

    async def _generate_and_judge(
        self,
        gen_prompt: str,
        search_query: str,
        size: str,
        placement: str,
        log,
    ) -> bytes:
        """Generate candidates from all providers in parallel, then judge."""

        async def _try_provider(provider: ImageProvider) -> ImageCandidate | None:
            try:
                # Unsplash uses search_query, generators use gen_prompt
                prompt = search_query if provider.name == "unsplash" else gen_prompt
                result = await provider.generate(prompt=prompt, size=size)
                self.budget.image_cost += result.cost
                log(f"    {provider.name}: generated ({result.model})")
                return ImageCandidate(
                    image_bytes=result.image_bytes,
                    provider=result.provider,
                    model=result.model,
                    cost=result.cost,
                    placement=placement,
                )
            except Exception as e:
                log(f"    {provider.name}: failed ({e})")
                return None

        # Generate from all providers in parallel
        results = await asyncio.gather(
            *[_try_provider(p) for p in self.image_providers]
        )
        candidates = [r for r in results if r is not None]

        if not candidates:
            raise RuntimeError("All image providers failed")

        # If only one candidate succeeded, use it directly (no judge needed)
        if len(candidates) == 1:
            log(f"    Only 1 candidate — using {candidates[0].provider} directly")
            return candidates[0].image_bytes

        # Run the judge
        log(f"    Judging {len(candidates)} candidates...")
        judgment, judge_response = await self.judge.evaluate(candidates)
        self.budget.record(
            "image_judge",
            judge_response.provider,
            judge_response.model,
            judge_response.input_tokens,
            judge_response.output_tokens,
        )

        winner = candidates[judgment.winner_index]
        log(
            f"    Winner: {winner.provider} "
            f"(scores: {judgment.scores}, reason: {judgment.reasoning[:80]})"
        )

        return winner.image_bytes


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_image_specs(text: str) -> list[ImageSpec]:
    """Parse image specifications from LLM output."""
    text = text.strip()

    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                specs = []
                for item in data:
                    if isinstance(item, dict):
                        spec = ImageSpec(
                            description=item.get("gen_prompt", item.get("dalle_prompt", item.get("description", ""))),
                            alt_text=item.get("alt_text", ""),
                            placement=item.get("placement", f"section-{len(specs) + 1}"),
                            method=item.get("method", "generate"),
                            search_query=item.get("search_query", ""),
                        )
                        specs.append(spec)
                return specs
        except json.JSONDecodeError:
            pass

    # Fallback: generate a single hero image
    return [ImageSpec(
        description="An atmospheric, evocative image related to the content theme",
        alt_text="Article hero image",
        placement="hero",
        method="generate",
    )]
