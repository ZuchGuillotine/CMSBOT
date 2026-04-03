# CMSBOT

CMSBOT is the reusable content and marketing core that can be pointed at different projects through a committed project pack.

## What lives in core

- `cmsbot generate`
  Multi-agent long-form and social content generation using the Autoreason pipeline.
- `cmsbot project ...`
  Project profile scaffolding, context rendering, and voice synthesis.
- `cmsbot topics`
  Turns SEO evaluation output and curated briefs into ready-to-run generation commands.
- `cmsbot export-blog-draft`
  Exports rendered HTML into a portable `blog_draft.json` artifact.
- [`seo/`](./seo/README.md)
  Project-driven keyword research and Search Console / GA4 evaluation.
- [`slideshow/`](./slideshow/README.md)
  Blog-to-slideshow spec generation, image generation, post-processing, and review output.

What does not belong in core:

- project CMS publishing logic
- S3 upload paths and auth cookies
- direct database insertion
- tunnels, bastions, and other environment-specific delivery logic

Those stay in each product repo as local adapters.

## Quick Start

```bash
git clone https://github.com/ZuchGuillotine/CMSBOT.git
cd CMSBOT
./run.sh bootstrap
```

Bootstrap creates `.venv`, installs the Python package, and installs `seo` Node dependencies.

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

./run.sh export-blog-draft output/lead-routing-mistakes

./run.sh seo research
./run.sh seo evaluate

./run.sh slideshow pipeline --project-file ../projects/example-project.json --batch
```

## Project Contract

The stable contract between CMSBOT and a product repo is a project profile JSON file.

Reference shape:

```bash
projects/example-project.json
```

The project profile carries product-specific inputs such as:

- brand voice and content rules
- SEO seed keywords, competitors, analytics config, and blog API URL
- slideshow voice, CTA variants, and screenshot references

Product repos can also add local context files, style overrides, curated topic briefs, screenshots, and publish adapters without changing the CMSBOT core.

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
- `seo/` reads `CMSBOT_PROJECT_FILE` when set, so the same tooling can be reused across projects without copying code.
