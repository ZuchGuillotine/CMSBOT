"""Data models for the Autoreason pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ContentType(str, Enum):
    BLOG = "blog"
    SOCIAL = "social"


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    cost: float  # USD


@dataclass
class Draft:
    """A content draft at any stage of the pipeline."""
    content: str
    round_num: int
    origin: str  # "author", "rewriter", "synthesizer", or "keep"
    provider: str = ""
    model: str = ""


@dataclass
class Critique:
    """Strawman critique of a draft."""
    problems: str
    round_num: int
    provider: str = ""
    model: str = ""


@dataclass
class Judgment:
    """A single judge's ranking of candidates."""
    ranking: list[int]  # Indices into the shuffled candidate list, best-first
    judge_id: int
    provider: str = ""
    model: str = ""


@dataclass
class ImageSpec:
    """Specification for a single image in the content."""
    description: str
    alt_text: str
    placement: str  # "hero", "section-1", "section-2", etc.
    method: str  # "generate" or "fetch"
    search_query: str = ""
    file_path: str = ""  # Filled after generation/fetch


@dataclass
class ImageCandidate:
    """A candidate image from one provider, awaiting judging."""
    image_bytes: bytes
    provider: str
    model: str
    cost: float
    placement: str


@dataclass
class RoundResult:
    """Result of one refinement round."""
    round_num: int
    draft_a: Draft
    critique: Critique
    candidate_b: Draft
    candidate_ab: Draft
    winner: Draft
    winner_label: str  # "A", "B", or "AB"
    streak: int
    round_cost: float


@dataclass
class PipelineResult:
    """Final output of the Autoreason pipeline."""
    task_prompt: str
    content_type: ContentType
    final_draft: Draft
    rounds: list[RoundResult]
    images: list[ImageSpec]
    html: str
    total_cost: float
    total_rounds: int
    converged: bool
