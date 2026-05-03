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
