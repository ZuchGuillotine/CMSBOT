from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from cmsbot.agents.judge import _parse_ranking
from cmsbot.config import Config, ProviderConfig, RoleAssignment
from cmsbot.de_ai import heuristic_review, score_thresholds
from cmsbot.gates import evaluate_asset_gates, record_human_approval
from cmsbot.providers.anthropic import AnthropicProvider, RefusalError


def _cfg(**providers: str) -> Config:
    return Config(**{name: ProviderConfig(api_key="k", model=model) for name, model in providers.items()})


# ---- role resolution -------------------------------------------------------


def test_roles_fall_back_to_any_configured_provider():
    config = _cfg(anthropic="claude-sonnet-5-5")
    for role in ("author", "strawman", "rewriter", "synthesizer", "planner", "drafter", "auditor"):
        assert config.provider_for(role) == "anthropic"
    assert config.judge_providers() == ["anthropic", "anthropic", "anthropic"]


def test_roles_keep_preferred_provider_when_configured():
    config = _cfg(anthropic="s", anthropic_opus="o", openai="g", gemini="m")
    assert config.provider_for("planner") == "anthropic_opus"
    assert config.provider_for("drafter") == "anthropic_opus"
    assert config.provider_for("auditor") == "anthropic"
    assert config.judge_providers() == ["anthropic_opus", "openai", "gemini"]


def test_roles_raise_without_any_provider():
    with pytest.raises(ValueError, match="No LLM provider"):
        Config().provider_for("author")


def test_role_env_overrides(monkeypatch):
    monkeypatch.setenv("CMSBOT_ROLE_AUTHOR", "anthropic_opus")
    monkeypatch.setenv("CMSBOT_ROLE_JUDGES", "openai, gemini")
    roles = RoleAssignment.from_env()
    assert roles.author == "anthropic_opus"
    assert roles.judges == ["openai", "gemini"]


# ---- Claude provider -------------------------------------------------------


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _response(stop_reason="end_turn", blocks=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="bio") if stop_reason == "refusal" else None,
        content=blocks if blocks is not None else [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Hello "),
            SimpleNamespace(type="text", text="world"),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5-5",
    )


def _provider(model: str, response) -> tuple[AnthropicProvider, _FakeMessages, _FakeMessages]:
    provider = AnthropicProvider(api_key="k", model=model)
    beta, legacy = _FakeMessages(response), _FakeMessages(response)
    provider._client = SimpleNamespace(messages=legacy, beta=SimpleNamespace(messages=beta))
    return provider, beta, legacy


def test_claude_5_requests_skip_temperature_and_opt_into_fallbacks():
    provider, beta, legacy = _provider("claude-sonnet-5-5", _response())
    result = asyncio.run(provider.generate_simple("hi", max_tokens=200, temperature=0.3))

    assert result.content == "Hello world"  # thinking blocks are skipped
    assert not legacy.calls
    call = beta.calls[0]
    assert "temperature" not in call
    assert call["max_tokens"] >= 16000
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["extra_body"] == {"fallbacks": "default"}


def test_older_claude_models_keep_temperature():
    provider, beta, legacy = _provider("claude-sonnet-4-6", _response())
    asyncio.run(provider.generate_simple("hi", temperature=0.3))
    assert not beta.calls
    assert legacy.calls[0]["temperature"] == 0.3


def test_refusals_raise_instead_of_returning_empty_text():
    provider, _, _ = _provider("claude-opus-5-5", _response(stop_reason="refusal", blocks=[]))
    with pytest.raises(RefusalError, match="bio"):
        asyncio.run(provider.generate_simple("hi"))


# ---- judge parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[2, 1, 3]", [2, 1, 3]),
        ("Ranking: [3,1,2] because...", [3, 1, 2]),
        ("[1, 1, 2]", None),
        ("I prefer candidate 2", None),
        ("", None),
    ],
)
def test_parse_ranking_requires_a_full_permutation(text, expected):
    assert _parse_ranking(text) == expected


# ---- de-AI heuristics ------------------------------------------------------


def _review(text: str) -> dict:
    return heuristic_review(text, asset_set_id="a", platform="blog", content_format="blog", source_path="x.md")


def test_contrast_frames_are_flagged_when_frequent():
    text = (
        "A disrupted rhythm is the problem, not the hormone. "
        "Timing is the lever, not the dose. "
        "It's not just sleep, it's when you sleep. "
        "Ferritin was 18 in March 2025 according to the lab report."
    )
    assert "contrast frames" in _review(text)["flags"]


def test_single_instruction_with_not_is_not_flagged():
    text = "Take magnesium glycinate with dinner, not coffee. " + "Ferritin and hemoglobin measure different things. " * 30
    assert "contrast frames" not in _review(text)["flags"]


def test_disease_claims_force_human_review():
    report = _review("Magnesium cures insomnia for most people who try it in 2024.")
    assert "disease claim" in report["flags"]
    assert report["requires_human_review"] is True


def test_always_and_never_are_not_inflated_claims():
    report = _review("Never take iron with calcium. Always log the dose in 2025.")
    assert "inflated claims" not in report["flags"]


def test_thresholds_come_from_rubric_file(tmp_path, monkeypatch):
    rubric = tmp_path / "rubric.json"
    rubric.write_text(json.dumps({"score_thresholds": {"pass": 90}}))
    monkeypatch.setenv("CMSBOT_DE_AI_RUBRIC_FILE", str(rubric))
    assert score_thresholds()["pass"] == 90
    assert score_thresholds()["human_review"] == 65


# ---- hash-bound approval ---------------------------------------------------


def _asset_set(tmp_path):
    root = tmp_path / "asset"
    (root / "source").mkdir(parents=True)
    (root / "distribution" / "x").mkdir(parents=True)
    (root / "source" / "content.md").write_text("draft")
    (root / "distribution" / "x" / "thread.json").write_text('{"posts": []}')
    (root / "distribution" / "x" / "de_ai_report.json").write_text(json.dumps({"score": 95}))
    (root / "manifest.json").write_text(json.dumps({"asset_set_id": "asset"}))
    return root


def test_approval_holds_until_content_changes(tmp_path):
    root = _asset_set(tmp_path)
    record_human_approval(root, approved_by="editor")
    assert evaluate_asset_gates(root)["gates"]["publish_ready"]["passed"] is True

    # Re-running a review doesn't invalidate the approval
    (root / "distribution" / "x" / "de_ai_report.json").write_text(json.dumps({"score": 96}))
    assert evaluate_asset_gates(root)["gates"]["human_approved"]["passed"] is True

    (root / "source" / "content.md").write_text("edited draft")
    gate = evaluate_asset_gates(root)["gates"]["human_approved"]
    assert gate["passed"] is False
    assert "changed after approval" in gate["reason"]


def test_legacy_approvals_without_hash_are_not_trusted(tmp_path):
    root = _asset_set(tmp_path)
    (root / "approval").mkdir()
    (root / "approval" / "approval.json").write_text(json.dumps({"human_approved": True}))
    gate = evaluate_asset_gates(root)["gates"]["human_approved"]
    assert gate["passed"] is False
    assert "predates content hashing" in gate["reason"]
