"""Image Director — decides what images are needed and generates/fetches them."""

from __future__ import annotations

import json
import re
from pathlib import Path

from cmsbot.budget import BudgetTracker
from cmsbot.models import ContentType, ImageSpec, LLMResponse
from cmsbot.providers.base import LLMProvider
from cmsbot.providers.openai_provider import OpenAIProvider

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
- "method": "generate" (AI-created art) or "fetch" (stock photo style)
- "dalle_prompt": If method is "generate", a detailed DALL-E prompt (photographic or illustrative style, NOT text-heavy)

RULES:
- Output ONLY a JSON array of image objects
- Keep DALL-E prompts visual — avoid text in images (DALL-E is bad at text)
- Hero images should be evocative, not literal
- Prefer "generate" for conceptual/abstract images, "fetch" for generic stock needs
- Be tasteful — no cheesy stock photo vibes"""


class ImageDirector:
    """Decides what images are needed and generates them."""

    def __init__(
        self,
        director_provider: LLMProvider,
        image_provider: OpenAIProvider,
        budget: BudgetTracker,
    ):
        self.director = director_provider
        self.image_provider = image_provider
        self.budget = budget

    async def generate_images(
        self,
        task_prompt: str,
        final_content: str,
        content_type: ContentType,
        output_dir: Path,
    ) -> tuple[list[ImageSpec], list[LLMResponse]]:
        """Plan and generate images for the final content."""
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

        # Step 2: Generate each image
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        generated_specs: list[ImageSpec] = []
        for i, spec in enumerate(specs):
            # Check budget before each image
            if not self.budget.can_afford(0.08):
                break

            filename = f"{spec.placement}.png"
            output_path = images_dir / filename
            spec.file_path = f"images/{filename}"

            if spec.method == "generate":
                dalle_prompt = spec.description
                # Use the dalle_prompt from spec if available
                try:
                    cost = await self.image_provider.generate_image(
                        prompt=dalle_prompt,
                        output_path=output_path,
                        size="1792x1024" if spec.placement == "hero" else "1024x1024",
                    )
                    self.budget.record_image(
                        "1792x1024" if spec.placement == "hero" else "1024x1024"
                    )
                except Exception:
                    # If image generation fails, skip this image
                    spec.file_path = ""
                    continue

            generated_specs.append(spec)

        return generated_specs, responses


def _parse_image_specs(text: str) -> list[ImageSpec]:
    """Parse image specifications from LLM output."""
    text = text.strip()

    # Extract JSON array from the text
    # Try to find JSON array
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                specs = []
                for item in data:
                    if isinstance(item, dict):
                        specs.append(ImageSpec(
                            description=item.get("dalle_prompt", item.get("description", "")),
                            alt_text=item.get("alt_text", ""),
                            placement=item.get("placement", f"section-{len(specs)}"),
                            method=item.get("method", "generate"),
                        ))
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
