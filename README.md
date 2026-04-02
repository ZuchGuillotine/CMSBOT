# CMSBOT

CMSBOT is a reusable content and marketing toolkit centered on a multi-agent content generator, with adjacent tooling for project profiling, SEO research, topic generation, and slideshow repurposing.

## What’s in the repo

- `cmsbot generate`
  Multi-agent long-form and social content generation using the Autoreason pipeline.
- `cmsbot project ...`
  Project profile scaffolding, context generation, and tone/personality synthesis.
- `cmsbot topics`
  Turns SEO evaluator output into ready-to-run CMSBOT prompts.
- [`seo/`](./seo/README.md)
  Keyword research plus Search Console / GA4 evaluation.
- [`slideshow/`](./slideshow/README.md)
  Blog-to-slideshow spec generation, image generation, post-processing, and review output.

The repo is designed to be pointed at different projects. Project-specific screenshots, local prompts, and brand assets should stay local and out of git.

## Install

Requires Python 3.10+ for the core package.

```bash
git clone https://github.com/ZuchGuillotine/CMSBOT.git
cd CMSBOT
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you want the SEO toolkit, install Node dependencies separately:

```bash
cd seo
npm install
cd ..
```

## Quick start

1. Create a project profile.

```bash
cmsbot project init "Northstar CRM"
```

2. Render a reusable context file from it.

```bash
cmsbot project context projects/northstar-crm.json --output contexts/northstar-crm.md
```

3. Optionally generate tone/personality flags with an LLM.

```bash
cmsbot project voice projects/northstar-crm.json --output contexts/northstar-crm.voice.json
```

4. Generate content with the project profile directly.

```bash
cmsbot generate "Write a blog post about lead routing mistakes" \
  --type blog \
  --project-file projects/northstar-crm.json \
  --style persuasive
```

## SEO workflow

```bash
export CMSBOT_PROJECT_FILE=projects/northstar-crm.json
cd seo
npm run research
npm run evaluate
cd ..
```

Then turn evaluator output into prompts:

```bash
cmsbot topics --project-file projects/northstar-crm.json
cmsbot topics --command 0 --project-file projects/northstar-crm.json
```

## Slideshow workflow

```bash
cd slideshow
python blog_to_slideshow.py --blog-slug lead-routing-mistakes --project-file ../projects/northstar-crm.json
./run_pipeline.sh --project-file ../projects/northstar-crm.json --batch
```

Place screenshots in `slideshow/screenshots/` when a spec requests `app_screenshot`.

## Configuration

Copy `.env.example` to `.env` and add the providers you want to use:

```env
Anthropic_API_Key=
OpenAI_API_Key=
GEMINI_API_Key=
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_BASE_URL=https://api.together.xyz/v1
```

## Repo layout

```text
src/cmsbot/         Python package and CLI
projects/           Example project profiles
contexts/           User-generated markdown contexts
seo/                Node-based SEO research and evaluation toolkit
slideshow/          Short-form content repurposing pipeline
```

## Notes

- `projects/example-project.json` is the reference schema for project profiles.
- Keep project-specific assets local: screenshots, proprietary prompts, unpublished topic lists, and credential files.
- The existing publishing bridge in this workspace is project-specific and should stay outside the reusable core unless you are wiring a specific CMS integration.
