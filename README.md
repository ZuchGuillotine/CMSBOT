# CMSBOT

CMSBOT is the reusable content and marketing core that can be pointed at different projects through a committed project pack.

## What lives in core

- `cmsbot generate`
  Multi-agent long-form and social content generation using the Autoreason pipeline.
- `cmsbot project ...`
  Project profile scaffolding, context rendering, and voice synthesis.
- `cmsbot topics`
  Turns SEO evaluator output and curated briefs into ready-to-run CMSBOT prompts.
- `cmsbot export-blog-draft`
  Exports rendered HTML into a portable `blog_draft.json` artifact.
- `cmsbot export-thread`
  Repurposes a generated blog section into a brand-aligned Threads/X thread with reusable or generated media.
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

4. Turn HTML output into a portable draft artifact when a product repo needs to publish it.

```bash
./run.sh export-blog-draft output/lead-routing-mistakes
```

5. Repurpose a section of the generated blog into a Threads/X-ready thread.

```bash
./run.sh export-thread output/lead-routing-mistakes \
  --project-file projects/northstar-crm.json \
  --section 2 \
  --image-mode auto
```

## Command Surface

```bash
./run.sh generate "Write a blog post about lead routing mistakes" \
  --type blog \
  --project-file projects/example-project.json \
  --style persuasive

./run.sh project init "Northstar CRM"
./run.sh project context projects/example-project.json --output contexts/northstar-crm.md
./run.sh project voice projects/example-project.json --output contexts/northstar-crm.voice.json

./run.sh topics --project-file projects/example-project.json
./run.sh topics --command 0 --project-file projects/example-project.json

./run.sh export-blog-draft output/lead-routing-mistakes
./run.sh export-thread output/lead-routing-mistakes --project-file projects/example-project.json --section 2 --image-mode auto

./run.sh seo research
./run.sh seo evaluate

./run.sh slideshow specs --blog-file output/lead-routing-mistakes/index.html --project-file projects/example-project.json
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
- slideshow voice, CTA variants, and screenshot references
- website and blog base URLs used by downstream adapters

CMSBOT standardizes these portable artifacts:

- generated HTML output
- generated markdown output (`content.md`)
- `blog_draft.json`
- `threads/thread.json`
- `threads/thread.md`
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

./run.sh export-thread output/lead-routing-mistakes \
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

- `threads/thread.json` for APIs, schedulers, or third-party social distribution tools.
- `threads/thread.md` for manual review and copy/paste posting.

Useful options:

- `--section 2` chooses a section by 1-based index.
- `--section-title "Why Lead Routing Breaks"` chooses a section by exact heading.
- `--image-mode auto|reuse|generate|none` controls whether media is reused, generated, or skipped.
- `--save-dir path/to/export` writes thread artifacts outside the default `output/.../threads` directory.

## Slideshow Workflow

```bash
./run.sh slideshow specs --blog-file output/lead-routing-mistakes/index.html --project-file projects/northstar-crm.json
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
scripts/bootstrap.sh
run.sh              Repo-level entrypoint
```

## Notes

- Keep project credentials, unpublished outputs, and private screenshots local and out of git.
- `cmsbot export-blog-draft` produces a portable artifact. Product repos are responsible for uploading images, rewriting URLs, inserting drafts, or posting to their CMS.
- `cmsbot export-thread` produces posting-ready thread artifacts, but product repos remain responsible for publishing through their own API, scheduler, or social tooling.
- `seo/` and `slideshow/` can be reused across projects without copying code when wrappers set `CMSBOT_PROJECT_FILE` and optional output-dir overrides.
