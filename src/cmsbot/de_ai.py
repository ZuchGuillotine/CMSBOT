"""Platform-aware de-AI review helpers for generated CMSBOT artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cmsbot.config import Config
from cmsbot.content_ops import resolve_asset_set_root, resolve_source_dir
from cmsbot.repurpose import parse_json_object, run_json_generation, update_distribution_manifest
from cmsbot.providers import build_provider


GENERIC_PHRASES = [
    "in today's fast-paced",
    "game-changer",
    "revolutionary",
    "unlock",
    "ultimate guide",
    "delve into",
    "dive into",
    "it is important to note",
    "in this article",
    "whether you're",
    "not only",
    "but also",
    "seamlessly",
    "robust",
    "leverage",
    "transform your",
    "take your",
]

INFLATED_CLAIMS = [
    "guaranteed",
    "always",
    "never",
    "everyone",
    "no one",
    "proven to",
    "scientifically proven",
    "best-in-class",
    "industry-leading",
]

AWKWARD_CTAS = [
    "click here",
    "don't miss out",
    "act now",
    "limited time",
    "link in bio to learn more",
]

PLATFORM_LENGTH_LIMITS = {
    "instagram": 120,
    "tiktok": 100,
    "x": 280,
    "threads": 280,
    "linkedin": 420,
    "blog": 700,
}


def review_de_ai(
    asset_set_dir: str | Path,
    *,
    platform: str,
    content_format: str,
    output_root: str | Path | None = None,
    env_file: str | Path | None = None,
    provider_name: str | None = None,
    use_llm: bool = False,
    save: bool = True,
) -> tuple[dict[str, Any], Path | None, Path | None]:
    """Review a generated artifact for AI-like tone and platform mismatch."""
    asset_root = resolve_asset_set_root(asset_set_dir)
    target_path = resolve_review_target(asset_root, platform=platform, content_format=content_format)
    content = target_path.read_text(encoding="utf-8").strip()
    asset_set_id = _load_asset_set_id(asset_root)

    report = heuristic_review(
        content,
        asset_set_id=asset_set_id,
        platform=platform,
        content_format=content_format,
        source_path=target_path,
    )

    if use_llm:
        report = _merge_llm_review(
            report,
            content=content,
            platform=platform,
            content_format=content_format,
            env_file=env_file,
            provider_name=provider_name,
        )

    report_path: Path | None = None
    review_path: Path | None = None
    if save:
        report_path, review_path = write_de_ai_artifacts(asset_root, platform=platform, report=report)

    return report, report_path, review_path


def resolve_review_target(asset_set_dir: str | Path, *, platform: str, content_format: str) -> Path:
    """Find the platform artifact that should be reviewed."""
    asset_root = resolve_asset_set_root(asset_set_dir)
    normalized_format = content_format.replace("-", "_")

    candidates: list[Path] = []
    distribution_dir = asset_root / "distribution" / platform
    if normalized_format == "carousel":
        candidates = [distribution_dir / "carousel.md", distribution_dir / "carousel.json"]
    elif normalized_format in {"video", "video_script"}:
        candidates = [distribution_dir / "script.md", distribution_dir / "video_script.json"]
    elif normalized_format in {"thread", "x_thread", "social_thread"}:
        candidates = [distribution_dir / "thread.md", distribution_dir / "thread.json"]
    elif normalized_format in {"blog", "article"}:
        source_dir = resolve_source_dir(asset_root)
        candidates = [source_dir / "content.md", source_dir / "index.html", source_dir / "blog_draft.json"]
    else:
        candidates = [
            distribution_dir / f"{normalized_format}.md",
            distribution_dir / f"{normalized_format}.json",
            distribution_dir / "content.md",
        ]

    for path in candidates:
        if path.exists() and path.is_file():
            return path
    candidate_list = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"No reviewable artifact found for {platform}/{content_format}. Checked: {candidate_list}")


def heuristic_review(
    content: str,
    *,
    asset_set_id: str,
    platform: str,
    content_format: str,
    source_path: str | Path,
) -> dict[str, Any]:
    """Deterministic review that works without API keys."""
    text = _normalize_content_for_review(content)
    lower = text.lower()
    flags: list[str] = []
    recommendations: list[str] = []
    score = 100

    generic_hits = [phrase for phrase in GENERIC_PHRASES if phrase in lower]
    if generic_hits:
        score -= min(24, len(generic_hits) * 6)
        flags.append("generic phrasing")
        recommendations.append(f"Replace generic phrases: {', '.join(generic_hits[:5])}.")

    claim_hits = [phrase for phrase in INFLATED_CLAIMS if re.search(rf"\b{re.escape(phrase)}\b", lower)]
    if claim_hits:
        score -= min(18, len(claim_hits) * 6)
        flags.append("inflated claims")
        recommendations.append("Soften absolute claims or add evidence before publishing.")

    cta_hits = [phrase for phrase in AWKWARD_CTAS if phrase in lower]
    if cta_hits:
        score -= min(12, len(cta_hits) * 4)
        flags.append("CTA awkwardness")
        recommendations.append("Rewrite the CTA so it matches the platform and brand voice.")

    repeated_starts = _repeated_sentence_starts(text)
    if repeated_starts:
        score -= min(12, len(repeated_starts) * 4)
        flags.append("repetitive sentence structure")
        recommendations.append(f"Vary repeated sentence openings: {', '.join(repeated_starts[:4])}.")

    avg_sentence_length = _average_sentence_length(text)
    if avg_sentence_length > 28:
        score -= 10
        flags.append("overlong sentences")
        recommendations.append("Break long sentences into shorter, more human-paced lines.")

    line_limit = PLATFORM_LENGTH_LIMITS.get(platform, 280)
    long_lines = [line for line in text.splitlines() if len(line.strip()) > line_limit]
    if long_lines and platform in {"instagram", "tiktok", "x", "threads"}:
        score -= min(16, len(long_lines) * 4)
        flags.append("platform mismatch")
        recommendations.append(f"Shorten lines for {platform}; {len(long_lines)} line(s) exceed the suggested limit.")

    if len(text) > 600 and not re.search(r"\b(\d+|example|for instance|case|customer|before|after)\b", lower):
        score -= 8
        flags.append("lack of concrete examples")
        recommendations.append("Add at least one concrete example, number, scenario, or before/after detail.")

    visual_artifact_terms = ["hyper-realistic", "cinematic lighting", "8k", "ultra detailed", "award-winning"]
    if any(term in lower for term in visual_artifact_terms):
        score -= 6
        flags.append("visual prompt artifact risk")
        recommendations.append("Remove image-generation prompt residue from publishable copy.")

    score = max(0, min(100, score))
    requires_human_review = score < 80 or bool(set(flags) & {"inflated claims", "platform mismatch"})

    return {
        "asset_set_id": asset_set_id,
        "platform": platform,
        "format": content_format,
        "source_path": str(source_path),
        "score": score,
        "risk_level": _risk_level(score),
        "flags": flags,
        "rewrite_recommendations": recommendations,
        "requires_human_review": requires_human_review,
        "reviewer": "heuristic",
        "llm_review": None,
    }


def write_de_ai_artifacts(asset_set_dir: str | Path, *, platform: str, report: dict[str, Any]) -> tuple[Path, Path]:
    """Write review artifacts into the platform distribution folder."""
    asset_root = resolve_asset_set_root(asset_set_dir)
    destination_dir = asset_root / "distribution" / platform
    destination_dir.mkdir(parents=True, exist_ok=True)

    report_path = destination_dir / "de_ai_report.json"
    review_path = destination_dir / "review.md"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    review_path.write_text(render_de_ai_markdown(report), encoding="utf-8")
    update_distribution_manifest(
        asset_root,
        platform=platform,
        artifact_paths={
            "de_ai_report_json": report_path,
            "de_ai_review_markdown": review_path,
        },
    )
    return report_path, review_path


def render_de_ai_markdown(report: dict[str, Any]) -> str:
    flags = report.get("flags") or []
    recommendations = report.get("rewrite_recommendations") or []
    lines = [
        f"# De-AI Review: {report.get('platform')} / {report.get('format')}",
        "",
        f"- Asset set: `{report.get('asset_set_id')}`",
        f"- Score: `{report.get('score')}`",
        f"- Risk level: `{report.get('risk_level')}`",
        f"- Requires human review: `{bool(report.get('requires_human_review'))}`",
        f"- Reviewer: `{report.get('reviewer')}`",
        "",
        "## Flags",
        "",
    ]
    if flags:
        lines.extend(f"- {flag}" for flag in flags)
    else:
        lines.append("- None")
    lines.extend(["", "## Rewrite Recommendations", ""])
    if recommendations:
        lines.extend(f"- {recommendation}" for recommendation in recommendations)
    else:
        lines.append("- No rewrite required by the current rubric.")
    lines.append("")
    return "\n".join(lines)


def _merge_llm_review(
    report: dict[str, Any],
    *,
    content: str,
    platform: str,
    content_format: str,
    env_file: str | Path | None,
    provider_name: str | None,
) -> dict[str, Any]:
    config = Config.from_env(env_file)
    available = config.available_providers()
    if not available:
        raise ValueError("No API providers configured. Omit --llm or set at least one provider API key.")
    chosen_provider = provider_name or (config.roles.synthesizer if config.roles.synthesizer in available else available[0])
    provider = build_provider(chosen_provider, config)

    system_prompt = """\
You are a strict platform-native editor. Return ONLY valid JSON with keys:
"score", "risk_level", "flags", "rewrite_recommendations", "requires_human_review".
Judge whether the artifact sounds overly AI-generated, generic, over-polished, risky, or mismatched for the platform.
"""
    prompt = f"""\
PLATFORM: {platform}
FORMAT: {content_format}

HEURISTIC REVIEW:
{json.dumps(report, indent=2)}

ARTIFACT:
{content[:9000]}
"""
    llm_payload = parse_json_object(
        run_json_generation(provider, system_prompt=system_prompt, prompt=prompt, max_tokens=1400).content,
        required_keys=["score", "risk_level", "flags", "rewrite_recommendations", "requires_human_review"],
    )
    merged = dict(report)
    merged["score"] = int(llm_payload.get("score", report["score"]))
    merged["risk_level"] = str(llm_payload.get("risk_level", report["risk_level"]))
    merged["flags"] = _dedupe_strings([*report.get("flags", []), *llm_payload.get("flags", [])])
    merged["rewrite_recommendations"] = _dedupe_strings(
        [*report.get("rewrite_recommendations", []), *llm_payload.get("rewrite_recommendations", [])]
    )
    merged["requires_human_review"] = bool(llm_payload.get("requires_human_review", report["requires_human_review"]))
    merged["reviewer"] = f"heuristic+{chosen_provider}"
    merged["llm_review"] = llm_payload
    return merged


def _load_asset_set_id(asset_root: Path) -> str:
    manifest_path = asset_root / "manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            value = payload.get("asset_set_id")
            if value:
                return str(value)
        except json.JSONDecodeError:
            pass
    return asset_root.name


def _normalize_content_for_review(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return stripped
    return stripped


def _repeated_sentence_starts(text: str) -> list[str]:
    starts: dict[str, int] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z']+", sentence.lower())
        if len(words) < 3:
            continue
        key = " ".join(words[:2])
        starts[key] = starts.get(key, 0) + 1
    return [key for key, count in starts.items() if count >= 3]


def _average_sentence_length(text: str) -> float:
    sentences = [sentence for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    if not sentences:
        return 0.0
    word_count = sum(len(re.findall(r"[A-Za-z0-9']+", sentence)) for sentence in sentences)
    return word_count / len(sentences)


def _risk_level(score: int) -> str:
    if score >= 80:
        return "low"
    if score >= 65:
        return "medium"
    return "high"


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text)
    return result
