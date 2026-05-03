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
