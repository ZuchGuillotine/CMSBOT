# CMSBOT Content Ops Plan

## Goal

Evolve CMSBOT from a prompt-driven content generator into a reusable content operations system that can:

- plan recurring content from a pillar-based hierarchy,
- generate multiple platform-ready assets from a shared narrative,
- preserve CMSBOT's existing image pre/post-processing and blind-selection flow,
- keep analytics as a feedback loop instead of a sidecar script,
- emit output folders that match how creators and downstream adapters actually distribute content.

This plan is intentionally general-purpose. It should work for any project pack, not only for a single niche content strategy.

## Current State

CMSBOT already has strong primitives:

- `cmsbot generate` for Autoreason-driven long-form generation,
- `cmsbot export-thread` for blog-to-thread repurposing,
- `slideshow/` for short-form visual content specs and image generation,
- image planning, multi-provider generation, judge selection, and post-processing,
- SEO and performance evaluation loops,
- a project-pack contract that keeps brand-specific adapters outside core.

The gap is orchestration. The current system is mostly:

- one prompt,
- one output folder,
- optional repurposing steps afterward,
- analytics stored separately from planning and generation.

That is good for individual content jobs, but weak for recurring content operations.

## Target Operating Model

CMSBOT should operate on this hierarchy:

`Pillar -> Outcome -> Narrative Arc -> Asset Set -> Distribution Slot -> Analytics -> Iteration`

### Core objects

- `Pillar`
  - Stable strategic lane such as education, product proof, workflows, comparisons, case studies, or seasonal campaigns.
- `Outcome`
  - The near-term goal for a cadence window such as awareness, lead capture, activation, SEO coverage, or retention.
- `Narrative Arc`
  - The weekly or campaign story tying several posts together.
- `Asset Set`
  - The family of publishable outputs generated from one source brief.
- `Distribution Slot`
  - The date, platform, format, and owner for each deliverable.
- `Analytics Record`
  - Performance metrics tied back to the originating pillar, angle, hook, format, and asset set.

The important shift is that CMSBOT should generate and track a content family, not just a single artifact.

## Proposal 1: Calendar and Cadence Layer

Add a new content-ops layer above the current pipeline instead of replacing the current pipeline.

### New config surface

Extend the project profile with a `content_ops` section:

```json
{
  "content_ops": {
    "pillars": [
      {
        "key": "education",
        "label": "Education",
        "goals": ["traffic", "awareness"],
        "default_formats": ["blog", "thread", "carousel"]
      }
    ],
    "cadence": {
      "timezone": "America/Los_Angeles",
      "planning_horizon_days": 30,
      "default_schedule": [
        {
          "day_offset": 0,
          "formats": ["carousel", "thread"]
        },
        {
          "day_offset": 1,
          "formats": ["video_script"]
        },
        {
          "day_offset": 2,
          "formats": ["blog"]
        }
      ]
    },
    "platforms": {
      "blog": { "enabled": true },
      "x": { "enabled": true },
      "threads": { "enabled": true },
      "instagram": { "enabled": true },
      "linkedin": { "enabled": true },
      "tiktok": { "enabled": true }
    },
    "formats": {
      "carousel": {
        "templates": ["fix-your-stack", "timing-breakdown", "interaction-map", "system-blueprint"]
      }
    }
  }
}
```

Keep this inside the project pack so each repo can vary cadence and channels without changing core behavior.

### New planning artifact

Add a first-class planning artifact:

- `content-calendar.json`
- optional `content-calendar.override.json`

Each calendar entry should describe one source brief plus its scheduled derivatives:

```json
{
  "id": "2026-04-20-education-routing-audit",
  "pillar": "education",
  "outcome": "traffic",
  "arc": "routing-mistakes-week",
  "topic": "how to audit lead routing rules",
  "hook_angles": [
    "Most routing audits fail because they start in the CRM",
    "Your routing errors are usually downstream of enrichment"
  ],
  "asset_set": ["blog", "thread", "carousel", "video_script"],
  "distribution_slots": [
    { "platform": "linkedin", "format": "carousel", "publish_at": "2026-04-20T09:00:00-07:00" },
    { "platform": "x", "format": "thread", "publish_at": "2026-04-20T11:00:00-07:00" },
    { "platform": "tiktok", "format": "video_script", "publish_at": "2026-04-21T09:00:00-07:00" },
    { "platform": "blog", "format": "blog", "publish_at": "2026-04-22T09:00:00-07:00" }
  ]
}
```

This is intentionally cron-friendly and Codex-automation-friendly: one stable JSON file, one deterministic record per planned content family.

### New commands

Add a small set of orchestration commands rather than overloading `generate`:

- `cmsbot calendar plan`
  - Build or refresh the next N days of planned entries from pillars, SEO/topic inputs, and recent analytics.
- `cmsbot calendar show`
  - Print due and upcoming entries.
- `cmsbot batch run --due today`
  - Generate all due asset sets.
- `cmsbot batch run --id <entry-id>`
  - Generate a single content family.
- `cmsbot batch analyze`
  - Pull in performance data and update recommendations for future runs.

This keeps existing commands intact while adding a new recurring mode.

## Proposal 2: Asset-Set Generation

The new orchestration flow should reuse existing CMSBOT primitives.

### Recommended pipeline

1. Planning
   - choose pillar, outcome, angle, and source brief,
   - attach cadence slots and platform targets.
2. Source generation
   - generate one canonical source artifact using Autoreason.
   - Start with blog or long-form narrative markdown because the current pipeline is strongest there.
3. Structured repurposing
   - derive thread, carousel script, caption copy, video script, and optional slideshow spec from the source artifact.
4. Media generation
   - reuse existing image director, judge, and post-processing flows,
   - add platform-specific prompt shaping for carousel slides, thread visuals, and video storyboard stills.
5. Packaging
   - write all assets and manifests into one asset-set directory.
6. Analytics
   - log publish and performance records back against the asset-set id and distribution slot ids.

### Why this should stay source-first

Do not generate every platform asset from scratch in parallel. That would discard the strongest part of CMSBOT: iterative refinement and consistency across derivative assets.

Instead:

- create one strongest source narrative,
- derive other formats from that source,
- let platform-specific generators reshape tone, length, hooks, and structure.

## Proposal 3: Output File Structure

The current output model is mostly command-shaped:

- one slug folder,
- `content.md`,
- `index.html`,
- `images/`,
- maybe `threads/`,
- slideshow artifacts elsewhere.

That is workable for generation, but not for a creator handoff or shared-drive distribution model.

### Recommended directory contract

Move to an asset-set contract grouped by campaign date, pillar, and distribution family:

```text
output/
  calendar/
    content-calendar.json
    execution-log.json
  asset-sets/
    2026/
      2026-04/
        2026-04-20-education-routing-audit/
          manifest.json
          source/
            brief.json
            prompt.md
            content.md
            index.html
            blog_draft.json
          distribution/
            blog/
              publish.json
              review.md
              assets/
            x/
              thread.json
              thread.md
              assets/
            threads/
              post.json
              caption.md
              assets/
            instagram/
              carousel.json
              carousel.md
              captions.md
              assets/
            linkedin/
              carousel.json
              carousel.md
              post.md
              assets/
            tiktok/
              video_script.json
              script.md
              storyboard.json
              assets/
          media/
            generated/
            processed/
            review/
          analytics/
            planned_metrics.json
            performance.json
            insights.json
```

### Why this structure is better

- Everything for one publishable content family lives in one place.
- `distribution/<platform>/` maps directly to how a creator or adapter thinks about delivery.
- `source/` stays portable and reusable.
- `media/` preserves raw/generated/processed distinctions so image pre/post-processing remains auditable.
- `analytics/` keeps feedback alongside the generated family instead of in a disconnected global CSV only.

### Shared-drive recommendation

If a product repo syncs to Google Drive, sync the `distribution/` subtree and selected `media/processed/` assets, not the entire generation workspace.

Drive-friendly rule:

- creators consume `distribution/<platform>/`
- operators inspect `manifest.json`
- automation reads `source/` and `analytics/`
- internal-only raw generation artifacts remain local unless explicitly exported

This keeps the shared-drive view aligned to distribution and reduces clutter from internal intermediate files.

## Proposal 4: Manifests and Metadata

Each asset set should include a top-level `manifest.json` with stable identifiers:

- `asset_set_id`
- `project`
- `pillar`
- `outcome`
- `arc`
- `topic`
- `source_artifacts`
- `distribution_slots`
- `media_assets`
- `analytics_keys`
- `status`

Every downstream artifact should reference the same ids. This is the key to tying together planning, generation, publishing, and analytics.

## Proposal 5: Platform-Ready Content Types

CMSBOT should standardize a small set of output schemas instead of freeform per-platform folders.

Recommended first-wave schemas:

- `blog`
  - existing HTML and `blog_draft.json`
- `thread`
  - existing thread JSON/markdown
- `carousel`
  - slide-by-slide copy, design direction, asset references, CTA
- `video_script`
  - hook, beat-by-beat script, b-roll or visual notes, CTA
- `storyboard`
  - optional bridge between slideshow/video planning and final editing

Do not attempt every channel in phase 1. A sensible initial matrix is:

- `blog`
- `thread`
- `carousel`
- `video_script`

That covers the content-family strategy without overextending the implementation.

## Proposal 6: Preserve and Extend Image Workflows

This part should be preserved, not replaced.

Current strengths worth keeping:

- image planning after the content is finalized,
- multiple image providers,
- judge-based selection,
- post-processing for more natural outputs.

### Recommended extension

Add image generation targets by asset purpose:

- `hero`
- `thread_cover`
- `carousel_slide`
- `video_storyboard_frame`
- `diagram`

Each target should carry:

- `platform`
- `aspect_ratio`
- `safe_text_regions`
- `visual_role`
- `source_section`

This lets CMSBOT reuse the existing image machinery while producing platform-appropriate assets.

## Proposal 7: Analytics Loop as a First-Class System

Right now analytics exists, but it is split:

- SEO evaluation in `seo/`
- social performance tracking in `slideshow/track_performance.py`

That should be unified under the asset-set and calendar model.

### Recommended data model

Track metrics by:

- `asset_set_id`
- `slot_id`
- `platform`
- `format`
- `hook_template`
- `topic`
- `pillar`

### Recommended metrics

Keep platform-specific metrics, but normalize a small shared set:

- impressions
- views
- saves
- shares
- comments
- click-throughs
- conversions
- dwell/completion rate when available

### Recommended outputs

- `analytics/performance.json` per asset set
- global `output/calendar/performance-index.json`
- optional CSV export for spreadsheets and ops review

### Feedback loop

On each planning run, CMSBOT should prefer:

- pillars with recent lift,
- hook types with strong saves or CTR,
- formats with high conversion efficiency,
- follow-ups to winning asset sets.

And it should de-prioritize:

- repeated low-performing hooks,
- underperforming format/platform pairs,
- topics already saturated within the recent horizon.

That turns the current analytics scripts into selection logic, not just reporting.

## Proposal 8: Recurring Automation

The design should be schedulable either in Codex automations or standard cron.

### Cron-friendly workflow

Use idempotent commands with file-based state:

1. `cmsbot calendar plan --days 30`
2. `cmsbot batch run --due today`
3. `cmsbot batch analyze`

Suggested cadence:

- daily morning: plan or refresh upcoming entries,
- daily after publish windows: generate due assets,
- weekly: ingest analytics and rebalance the next calendar window.

### Codex-friendly workflow

Run the same commands behind a thread heartbeat or cron automation.

Good automation boundaries:

- plan next 7 or 30 days,
- generate today’s due asset sets,
- summarize new winners and losers from analytics,
- do not publish directly unless the product repo provides an adapter and the user wants that behavior.

## Proposed Rollout

### Phase 1: Planning and filesystem contract

- add `content_ops` schema to project profiles,
- add `content-calendar.json`,
- add `asset-set` output layout,
- add `manifest.json`,
- keep generation logic mostly source-blog-first.

This is the lowest-risk phase and immediately improves recurring operation and shared-drive usability.

### Phase 2: Structured platform derivatives

- add native `carousel` and `video_script` artifact generators,
- adapt `export-thread` into a more general derivative pipeline,
- keep using blog/source content as the canonical narrative.

### Phase 3: Analytics integration

- move slideshow social metrics into a general analytics module,
- connect performance records to asset-set ids and slot ids,
- let planning read recent winners when generating the next calendar.

### Phase 4: Optional product-repo adapters

- allow project repos to export creator-ready folders to shared drives,
- optionally push publish-ready assets into CMS, schedulers, or social tools.

Core should still stop at portable artifacts by default.

## Recommended Initial Implementation Scope

If the goal is to land this without stalling the repo, start with:

1. new project profile fields for `content_ops`,
2. `cmsbot calendar plan`,
3. `cmsbot batch run --id/--due`,
4. new `asset-set` directory contract,
5. manifest generation,
6. thread + carousel + video-script derivatives from one source article,
7. analytics ids added to existing outputs.

Do not start with:

- direct publishing,
- Google Drive API integration,
- per-platform UI automation,
- a full rewrite of the pipeline internals.

Those belong in adapters or later phases.

## Repo-Level Notes

Based on the current codebase, the cleanest path is:

- keep `AutoreasonPipeline` as the narrative refinement engine,
- build the calendar and asset-set orchestration above it,
- generalize `export-thread` into a derivative-asset pattern,
- unify slideshow performance tracking with the broader analytics model,
- move output organization from command-oriented folders to distribution-oriented asset sets.

This preserves the best parts of CMSBOT while making it usable as a recurring content system.
