from __future__ import annotations

import json
import sys
import types

import pytest

from cmsbot.agents.image_judge import ImageJudgment, _parse_judgment
from cmsbot.agents.images import (
    ImageDirector,
    _apply_text_risk_guard,
    _enhance_prompt_for_realism,
    _parse_image_specs,
)
from cmsbot.agents.svg_diagrams import (
    SvgDiagramGenerator,
    SvgSectionContext,
    SvgValidationError,
    sanitize_svg,
)
from cmsbot.budget import BudgetTracker
from cmsbot.models import ContentType, ImageCandidateAssessment, ImageSpec, LLMResponse
from cmsbot.providers.base import LLMProvider
from cmsbot.providers.image_providers import ImageProvider, ImageResult


class StubLLMProvider(LLMProvider):
    def __init__(
        self,
        responses: list[str | Exception],
        *,
        name: str = "stub",
        model: str = "stub-model",
    ):
        self.responses = list(responses)
        self.name = name
        self.model = model
        self.calls: list[dict] = []

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("No stub LLM responses left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(
            content=response,
            input_tokens=10,
            output_tokens=20,
            model=self.model,
            provider=self.name,
            cost=0.0,
        )


class StubImageProvider(ImageProvider):
    def __init__(
        self,
        name: str,
        *,
        image_bytes: bytes = b"image-bytes",
        error: Exception | None = None,
    ):
        self.name = name
        self.image_bytes = image_bytes
        self.error = error
        self.calls: list[dict] = []

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        self.calls.append({"prompt": prompt, "size": size})
        if self.error is not None:
            raise self.error
        return ImageResult(
            image_bytes=self.image_bytes,
            provider=self.name,
            model=f"{self.name}-model",
            cost=0.0,
        )


class StubJudge:
    def __init__(self, judgment: ImageJudgment):
        self.judgment = judgment
        self.calls = 0

    async def evaluate(self, candidates):
        self.calls += 1
        return self.judgment, LLMResponse(
            content="{}",
            input_tokens=5,
            output_tokens=5,
            model="judge-model",
            provider="judge",
            cost=0.0,
        )


@pytest.fixture
def fake_postprocess(monkeypatch):
    def _fake_postprocess(image_bytes: bytes, output_path, placement: str = "section"):
        output_path.write_bytes(image_bytes)
        return {"placement": placement}

    fake_module = types.ModuleType("cmsbot.postprocess")
    fake_module.postprocess_blog_image_bytes = _fake_postprocess
    monkeypatch.setitem(sys.modules, "cmsbot.postprocess", fake_module)


def _make_assessment(
    *,
    score: int = 7,
    disqualified: bool = False,
    reason: str = "",
) -> ImageCandidateAssessment:
    return ImageCandidateAssessment(
        red_flags=[],
        strengths=[],
        score=score,
        disqualified=disqualified,
        disqualification_reason=reason,
    )


def test_parse_image_specs_supports_all_methods():
    raw = json.dumps(
        [
            {
                "description": "Concrete office scene",
                "alt_text": "Office scene",
                "placement": "hero",
                "method": "fetch",
                "search_query": "office desk team",
            },
            {
                "description": "Custom editorial photo",
                "alt_text": "Editorial photo",
                "placement": "section-1",
                "method": "generate",
            },
            {
                "description": "Lead routing workflow diagram",
                "alt_text": "Lead routing workflow",
                "placement": "section-2",
                "method": "diagram",
            },
        ]
    )

    specs = _parse_image_specs(raw)

    assert [spec.method for spec in specs] == ["fetch", "generate", "diagram"]


def test_text_risk_guard_routes_sections_to_diagram_but_keeps_hero_photographic():
    specs = [
        ImageSpec(
            description="Workflow diagram explaining lead routing steps",
            alt_text="Workflow diagram",
            placement="hero",
            method="generate",
        ),
        ImageSpec(
            description="Workflow diagram explaining lead routing steps",
            alt_text="Workflow diagram",
            placement="section-1",
            method="generate",
        ),
    ]

    guarded = _apply_text_risk_guard(specs, content_type=ContentType.BLOG)

    assert guarded[0].method == "generate"
    assert guarded[1].method == "diagram"


def test_enhance_prompt_for_realism_rotates_anti_ai_variants():
    hero_prompt = _enhance_prompt_for_realism("Editorial founder portrait", "hero", 0)
    section_prompt = _enhance_prompt_for_realism("Office collaboration scene", "section-1", 1)

    assert "720p editorial web photo" in hero_prompt
    assert "visible skin texture and pores" in section_prompt
    assert hero_prompt != section_prompt


def test_parse_judgment_returns_none_when_all_candidates_are_disqualified():
    text = json.dumps(
        {
            "analysis": {
                "A": {
                    "red_flags": ["garbled text"],
                    "strengths": [],
                    "score": 3,
                    "disqualified": True,
                    "disqualification_reason": "garbled text",
                },
                "B": {
                    "red_flags": ["dashboard UI"],
                    "strengths": [],
                    "score": 4,
                    "disqualified": True,
                    "disqualification_reason": "dashboard UI",
                },
            },
            "winner": None,
            "reasoning": "Both images contain unusable text artifacts.",
        }
    )

    judgment = _parse_judgment(text, ["A", "B"], "judge", "judge-model")

    assert judgment.winner_index is None
    assert [assessment.disqualified for assessment in judgment.assessments] == [True, True]


def test_sanitize_svg_accepts_clean_flowchart():
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 720">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0 0 L8 4 L0 8 Z" fill="#1f2937" />
        </marker>
      </defs>
      <rect x="120" y="80" width="240" height="90" rx="16" fill="#f8fafc" stroke="#1f2937" stroke-width="2" />
      <text x="240" y="135" text-anchor="middle" font-size="28" font-family="Arial" fill="#1f2937">Capture leads</text>
      <line x1="360" y1="125" x2="560" y2="125" stroke="#1f2937" stroke-width="3" marker-end="url(#arrow)" />
      <rect x="560" y="80" width="240" height="90" rx="16" fill="#eef2ff" stroke="#1f2937" stroke-width="2" />
      <text x="680" y="135" text-anchor="middle" font-size="28" font-family="Arial" fill="#1f2937">Route owner</text>
    </svg>
    """

    sanitized = sanitize_svg(svg)

    assert sanitized.startswith("<svg")
    assert "marker-end=\"url(#arrow)\"" in sanitized


@pytest.mark.parametrize(
    "svg",
    [
        "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><foreignObject /></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><rect fill='url(https://evil.test/a)' /></svg>",
    ],
)
def test_sanitize_svg_rejects_unsafe_content(svg):
    with pytest.raises(SvgValidationError):
        sanitize_svg(svg)


def test_sanitize_svg_rejects_excessive_text():
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 720">
      <text x="100" y="100">This label has far too many words</text>
    </svg>
    """

    with pytest.raises(SvgValidationError):
        sanitize_svg(svg)


@pytest.mark.asyncio
async def test_svg_generator_repairs_after_invalid_primary():
    generator = SvgDiagramGenerator(
        primary_provider=StubLLMProvider(
            ["<svg xmlns='http://www.w3.org/2000/svg'><script /></svg>"],
            name="openai",
        ),
        fallback_provider=StubLLMProvider(
            [
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 720'>"
                "<rect x='80' y='80' width='220' height='90' rx='16' fill='#f8fafc' stroke='#111827' stroke-width='2' />"
                "<text x='190' y='135' text-anchor='middle' font-size='28' font-family='Arial'>Route owner</text>"
                "</svg>"
            ],
            name="anthropic",
        ),
    )

    svg, responses = await generator.generate(
        ImageSpec(
            description="Simple ownership flow",
            alt_text="Ownership flow diagram",
            placement="section-1",
            method="diagram",
        ),
        SvgSectionContext(heading="Lead Routing", body="Assign leads to the correct owner."),
    )

    assert svg.startswith("<svg")
    assert len(responses) == 2


@pytest.mark.asyncio
async def test_fetch_method_uses_unsplash_only_and_skips_judge(fake_postprocess, tmp_path):
    director_provider = StubLLMProvider(
        [
            json.dumps(
                [
                    {
                        "description": "A real office collaboration scene",
                        "alt_text": "Team working together",
                        "placement": "section-1",
                        "method": "fetch",
                        "search_query": "office collaboration team",
                    }
                ]
            )
        ],
        name="director",
    )
    fetch_provider = StubImageProvider("unsplash", image_bytes=b"real-photo")
    raster_provider = StubImageProvider("grok", image_bytes=b"generated-photo")
    judge = StubJudge(
        ImageJudgment(
            winner_index=0,
            reasoning="Only used in generate mode",
            scores=[8],
            assessments=[_make_assessment()],
            provider="judge",
            model="judge-model",
        )
    )

    director = ImageDirector(
        director_provider=director_provider,
        raster_image_providers=[raster_provider],
        fetch_provider=fetch_provider,
        judge=judge,
        budget=BudgetTracker(cap=10.0),
    )

    images, _ = await director.generate_images(
        task_prompt="Write about lead routing",
        final_content="## Routing\nExplain routing.",
        content_type=ContentType.BLOG,
        output_dir=tmp_path,
    )

    assert [image.file_path for image in images] == ["images/section-1.jpg"]
    assert len(fetch_provider.calls) == 1
    assert len(raster_provider.calls) == 0
    assert judge.calls == 0
    assert (tmp_path / "images" / "section-1.jpg").read_bytes() == b"real-photo"


@pytest.mark.asyncio
async def test_generate_method_respects_judge_disqualification(fake_postprocess, tmp_path):
    director_provider = StubLLMProvider(
        [
            json.dumps(
                [
                    {
                        "description": "A polished editorial desk scene",
                        "alt_text": "Editorial desk scene",
                        "placement": "section-1",
                        "method": "generate",
                    }
                ]
            )
        ],
        name="director",
    )
    raster_providers = [
        StubImageProvider("grok", image_bytes=b"candidate-a"),
        StubImageProvider("nanobanana", image_bytes=b"candidate-b"),
    ]
    judge = StubJudge(
        ImageJudgment(
            winner_index=None,
            reasoning="Both candidates contain broken text artifacts.",
            scores=[3, 4],
            assessments=[
                _make_assessment(disqualified=True, reason="garbled text"),
                _make_assessment(disqualified=True, reason="garbled text"),
            ],
            provider="judge",
            model="judge-model",
        )
    )

    director = ImageDirector(
        director_provider=director_provider,
        raster_image_providers=raster_providers,
        fetch_provider=None,
        judge=judge,
        budget=BudgetTracker(cap=10.0),
    )

    images, _ = await director.generate_images(
        task_prompt="Write about lead routing",
        final_content="## Routing\nExplain routing.",
        content_type=ContentType.BLOG,
        output_dir=tmp_path,
    )

    assert images == []
    assert judge.calls == 1
    assert all(len(provider.calls) == 1 for provider in raster_providers)


@pytest.mark.asyncio
async def test_generate_slots_rotate_anti_ai_prompt_profiles(fake_postprocess, tmp_path):
    director_provider = StubLLMProvider(
        [
            json.dumps(
                [
                    {
                        "description": "A candid founder portrait in a studio office",
                        "alt_text": "Founder portrait",
                        "placement": "hero",
                        "method": "generate",
                    },
                    {
                        "description": "A team working around a laptop in a bright office",
                        "alt_text": "Team collaboration",
                        "placement": "section-1",
                        "method": "generate",
                    },
                ]
            )
        ],
        name="director",
    )
    raster_provider = StubImageProvider("grok", image_bytes=b"generated-photo")
    judge = StubJudge(
        ImageJudgment(
            winner_index=0,
            reasoning="Looks natural",
            scores=[8],
            assessments=[_make_assessment()],
            provider="judge",
            model="judge-model",
        )
    )

    director = ImageDirector(
        director_provider=director_provider,
        raster_image_providers=[raster_provider],
        fetch_provider=None,
        judge=judge,
        budget=BudgetTracker(cap=10.0),
    )

    images, _ = await director.generate_images(
        task_prompt="Write about lead routing",
        final_content="## Routing\nExplain routing.\n\n## Handoff\nExplain handoff.",
        content_type=ContentType.BLOG,
        output_dir=tmp_path,
    )

    assert [image.file_path for image in images] == ["images/hero.jpg", "images/section-1.jpg"]
    assert len(raster_provider.calls) == 2
    assert "720p editorial web photo" in raster_provider.calls[0]["prompt"]
    assert "visible skin texture and pores" in raster_provider.calls[1]["prompt"]
    assert raster_provider.calls[0]["prompt"] != raster_provider.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_diagram_method_writes_svg_and_skips_raster_path(tmp_path):
    director_provider = StubLLMProvider(
        [
            json.dumps(
                [
                    {
                        "description": "A workflow diagram for assigning leads",
                        "alt_text": "Lead routing workflow",
                        "placement": "section-1",
                        "method": "diagram",
                    }
                ]
            )
        ],
        name="director",
    )
    fetch_provider = StubImageProvider("unsplash", image_bytes=b"real-photo")
    raster_provider = StubImageProvider("grok", image_bytes=b"generated-photo")
    svg_provider = StubLLMProvider(
        [
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 720'>"
            "<rect x='80' y='80' width='220' height='90' rx='16' fill='#f8fafc' stroke='#111827' stroke-width='2' />"
            "<text x='190' y='135' text-anchor='middle' font-size='28' font-family='Arial'>Route owner</text>"
            "</svg>"
        ],
        name="openai",
    )
    judge = StubJudge(
        ImageJudgment(
            winner_index=0,
            reasoning="unused",
            scores=[8],
            assessments=[_make_assessment()],
            provider="judge",
            model="judge-model",
        )
    )

    director = ImageDirector(
        director_provider=director_provider,
        raster_image_providers=[raster_provider],
        fetch_provider=fetch_provider,
        judge=judge,
        budget=BudgetTracker(cap=10.0),
        svg_provider=svg_provider,
    )

    images, responses = await director.generate_images(
        task_prompt="Write about lead routing",
        final_content="## Routing\nAssign leads by territory.",
        content_type=ContentType.BLOG,
        output_dir=tmp_path,
    )

    assert [image.file_path for image in images] == ["images/section-1.svg"]
    assert len(fetch_provider.calls) == 0
    assert len(raster_provider.calls) == 0
    assert judge.calls == 0
    assert len(responses) == 2
    assert (tmp_path / "images" / "section-1.svg").read_text(encoding="utf-8").startswith("<svg")


def test_renderer_keeps_hero_meta_raster_and_section_svg():
    pytest.importorskip("jinja2")
    from cmsbot.renderer import Renderer

    renderer = Renderer()
    html = renderer.render(
        content="# Lead Routing\n\n## Workflow\nExplain the routing logic.",
        images=[
            ImageSpec(
                description="Hero photo",
                alt_text="Hero photo",
                placement="hero",
                method="generate",
                file_path="images/hero.jpg",
            ),
            ImageSpec(
                description="Workflow diagram",
                alt_text="Workflow diagram",
                placement="section-1",
                method="diagram",
                file_path="images/section-1.svg",
            ),
        ],
        content_type=ContentType.BLOG,
        task_prompt="Lead routing article",
    )

    assert 'property="og:image" content="images/hero.jpg"' in html
    assert 'src="images/hero.jpg"' in html
    assert 'src="images/section-1.svg"' in html
