"""Repo-backed goal and strategy scaffolding for long-running CMSBOT work."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cmsbot.workflow import finish_run, load_latest_runs, start_run


DEFAULT_GOALS: dict[str, str] = {
    "weekly-content-calendar.goal.md": """\
---
status: active
cadence: weekly
owner: codex
budget: standard
---

# Weekly Content Calendar

## Objective

Review recent analytics, project strategy, and the current content calendar. Propose the next 14 days of content as durable calendar changes.

## Allowed Commands

- `cmsbot analytics report --days 30`
- `cmsbot calendar plan --project-file <project.json> --days 14`
- `cmsbot goal prepare weekly-content-calendar --project-file <project.json>`
- `cmsbot goal summarize`
- `cmsbot goal status`

## Acceptance Criteria

- Calendar entries map to project pillars and distribution formats.
- Recommendations cite recent analytics signals when available.
- Strategic changes are proposed as file diffs, not hidden chat state.
- A PR is created only when tracked planning, strategy, rubric, routing, or adapter-contract files change and validation passes.
- Publishing is not performed.

## Resume Instructions

Read `strategy/strategy-memory.md`, `output/calendar/performance-index.json`, and the latest `output/calendar/content-calendar.json`, then continue from the last run note.
""",
    "daily-asset-generation.goal.md": """\
---
status: active
cadence: daily
owner: codex
budget: standard
---

# Daily Asset Generation

## Objective

Generate due asset sets from planned calendar entries and prepare platform-specific artifacts for human review.

## Allowed Commands

- `cmsbot generate "<prompt>" --project-file <project.json>`
- `cmsbot export-blog-draft <asset-set-dir>`
- `cmsbot export-thread <asset-set-dir> --project-file <project.json>`
- `cmsbot export-carousel <asset-set-dir> --project-file <project.json>`
- `cmsbot export-video-script <asset-set-dir> --project-file <project.json>`
- `cmsbot review de-ai <asset-set-dir> --platform <platform> --format <format>`
- `cmsbot review gates <asset-set-dir>`

## Acceptance Criteria

- Artifacts are written under the asset-set distribution structure.
- Every generated platform artifact has a de-AI review report.
- Publish readiness is evaluated through review gates.
- Asset status remains review-ready, not published.

## Resume Instructions

Inspect the current asset-set manifest, distribution folders, and `output/runs/` before continuing.
""",
    "weekly-analytics.goal.md": """\
---
status: active
cadence: weekly
owner: codex
budget: low
---

# Weekly Analytics Loop

## Objective

Summarize recent content performance and convert the findings into strategy-memory updates and calendar recommendations.

## Allowed Commands

- `cmsbot analytics report --days 30`
- `cmsbot analytics import-seo-eval --file <report.json>`
- `cmsbot analytics import-social-csv --file <csv> --platform <platform> --format <format>`
- `cmsbot goal summarize`
- `cmsbot goal status`

## Acceptance Criteria

- Findings are tied to asset_set_id, platform, format, pillar, topic, or hook where possible.
- Strategy-memory changes are explicit and reviewable.
- A PR is created only when tracked planning, strategy, rubric, routing, or adapter-contract files change and validation passes.
- Prompt or rubric changes are proposed for review instead of silently applied.

## Resume Instructions

Read the latest analytics report and previous run state under `output/runs/`.
""",
    "de-ai-rubric-evaluation.goal.md": """\
---
status: active
cadence: weekly
owner: codex
budget: low
---

# De-AI Rubric Evaluation

## Objective

Review recent de-AI failures, identify recurring tone problems, and propose updates to platform profiles or rubrics.

## Allowed Commands

- `cmsbot review de-ai <asset-set-dir> --platform <platform> --format <format>`
- `cmsbot review gates <asset-set-dir>`
- `cmsbot routing decide final_polish --trigger de_ai_failed`
- `cmsbot goal status`

## Acceptance Criteria

- Review updates preserve platform-specific style instead of generic polish.
- Human-approved examples are favored over speculative preferences.
- High-risk claims remain human-gated.

## Resume Instructions

Scan `distribution/*/de_ai_report.json` files and `strategy/de-ai-rubrics.json`.
""",
}


DEFAULT_STRATEGY_FILES: dict[str, str] = {
    "strategy-memory.md": """\
# CMSBOT Strategy Memory

This file is the durable strategy memory for Codex, Claude, and humans. Keep concise, dated notes here when analytics or human decisions change content direction.

## Current Priorities

- Preserve project-specific voice and concrete examples.
- Prefer asset sets that can become blog, social thread, carousel, and video-script variants.
- Keep publishing human-gated until a product repo adapter has explicit approval.

## Recent Decisions

- YYYY-MM-DD: Add decisions here with the evidence that justified them.

## Open Questions

- Which platforms have the strongest current signal?
- Which pillar or content format should be paused or expanded next?
""",
    "platform-profiles.json": json.dumps(
        {
            "blog": {
                "ai_tolerance": "medium",
                "focus": ["factual support", "clear structure", "search intent"],
                "avoid": ["unsupported claims", "thin introductions", "generic summaries"],
            },
            "x": {
                "ai_tolerance": "low",
                "focus": ["sharp opening", "specific opinion", "compressed rhythm"],
                "avoid": ["corporate tone", "thread boilerplate", "over-explaining"],
            },
            "threads": {
                "ai_tolerance": "low",
                "focus": ["conversational clarity", "specific examples", "human pacing"],
                "avoid": ["essay tone", "empty hooks", "generic CTAs"],
            },
            "instagram": {
                "ai_tolerance": "medium",
                "focus": ["visual clarity", "short slide copy", "save-worthy framing"],
                "avoid": ["dense paragraphs", "abstract claims", "caption bloat"],
            },
            "tiktok": {
                "ai_tolerance": "low",
                "focus": ["spoken rhythm", "fast hook", "clear visual beats"],
                "avoid": ["scripted essay language", "slow setup", "over-polished narration"],
            },
            "linkedin": {
                "ai_tolerance": "medium",
                "focus": ["operational examples", "credible takeaways", "professional specificity"],
                "avoid": ["gimmicks", "engagement bait", "inflated expertise claims"],
            },
        },
        indent=2,
    )
    + "\n",
    "de-ai-rubrics.json": json.dumps(
        {
            "version": 1,
            "score_thresholds": {
                "pass": 80,
                "human_review": 65,
                "rewrite": 50,
            },
            "review_dimensions": [
                "generic phrasing",
                "inflated claims",
                "repetitive sentence structure",
                "overly symmetrical formatting",
                "platform mismatch",
                "CTA awkwardness",
                "jargon density",
                "lack of concrete examples",
                "human-edit likelihood",
                "visual prompt artifact risk",
            ],
        },
        indent=2,
    )
    + "\n",
    "model-routing.json": json.dumps(
        {
            "version": 1,
            "default_policy": "cost-aware",
            "routing": {
                "calendar_draft": {"tier": "cheap", "escalate_on": ["strategy_change"]},
                "source_draft": {"tier": "mid", "escalate_on": ["regulated_claims", "high_reach"]},
                "final_polish": {"tier": "premium", "escalate_on": ["de_ai_failed", "human_review"]},
                "critique": {"tier": "provider_diverse", "require_different_provider": True},
                "analytics_summary": {"tier": "cheap", "escalate_on": ["strategy_pivot"]},
            },
            "limits": {
                "max_premium_calls_per_asset_set": 2,
                "max_rewrite_loops_per_artifact": 2,
            },
        },
        indent=2,
    )
    + "\n",
}


@dataclass(frozen=True)
class GoalStatus:
    path: Path
    title: str
    status: str
    cadence: str
    owner: str
    budget: str


def initialize_ops_scaffold(root: str | Path, *, overwrite: bool = False) -> list[Path]:
    """Create default goals and strategy memory files under a repo or project root."""
    root_path = Path(root)
    written: list[Path] = []

    goals_dir = root_path / "goals"
    goals_dir.mkdir(parents=True, exist_ok=True)
    for filename, body in DEFAULT_GOALS.items():
        destination = goals_dir / filename
        if destination.exists() and not overwrite:
            continue
        destination.write_text(body, encoding="utf-8")
        written.append(destination)

    strategy_dir = root_path / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    for filename, body in DEFAULT_STRATEGY_FILES.items():
        destination = strategy_dir / filename
        if destination.exists() and not overwrite:
            continue
        destination.write_text(body, encoding="utf-8")
        written.append(destination)

    return written


def load_goal_statuses(root: str | Path) -> list[GoalStatus]:
    """Load high-level status from `goals/*.goal.md` files."""
    goals_dir = Path(root) / "goals"
    if not goals_dir.exists():
        return []

    statuses: list[GoalStatus] = []
    for path in sorted(goals_dir.glob("*.goal.md")):
        text = path.read_text(encoding="utf-8")
        metadata = _parse_front_matter(text)
        statuses.append(
            GoalStatus(
                path=path,
                title=_extract_title(text) or path.stem.replace(".goal", "").replace("-", " ").title(),
                status=str(metadata.get("status") or "unknown"),
                cadence=str(metadata.get("cadence") or "manual"),
                owner=str(metadata.get("owner") or "unassigned"),
                budget=str(metadata.get("budget") or "unspecified"),
            )
        )
    return statuses


def resolve_goal_path(root: str | Path, goal: str) -> Path:
    """Resolve a goal name, filename, or title to a goal markdown file."""
    root_path = Path(root)
    goals_dir = root_path / "goals"
    candidates = [
        goals_dir / goal,
        goals_dir / f"{goal}.goal.md",
        goals_dir / f"{slugify_goal(goal)}.goal.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    wanted = goal.strip().lower()
    for status in load_goal_statuses(root_path):
        if status.title.lower() == wanted or status.path.stem.lower() == wanted:
            return status.path
    raise FileNotFoundError(f"Goal not found under {goals_dir}: {goal}")


def prepare_goal_handoff(
    root: str | Path,
    output_root: str | Path,
    *,
    goal: str,
    project_file: str | Path | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Create a resumable handoff bundle for a scheduled agent goal."""
    root_path = Path(root)
    output_path = Path(output_root)
    goal_path = resolve_goal_path(root_path, goal)
    goal_text = goal_path.read_text(encoding="utf-8")
    title = _extract_title(goal_text) or goal_path.name

    canonical_files = _canonical_files(root_path, output_path, project_file=project_file)
    run_record, run_path = start_run(
        output_path,
        kind=f"goal-prepare-{slugify_goal(title)}",
        parameters={
            "goal": title,
            "goal_file": str(goal_path),
            "project_file": str(project_file) if project_file else "",
        },
        files=[goal_path, *[Path(item["path"]) for item in canonical_files if item["exists"]]],
    )
    handoff_path = run_path.parent / "handoff.md"
    handoff = {
        "run_id": run_record["run_id"],
        "goal": title,
        "goal_file": str(goal_path),
        "project_file": str(project_file) if project_file else "",
        "canonical_files": canonical_files,
        "recent_runs": load_latest_runs(output_path, limit=5),
        "pr_creation_cue": {
            "create_pr_when": (
                "A scheduled goal produces a non-empty proposed diff to tracked planning, strategy, "
                "rubric, routing, or adapter-contract files and validation passes."
            ),
            "do_not_create_pr_for": [
                "routine asset output",
                "single analytics imports",
                "single de-AI reports",
                "external publishing",
            ],
        },
    }
    handoff_path.write_text(render_goal_handoff(handoff, goal_text=goal_text), encoding="utf-8")
    finish_run(
        run_path,
        files_changed=[handoff_path],
        summary=f"Prepared handoff for {title}.",
        next_safe_action="Scheduled agent should inspect handoff.md and create a PR only if it produces validated tracked-file diffs.",
    )
    return handoff, run_path, handoff_path


def render_goal_handoff(handoff: dict[str, Any], *, goal_text: str) -> str:
    """Render a markdown handoff for Codex, Claude, or a human operator."""
    lines = [
        f"# Goal Handoff: {handoff['goal']}",
        "",
        f"- Run ID: `{handoff['run_id']}`",
        f"- Goal file: `{handoff['goal_file']}`",
        f"- Project file: `{handoff.get('project_file') or 'n/a'}`",
        "",
        "## PR Creation Cue",
        "",
        handoff["pr_creation_cue"]["create_pr_when"],
        "",
        "Do not create PRs for:",
        "",
    ]
    lines.extend(f"- {item}" for item in handoff["pr_creation_cue"]["do_not_create_pr_for"])
    lines.extend(["", "## Canonical Files", ""])
    for item in handoff["canonical_files"]:
        status = "present" if item["exists"] else "missing"
        lines.append(f"- `{item['path']}`: {status}")
    lines.extend(["", "## Goal Spec", "", goal_text.strip(), ""])
    return "\n".join(lines)


def summarize_goal_run(output_root: str | Path, *, run_id: str | None = None) -> tuple[dict[str, Any], Path]:
    """Write a compact summary for a prepared or completed workflow run."""
    output_path = Path(output_root)
    run_path = _resolve_run_path(output_path, run_id)
    record = json.loads(run_path.read_text(encoding="utf-8"))
    summary_path = run_path.parent / "summary.md"
    lines = [
        f"# Run Summary: {record.get('run_id')}",
        "",
        f"- Kind: `{record.get('kind')}`",
        f"- Status: `{record.get('status')}`",
        f"- Started: `{record.get('started_at')}`",
        f"- Finished: `{record.get('finished_at') or 'n/a'}`",
        f"- Summary: {record.get('summary') or 'No summary recorded.'}",
        f"- Next safe action: {record.get('next_safe_action') or 'Review run artifacts.'}",
        "",
        "## Files Changed",
        "",
    ]
    changed = record.get("files_changed") or []
    lines.extend(f"- `{path}`" for path in changed) if changed else lines.append("- None recorded")
    lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return record, summary_path


def slugify_goal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "goal"


def _parse_front_matter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\n(.*?)\n---", text, flags=re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata


def _canonical_files(root: Path, output_root: Path, *, project_file: str | Path | None) -> list[dict[str, Any]]:
    paths = [
        root / "strategy" / "strategy-memory.md",
        root / "strategy" / "platform-profiles.json",
        root / "strategy" / "de-ai-rubrics.json",
        root / "strategy" / "model-routing.json",
        output_root / "calendar" / "content-calendar.json",
        output_root / "calendar" / "performance-index.json",
    ]
    if project_file:
        paths.insert(0, Path(project_file))
    return [{"path": str(path), "exists": path.exists()} for path in paths]


def _resolve_run_path(output_root: Path, run_id: str | None) -> Path:
    if run_id:
        path = output_root / "runs" / run_id / "run.json"
        if not path.exists():
            raise FileNotFoundError(f"Run not found: {run_id}")
        return path
    latest = load_latest_runs(output_root, limit=1)
    if not latest:
        raise FileNotFoundError(f"No runs found under {output_root / 'runs'}")
    return output_root / "runs" / str(latest[0]["run_id"]) / "run.json"


def _extract_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None
