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
