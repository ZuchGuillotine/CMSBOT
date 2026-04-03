# CMSBOT — Project Status

## Status

CMSBOT is now organized as a reusable marketing core:

- multi-agent content generation
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
| Autoreason loop | Done | Author → Strawman → Rewrite/Synthesize → Blind Judges → Convergence |
| Multi-provider image flow | Done | Grok + Nano Banana + Unsplash + DALL-E fallback with image judging |
| Project profiles | Done | `cmsbot project init/context/voice` |
| Topic generation | Done | `cmsbot topics` merges SEO briefs and curated briefs |
| Blog draft export | Done | `cmsbot export-blog-draft` writes portable draft artifacts |
| SEO toolkit | Done | `seo/` uses `CMSBOT_PROJECT_FILE` |
| Slideshow pipeline | Done | `slideshow/` accepts project profile input |
| Repo bootstrap | Done | `./run.sh bootstrap` installs Python + SEO dependencies |
| Repo command surface | Done | `./run.sh` dispatches core, SEO, and slideshow commands |

## Current Interface

- Stable project contract: `projects/example-project.json`
- Stable portable artifacts:
  - generated HTML output
  - `blog_draft.json`
  - `topic_briefs_*.json`
  - slideshow spec JSON
  - review HTML
  - generated image folders

## Explicit Non-Goals For Core

These remain product-side concerns:

- S3 upload destinations
- admin auth cookies
- direct DB insertion
- bastion/SSM tunnels
- project-specific CMS publishing workflows

## Next Steps

- Add more product repos against the same `CMSBOT_PROJECT_FILE` contract.
- Freeze the project-pack interface after two repos are running cleanly.
- Decide later whether distribution should remain pinned-git internal or move to package publishing.
