# CMSBOT — Project Status

## Overview

CMSBOT is a multi-agent content refinement pipeline built on the **Autoreason pattern**. It orchestrates multiple LLM providers (Anthropic, OpenAI, Google Gemini, and OpenAI-compatible endpoints like Qwen) through iterative draft-critique-rewrite loops to produce high-quality blog posts and social media content, output as fully styled, production-ready HTML with AI-generated images.

---

## Current Status: v0.1.0 — Core Pipeline Complete

### What's Working

| Feature | Status | Notes |
|---------|--------|-------|
| Autoreason loop | Done | Author → Strawman → Rewrite/Synthesize → Blind Judges → Convergence |
| Anthropic provider (Claude) | Done | Claude Sonnet 4.6 for authoring and rewriting |
| OpenAI provider (GPT + DALL-E) | Done | GPT-5.4 Mini for synthesis, DALL-E 3 for images |
| Gemini provider | Done | Gemini 3 Flash for critique, uses `google-genai` SDK |
| OpenAI-compatible provider | Done | Qwen 3.5 via OpenRouter |
| Budget tracking | Done | Per-call token + cost tracking, $75 default cap, utilization warnings |
| Blind judge panel | Done | 3 independent judges, Borda count ranking, shuffled candidates |
| Image generation | Done | AI art director agent plans images, DALL-E 3 generates them |
| Blog HTML template | Done | Responsive, dark mode, OG/Twitter cards, serif typography |
| Social HTML template | Done | Dark theme, platform card layout |
| CLI (`cmsbot`) | Done | `generate`, `check`, `models` commands via Click + Rich |
| Python API | Done | `AutoreasonPipeline` importable for programmatic use |
| Editable install | Done | `pip install -e .` into any project's venv |

### First Test Run (2026-03-31)

- Prompt: "Write a short blog post about why multi-agent AI systems produce better content than single-model approaches"
- Settings: 2 rounds, $5 budget
- Results: **$0.27 total cost** (5.3% of budget), 14 API calls, 3 DALL-E images, fully styled HTML output
- Round 1: B (rewrite) won, Round 2: A (previous winner) won — streak=1, did not converge in 2 rounds

### Architecture

```
src/cmsbot/
├── cli.py              # Click CLI with Rich output
├── config.py           # Env-based config, provider setup, role assignments
├── models.py           # Data models (Draft, Critique, Judgment, ImageSpec, etc.)
├── budget.py           # Token/cost tracking with per-model pricing table
├── pipeline.py         # Main Autoreason orchestrator loop
├── renderer.py         # Markdown-to-HTML conversion + Jinja2 rendering
├── providers/
│   ├── base.py         # Abstract LLMProvider interface
│   ├── anthropic.py    # Claude (AsyncAnthropic)
│   ├── openai_provider.py  # GPT + DALL-E + OpenAI-compatible (Qwen, etc.)
│   └── gemini.py       # Gemini (google-genai SDK)
├── agents/
│   ├── author.py       # Author A — writes initial draft
│   ├── strawman.py     # Adversarial critic — problems only, no fixes
│   ├── candidates.py   # B·rewrite and AB·synthesize generators
│   ├── judge.py        # Blind judge panel with Borda count
│   └── images.py       # Image director + DALL-E generation
└── templates/
    ├── blog.html       # Production blog template (responsive, dark mode, OG tags)
    └── social.html     # Social media content pack template
```

### Agent Role Assignments (Default)

| Role | Provider | Model | Rationale |
|------|----------|-------|-----------|
| Author A | OpenRouter | Qwen 3.5 (397B-A17B) | Cheap rough drafts, expensive models refine |
| Strawman | OpenRouter | Qwen 3.5 (397B-A17B) | Cheap adversarial critique |
| B·Rewrite | Anthropic | Claude Sonnet 4.6 | Quality rewrites incorporating critique |
| AB·Synthesize | OpenAI | GPT-5.4 Mini | Strong at merging multiple drafts |
| Judge 1 | Anthropic | Claude Opus 4.6 | Highest-quality judge, provider diversity reduces bias |
| Judge 2 | OpenAI | GPT-5.4 Mini | Provider diversity reduces bias |
| Judge 3 | Gemini | Gemini 3 Flash | Provider diversity reduces bias |
| Image Director | OpenAI | GPT-5.4 Mini | Plans image needs for the content |
| Image Generation | OpenAI | DALL-E 3 | Best image quality |

---

## Next Steps

### High Priority

- [ ] **Qwen integration testing** — OpenRouter key configured, test Qwen 3.5 (397B-A17B) for judge and strawman roles to reduce per-run costs
- [ ] **Role assignment CLI/config** — Add a `cmsbot config` command or YAML config file so role-to-provider mappings can be changed without editing code
- [ ] **Budget-aware model downgrading** — When `budget.should_downgrade()` returns True (>60% utilization), automatically swap expensive models for cheaper alternatives mid-pipeline
- [ ] **Error handling and retries** — Add retry with exponential backoff for transient API failures (rate limits, timeouts)
- [ ] **Unsplash/Pexels stock photo support** — Add fetching option alongside DALL-E generation for the image director's "fetch" method

### Medium Priority

- [ ] **SEO metadata agent** — Add a post-processing agent that generates keywords, structured data (JSON-LD), and optimized meta descriptions
- [ ] **Content type expansion** — Email newsletters, landing page copy, documentation
- [ ] **Batch mode** — Process multiple prompts from a file in sequence with shared budget tracking
- [ ] **Output format options** — Markdown output alongside HTML, optional JSON metadata sidecar
- [ ] **Template customization** — Allow users to provide their own Jinja2 templates for brand-specific styling
- [ ] **Convergence analytics** — Log and visualize how content evolves across rounds (diff view, quality trajectory)

### Low Priority / Future

- [ ] **Web API server** — FastAPI wrapper for the pipeline, enabling integration via HTTP endpoints
- [ ] **Webhook/callback support** — Notify external systems when content is ready (Slack, CMS, etc.)
- [ ] **Caching layer** — Cache intermediate results to allow resuming interrupted pipelines
- [ ] **A/B testing framework** — Compare different agent configurations to find optimal role assignments
- [ ] **Plugin system** — Allow custom agents to be registered for domain-specific content needs
- [ ] **Publish to PyPI** — Package and distribute as `pip install cmsbot` from the public index

---

## Cost Estimates

Based on the test run and current model pricing:

| Scenario | Estimated Cost | Notes |
|----------|---------------|-------|
| Short blog (2 rounds, 3 images) | ~$0.25–$0.50 | Well under budget |
| Long-form blog (5 rounds, 5 images) | ~$1.00–$3.00 | Converges around round 3–4 |
| Social media pack (2 rounds, 1 image) | ~$0.15–$0.30 | Shorter content = fewer tokens |
| With Qwen judges/strawman | ~30–50% less | Qwen 3.5 via OpenRouter significantly cheaper |

The $75 budget cap is a safety net — typical content pieces cost well under $5.

---

## Integration Guide

To use CMSBOT in another project:

```bash
# 1. Activate your project's virtual environment
source .venv/bin/activate

# 2. Install CMSBOT
pip install -e /path/to/CMSBOT

# 3. Add API keys to your project's .env
# Anthropic_API_Key=sk-ant-...
# OpenAI_API_Key=sk-proj-...
# GEMINI_API_Key=AIza...
# OPENAI_COMPAT_API_KEY=sk-or-...       # OpenRouter key for Qwen
# OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1

# 4. Generate content
cmsbot generate "Your content prompt here"
```

Or use the Python API:

```python
import asyncio
from cmsbot import AutoreasonPipeline, Config

config = Config.from_env()
pipeline = AutoreasonPipeline(config)

result = asyncio.run(pipeline.run(
    task_prompt="Write a blog about...",
    content_type="blog",
))

print(result.html)           # Full HTML output
print(result.total_cost)     # Cost in USD
print(result.converged)      # Whether the loop converged
```
