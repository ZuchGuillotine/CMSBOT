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
