# CMSBOT — Project Status

## Status

CMSBOT is now organized as a reusable marketing core:

- multi-agent content generation
- recurring content calendar planning
- creator-ready derivative exports for thread, carousel, and video script workflows
- analytics indexing keyed by asset set and slot ids
- project profiles, context rendering, and voice synthesis
- project-driven SEO research and evaluation
- slideshow generation and review tooling
- portable `blog_draft.json` export from rendered HTML

The intended boundary is:

- core repo: reusable logic and portable artifacts
- product repo: brand pack, screenshots, curated briefs, and publish adapters

## Working Capabilities

| Area | Status | Notes |
|---|---|---|
| Autoreason loop | Done | Author -> Strawman -> Rewrite/Synthesize -> Blind Judges -> Convergence |
| Provider mix | Done | Anthropic, OpenAI, Gemini, and OpenAI-compatible providers are wired |
| Budget tracking | Done | Per-call token and cost tracking with configurable budget cap |
| Multi-provider image flow | Done | Grok + Nano Banana + Unsplash + DALL-E fallback with image judging |
| Project profiles | Done | `cmsbot project init/context/voice` |
| Content calendar | Done | `cmsbot calendar plan/show` reads `content_ops` from project profiles |
| Topic generation | Done | `cmsbot topics` merges SEO briefs and curated briefs |
| Blog draft export | Done | `cmsbot export-blog-draft` writes portable draft artifacts |
| Derivative exports | Done | `cmsbot export-thread`, `export-carousel`, and `export-video-script` derive platform-ready assets from source content |
| Shared analytics index | Done | `cmsbot analytics ...` writes `performance-index.json` and asset-set-local performance files |
| SEO toolkit | Done | `seo/` uses `CMSBOT_PROJECT_FILE` and optional output-dir override |
| Slideshow pipeline | Done | `slideshow/` accepts project profile input and optional output-dir override |
| Repo bootstrap | Done | `./run.sh bootstrap` installs Python package plus SEO dependencies |
| Repo command surface | Done | `./run.sh` dispatches core, SEO, and slideshow workflows |
| Asset-set output layout | Done | Generated source, distribution, and analytics artifacts are grouped under stable asset-set directories |

## Current Interface

- Stable project contract: `projects/example-project.json`
- Stable portable artifacts:
  - asset-set manifest and source brief output
  - generated HTML output
  - `blog_draft.json`
  - thread export JSON and markdown
  - carousel export JSON, markdown, and caption files
  - video script export JSON, markdown, and storyboard files
  - content calendar JSON
  - performance index JSON
  - `topic_briefs_*.json`
  - slideshow spec JSON
  - review HTML
  - generated image folders

## Core Layout

```text
src/cmsbot/
├── cli.py              # Click CLI with Rich output
├── config.py           # Env-based config and provider setup
├── pipeline.py         # Main Autoreason orchestrator loop
├── renderer.py         # Markdown-to-HTML conversion and templates
├── blog_draft.py       # Portable draft export from generated HTML
├── content_ops.py      # Calendar planning and asset-set layout helpers
├── project.py          # Project profile schema and rendering helpers
├── repurpose.py        # Shared source-to-derivative helpers
├── social_derivatives.py # Carousel and video script export flows
├── topics.py           # SEO brief loading and command generation
├── voice.py            # Project voice synthesis
├── providers/          # Anthropic, OpenAI, Gemini, image providers
└── templates/          # Blog and social templates
```

## Explicit Non-Goals For Core

These remain product-side concerns:

- S3 upload destinations
- admin auth cookies
- direct DB insertion
- bastion and SSM tunnels
- project-specific CMS publishing workflows

## Next Steps

- Add more product repos against the same `CMSBOT_PROJECT_FILE` contract.
- Tighten the analytics ingestion path from real social platform exports and adapters.
- Decide later whether distribution should remain pinned-git internal or move to package publishing.
