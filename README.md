# CMSBOT

CMSBOT is the reusable content and marketing core that can be pointed at different projects through a committed project pack.

## What lives in core

- `cmsbot generate`
  Multi-agent long-form and social content generation using the Autoreason pipeline.
- `cmsbot project ...`
  Project profile scaffolding, context rendering, and voice synthesis.
- `cmsbot calendar ...`
  Build and inspect recurring content calendars from project-level pillar and cadence settings.
- `cmsbot topics`
  Turns SEO evaluator output and curated briefs into ready-to-run CMSBOT prompts.
- `cmsbot export-blog-draft`
  Exports rendered HTML into a portable `blog_draft.json` artifact.
- `cmsbot export-thread`
  Repurposes a generated blog section into a brand-aligned Threads/X thread with reusable or generated media.
- `cmsbot export-carousel`
  Derives creator-ready Instagram or LinkedIn carousel packages from generated source content.
- `cmsbot export-video-script`
  Derives creator-ready short-form video script packages from generated source content.
- `cmsbot analytics ...`
  Logs social performance, imports SEO evaluation output, and summarizes cross-platform winners keyed by asset set ids.
- [`seo/`](./seo/README.md)
  Project-driven keyword research and Search Console / GA4 evaluation.
- [`slideshow/`](./slideshow/README.md)
  Blog-to-slideshow spec generation, image generation, post-processing, and review output.

## What stays in product repos

These do not belong in core:

- project CMS publishing logic
- S3 upload paths and auth cookies
- direct database insertion
- tunnels, bastions, and other environment-specific delivery logic
- local screenshots, unpublished briefs, credentials, and brand-only prompt files

Those stay in each product repo as local adapters and project packs.

## Install

Requires Python 3.10+ for the core package.

```bash
git clone https://github.com/ZuchGuillotine/CMSBOT.git
cd CMSBOT
./run.sh bootstrap
```

Bootstrap creates `.venv`, installs the Python package, and installs `seo` Node dependencies.

## Quick Start

1. Create a project profile.

```bash
./run.sh project init "Northstar CRM"
```

2. Render reusable context from it.

```bash
./run.sh project context projects/northstar-crm.json --output contexts/northstar-crm.md
./run.sh project voice projects/northstar-crm.json --output contexts/northstar-crm.voice.json
```

3. Generate content with the project profile directly.

```bash
./run.sh generate "Write a blog post about lead routing mistakes" \
  --type blog \
  --project-file projects/northstar-crm.json \
  --style persuasive
```

4. Plan recurring content from the project profile's `content_ops` settings.

```bash
./run.sh calendar plan --project-file projects/northstar-crm.json --days 14
./run.sh calendar show
```

5. Turn generated source output into derivative distribution assets.

```bash
./run.sh export-thread output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --section 2 \
  --image-mode auto

./run.sh export-carousel output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --platform instagram

./run.sh export-video-script output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --platform tiktok
```

6. Turn HTML output into a portable draft artifact when a product repo needs to publish it.

```bash
./run.sh export-blog-draft output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes
```

## Command Surface

```bash
./run.sh generate "Write a blog post about lead routing mistakes" \
  --type blog \
  --project-file projects/example-project.json \
  --style persuasive

./run.sh calendar plan --project-file projects/example-project.json --days 30
./run.sh calendar show

./run.sh project init "Northstar CRM"
./run.sh project context projects/example-project.json --output contexts/northstar-crm.md
./run.sh project voice projects/example-project.json --output contexts/northstar-crm.voice.json

./run.sh topics --project-file projects/example-project.json
./run.sh topics --command 0 --project-file projects/example-project.json

./run.sh export-blog-draft output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes
./run.sh export-thread output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes --project-file projects/example-project.json --section 2 --image-mode auto
./run.sh export-carousel output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes --project-file projects/example-project.json --platform instagram
./run.sh export-video-script output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes --project-file projects/example-project.json --platform tiktok

./run.sh analytics log-social --asset-set-id 2026-04-18-lead-routing-mistakes --platform instagram --format carousel --views-7d 25000 --saves 1200
./run.sh analytics import-seo-eval --file seo/output/evaluation_YYYY-MM-DD.json
./run.sh analytics report --days 30

./run.sh seo research
./run.sh seo evaluate

./run.sh slideshow specs --blog-file output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes/source/index.html --project-file projects/example-project.json
./run.sh slideshow pipeline --project-file projects/example-project.json --batch
```

## Project Contract

`CMSBOT_PROJECT_FILE` is the stable contract between CMSBOT and a product repo.

Reference shape:

```text
projects/example-project.json
```

The project profile carries product-specific inputs such as:

- brand voice and content rules
- SEO seed keywords, competitors, analytics config, and blog path settings
- content pillars, cadence, and default distribution formats
- slideshow voice, CTA variants, and screenshot references
- website and blog base URLs used by downstream adapters

CMSBOT standardizes these portable artifacts:

- asset-set manifests and source briefs
- generated HTML output
- generated markdown output (`content.md`)
- `blog_draft.json`
- `distribution/x/thread.json`
- `distribution/x/thread.md`
- `distribution/instagram/carousel.json`
- `distribution/instagram/carousel.md`
- `distribution/tiktok/video_script.json`
- `distribution/tiktok/script.md`
- `calendar/content-calendar.json`
- `calendar/performance-index.json`
- `topic_briefs_*.json`
- slideshow spec JSON
- review HTML
- generated image folders

Product repos can add local context files, style overrides, curated topic briefs, screenshots, and publish adapters without changing the CMSBOT core.

See [`docs/project-pack-integration.md`](./docs/project-pack-integration.md) for the repo-by-repo integration guide and wrapper example.

## SEO Workflow

```bash
export CMSBOT_PROJECT_FILE=projects/northstar-crm.json
./run.sh seo research
./run.sh seo evaluate
```

Then turn evaluator output into prompts:

```bash
./run.sh topics --project-file projects/northstar-crm.json
./run.sh topics --command 0 --project-file projects/northstar-crm.json
```

Set `CMSBOT_SEO_OUTPUT_DIR` if a wrapper wants SEO artifacts written somewhere other than `seo/output`.

## Threads/X Workflow

Generate a blog post first, then repurpose one section into a short thread:

```bash
./run.sh generate "Write a blog post about lead routing mistakes" \
  --type blog \
  --project-file projects/northstar-crm.json \
  --style persuasive

./run.sh export-thread output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --section 2 \
  --image-mode auto
```

Key behavior:

- Uses the generated blog content from `content.md` when available, with `index.html` as fallback.
- Summarizes a single section into a maximum of 5 posts.
- Enforces project-specific branding, voice, and disallowed-claim rules from the project profile.
- Avoids hashtags and requires a natural reference to the project in the thread.
- Reuses an existing section image or hero image when possible, or generates a related social visual with the existing image pipeline.

Output artifacts:

- `distribution/x/thread.json` for APIs, schedulers, or third-party social distribution tools.
- `distribution/x/thread.md` for manual review and copy/paste posting.

Useful options:

- `--section 2` chooses a section by 1-based index.
- `--section-title "Why Lead Routing Breaks"` chooses a section by exact heading.
- `--image-mode auto|reuse|generate|none` controls whether media is reused, generated, or skipped.
- `--save-dir path/to/export` writes thread artifacts outside the default distribution directory.

## Carousel Workflow

Generate source content first, then derive a carousel:

```bash
./run.sh export-carousel output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --platform instagram
```

Output artifacts:

- `distribution/instagram/carousel.json`
- `distribution/instagram/carousel.md`
- `distribution/instagram/captions.md`

## Video Script Workflow

Generate source content first, then derive a short-form video package:

```bash
./run.sh export-video-script output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --platform tiktok
```

Output artifacts:

- `distribution/tiktok/video_script.json`
- `distribution/tiktok/script.md`
- `distribution/tiktok/storyboard.json`

## Content Calendar And Analytics

Recurring content planning and performance tracking now sit alongside generation:

```bash
./run.sh calendar plan --project-file projects/northstar-crm.json --days 30
./run.sh calendar show

./run.sh analytics log-social \
  --asset-set-id 2026-04-18-lead-routing-mistakes \
  --slot-id 2026-04-18-lead-routing-mistakes:carousel:1 \
  --platform instagram \
  --format carousel \
  --views-7d 25000 \
  --saves 1200

./run.sh analytics import-seo-eval --file seo/output/evaluation_YYYY-MM-DD.json
./run.sh analytics report --days 30
```

Key behavior:

- `calendar plan` reads `content_ops` from the project profile.
- Generated entries now include stable `asset_set_id` and `slot_id` values.
- `analytics log-social` writes a shared performance index and, when possible, an asset-set-local analytics file.
- Recent analytics signals are fed back into future planning so stronger pillars, formats, and hook styles are prioritized.

## Output Layout

By default, `cmsbot generate` now writes to an asset-set structure:

```text
output/
  asset-sets/
    2026/
      2026-04/
        2026-04-18-lead-routing-mistakes/
          manifest.json
          source/
            brief.json
            prompt.md
            content.md
            index.html
            blog_draft.json
          distribution/
            x/
            instagram/
            tiktok/
          analytics/
  calendar/
    content-calendar.json
    performance-index.json
```

Explicit `--output` paths still work for legacy workflows, but new default runs use the asset-set layout.

## Slideshow Workflow

```bash
./run.sh slideshow specs --blog-file output/asset-sets/2026/2026-04/2026-04-18-lead-routing-mistakes/source/index.html --project-file projects/northstar-crm.json
./run.sh slideshow specs --brief-file briefs/q2-launch.md --project-file projects/northstar-crm.json
./run.sh slideshow pipeline --project-file projects/northstar-crm.json --batch
```

Add project screenshots to `slideshow/screenshots/` when a spec references `app_screenshot`.

Set `CMSBOT_SLIDESHOW_OUTPUT_DIR` if a wrapper wants slideshow artifacts written somewhere other than `slideshow/output`.
Set `CMSBOT_SLIDESHOW_CONFIG_FILE`, `CMSBOT_SLIDESHOW_HOOK_PROMPT_FILE`, and `CMSBOT_SLIDESHOW_SCREENSHOTS_DIR` when a product repo needs pack-specific slideshow behavior without forking the core pipeline.

## Configuration

Copy `.env.example` to `.env` and add the providers you want to use:

```env
Anthropic_API_Key=
OpenAI_API_Key=
GEMINI_API_Key=
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_BASE_URL=https://api.together.xyz/v1
XAI_API_KEY=
UNSPLASH_ACCESS_KEY=
```

For blog image generation, CMSBOT can use any configured combination of Grok Imagine, Nano Banana, Unsplash, and DALL-E fallback, then run an image judge to pick the most natural-looking candidate.

## Repo Layout

```text
src/cmsbot/         Python package and CLI
projects/           Example project profiles
contexts/           Optional generated or hand-authored markdown contexts
seo/                Node-based SEO research and evaluation toolkit
slideshow/          Short-form content repurposing pipeline
output/             Generated asset sets, calendars, and analytics indexes
scripts/bootstrap.sh
run.sh              Repo-level entrypoint
```

## Notes

- Keep project credentials, unpublished outputs, and private screenshots local and out of git.
- `cmsbot export-blog-draft` produces a portable artifact. Product repos are responsible for uploading images, rewriting URLs, inserting drafts, or posting to their CMS.
- `cmsbot export-thread`, `cmsbot export-carousel`, and `cmsbot export-video-script` produce posting-ready artifacts, but product repos remain responsible for publishing through their own API, scheduler, or social tooling.
- `seo/` and `slideshow/` can be reused across projects without copying code when wrappers set `CMSBOT_PROJECT_FILE` and optional output-dir overrides.
