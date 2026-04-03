"""Image judge — single vision-capable agent that picks the most natural-looking image.

Unlike the text JudgePanel (3 judges, Borda count), the image judge is a single
agent with a detailed prompt focused specifically on detecting AI-generated artifacts.
It receives 3 candidate images and returns the index of the winner.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from cmsbot.models import ImageCandidate, ImageCandidateAssessment, LLMResponse
from cmsbot.providers.base import LLMProvider


@dataclass
class ImageJudgment:
    """Result of an image quality judgment."""
    winner_index: int | None  # 0-indexed into the candidates list
    reasoning: str
    scores: list[int]  # 1-10 naturalness score per candidate
    assessments: list[ImageCandidateAssessment]
    provider: str
    model: str


SYSTEM = """\
You are an expert photo editor and AI-image forensics analyst. Your job is to review
candidate images and select the one that looks MOST like a real photograph taken by
a human with a camera, and LEAST like an AI-generated image.

You will receive 3 candidate images labeled A, B, and C. Evaluate each on these criteria:

## AI-GENERATION RED FLAGS (things that make an image look fake)

**Skin & People:**
- Poreless, plasticky, or overly smoothed skin (the #1 AI giveaway)
- Uncanny symmetry in faces — real faces are slightly asymmetric
- Wrong number of fingers, malformed hands, merged fingers
- Hair that looks painted rather than strand-like, or hair that merges with background
- Teeth that are too uniform, too white, or oddly shaped
- Eyes that are different sizes, have weird catchlights, or look glassy
- Earrings, jewelry, or accessories that are inconsistent between sides

**Textures & Surfaces:**
- Surfaces that are too smooth or lack natural texture (fabric, wood, stone)
- Text or writing that is distorted, nonsensical, or has wrong characters
- Repeated patterns that are too regular (floor tiles, brick, fabric weave)
- Objects that melt into each other at the edges or have no clear boundary
- Backgrounds that dissolve into meaningless blobs or repetitive patterns

**Lighting & Color:**
- Lighting that is perfectly even with no natural falloff
- Shadows that go in inconsistent directions
- Highlights that are too clean and uniform
- Colors that are oversaturated or have an unnatural "HDR" look
- Lack of chromatic aberration, lens flare, or other optical imperfections

**Composition & Physics:**
- Objects floating or not properly resting on surfaces
- Perspective inconsistencies (vanishing points don't converge)
- Depth of field that is unnaturally smooth or inconsistent
- Scale inconsistencies between objects in the same scene

**Overall "Feel":**
- Too perfect, too clean, too "stock photo" — real photos have character
- Lack of any sensor noise or film grain
- Everything in sharp focus (real cameras have depth of field)
- The image feels "assembled" rather than "captured" — like a collage of concepts

## WHAT REAL PHOTOS LOOK LIKE

- Slight imperfections: a bit of noise, minor motion blur, dust spots
- Natural depth of field: foreground or background softly out of focus
- Authentic lighting: window light, overhead fixtures, mixed color temperatures
- Texture detail: visible fabric weave, skin pores, wood grain, surface imperfections
- Slightly imperfect framing: not perfectly centered, natural crop
- Color: natural white balance with slight warm or cool cast, not perfectly neutral

## YOUR TASK

For each candidate:
1. List specific AI-generation red flags you observe (or note their absence)
2. Give a naturalness score from 1-10 (10 = indistinguishable from a real photo)
3. Mark `disqualified=true` if the image has distorted, nonsensical, or unnecessary visible text,
   labels, signage, UI chrome, dashboards, screenshots, or other wording-heavy artifacts that would
   make it unusable in a brand-safe blog post. Also disqualify catastrophic anatomy or physics failures.

Then pick the WINNER — the highest-scoring candidate that is not disqualified.
If every candidate is disqualified, set `"winner": null`.

## OUTPUT FORMAT

Output ONLY valid JSON in this exact format:
{
  "analysis": {
    "A": {
      "red_flags": ["flag1", "flag2"],
      "strengths": ["strength1"],
      "score": 7,
      "disqualified": false,
      "disqualification_reason": ""
    },
    "B": {
      "red_flags": ["flag1"],
      "strengths": ["strength1", "strength2"],
      "score": 8,
      "disqualified": false,
      "disqualification_reason": ""
    }
  },
  "winner": "B",
  "reasoning": "One sentence explaining your choice"
}"""


class ImageJudge:
    """Single vision-capable agent that evaluates image candidates for naturalness."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def evaluate(
        self,
        candidates: list[ImageCandidate],
    ) -> tuple[ImageJudgment, LLMResponse]:
        """Evaluate candidate images and pick the most natural-looking one.

        Args:
            candidates: 2-3 ImageCandidate objects with raw image bytes.

        Returns:
            (judgment, llm_response) tuple.
        """
        labels = ["A", "B", "C"][:len(candidates)]

        prompt = (
            f"Evaluate these {len(candidates)} candidate images for a blog post. "
            "Pick the one that looks most like a real photograph and least like an "
            "AI-generated image. Distorted or unnecessary visible text is a fatal issue. "
            "Output ONLY the JSON format specified in your instructions."
        )
        messages = _build_messages_for_provider(self.provider.name, candidates, labels, prompt)
        response = await self.provider.generate(
            messages=messages,
            system_prompt=SYSTEM,
            max_tokens=1500,
            temperature=0.2,
        )

        judgment = _parse_judgment(response.content, labels, response.provider, response.model)
        return judgment, response


def _build_messages_for_provider(
    provider_name: str,
    candidates: list[ImageCandidate],
    labels: list[str],
    prompt: str,
) -> list[dict]:
    """Build provider-specific multimodal messages for image evaluation."""
    if provider_name == "anthropic":
        content_parts: list[dict] = [{"type": "text", "text": prompt}]
        for i, candidate in enumerate(candidates):
            b64 = base64.b64encode(candidate.image_bytes).decode()
            mime = "image/jpeg"
            if candidate.image_bytes[:4] == b"\x89PNG":
                mime = "image/png"
            content_parts.append(
                {
                    "type": "text",
                    "text": f"Candidate {labels[i]} (from {candidate.provider})",
                }
            )
            content_parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": b64,
                    },
                }
            )
        return [{"role": "user", "content": content_parts}]

    content_parts: list[dict] = [{"type": "text", "text": prompt}]
    for i, candidate in enumerate(candidates):
        b64 = base64.b64encode(candidate.image_bytes).decode()
        mime = "image/jpeg"
        if candidate.image_bytes[:4] == b"\x89PNG":
            mime = "image/png"
        content_parts.append(
            {
                "type": "text",
                "text": f"\n--- Candidate {labels[i]} (from {candidate.provider}) ---",
            }
        )
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return [{"role": "user", "content": content_parts}]


def _parse_judgment(
    text: str,
    labels: list[str],
    provider: str,
    model: str,
) -> ImageJudgment:
    """Parse the judge's JSON output into an ImageJudgment."""
    text = text.strip()

    # Try to extract JSON from the response
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            winner_value = data.get("winner")
            reasoning = data.get("reasoning", "")

            # Extract scores
            analysis = data.get("analysis", {})
            scores = []
            assessments: list[ImageCandidateAssessment] = []
            for label in labels:
                entry = analysis.get(label, {})
                score = int(entry.get("score", 5))
                scores.append(score)
                assessments.append(
                    ImageCandidateAssessment(
                        red_flags=list(entry.get("red_flags", [])),
                        strengths=list(entry.get("strengths", [])),
                        score=score,
                        disqualified=bool(entry.get("disqualified", False)),
                        disqualification_reason=str(entry.get("disqualification_reason", "")),
                    )
                )

            winner_index = _resolve_winner_index(winner_value, labels, assessments, scores)

            return ImageJudgment(
                winner_index=winner_index,
                reasoning=reasoning,
                scores=scores,
                assessments=assessments,
                provider=provider,
                model=model,
            )
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: look for a winner letter
    for i, label in enumerate(labels):
        if f"winner\": \"{label}" in text or f"Winner: {label}" in text:
            return ImageJudgment(
                winner_index=i,
                reasoning="Parsed from partial output",
                scores=[5] * len(labels),
                assessments=[
                    ImageCandidateAssessment(red_flags=[], strengths=[], score=5)
                    for _ in labels
                ],
                provider=provider,
                model=model,
            )

    # Last resort: pick first candidate
    return ImageJudgment(
        winner_index=0,
        reasoning="Could not parse judge output — defaulting to first candidate",
        scores=[5] * len(labels),
        assessments=[
            ImageCandidateAssessment(red_flags=[], strengths=[], score=5)
            for _ in labels
        ],
        provider=provider,
        model=model,
    )


def _resolve_winner_index(
    winner_value: str | None,
    labels: list[str],
    assessments: list[ImageCandidateAssessment],
    scores: list[int],
) -> int | None:
    """Resolve the winning index while respecting disqualifications."""
    eligible_indices = [i for i, assessment in enumerate(assessments) if not assessment.disqualified]
    if not eligible_indices:
        return None

    if isinstance(winner_value, str):
        winner_label = winner_value.upper()
        if winner_label in labels:
            winner_index = labels.index(winner_label)
            if not assessments[winner_index].disqualified:
                return winner_index

    return max(eligible_indices, key=lambda idx: scores[idx])
