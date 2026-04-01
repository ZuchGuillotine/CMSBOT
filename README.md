# CMSBOT

Multi-agent content refinement pipeline using the **Autoreason pattern** — iterative debate between AI agents that argue over drafts until convergence, rather than optimizing a numeric score.

## How it works

```
Prompt
  -> Author (Claude) writes initial draft
  -> Strawman (Gemini) attacks it — problems only, no fixes
  -> 3 candidates generated:
       A = keep original
       B = full rewrite (Claude) informed by critique
       AB = synthesize best of A and B (GPT-4o)
  -> Blind judge panel: 3 judges from different providers, shuffled candidates, Borda count
  -> Winner becomes new draft; streak++ if A wins, else reset
  -> Loop until streak=2 (convergence) or budget/rounds exhausted
  -> Image Director plans images -> DALL-E 3 generates them
  -> Renderer outputs styled HTML
```

Each round pits the incumbent draft against two challengers. When the original survives two consecutive rounds of blind judging, it's considered converged — no agent can improve it further.

## Installation

Requires Python 3.10+.

```bash
# Clone and install
git clone https://github.com/ZuchGuillotine/CMSBOT.git
cd CMSBOT
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and add your API keys:

```bash
cp .env.example .env
```

```env
# Required — all three providers are used in the pipeline
Anthropic_API_Key=sk-ant-...
OpenAI_API_Key=sk-proj-...
GEMINI_API_Key=AIza...

# Optional — OpenAI-compatible provider for Qwen/other models
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_BASE_URL=https://api.together.xyz/v1
OPENAI_COMPAT_MODEL=Qwen/Qwen3-235B-A22B
```

Verify your providers are configured:

```bash
cmsbot check
```

## Usage

### CLI

```bash
# Generate a blog post
cmsbot generate "Write a blog post about sustainable architecture" --type blog --budget 5.00 --max-rounds 3

# Generate social media content
cmsbot generate "Launch announcement for our new API" --type social --budget 2.00

# Show model pricing
cmsbot models
```

Output is saved to `./output/<slug>/index.html` with generated images.

### Python library

```python
import asyncio
from cmsbot import AutoreasonPipeline, Config

config = Config.from_env()
config.budget_cap = 5.00
config.max_rounds = 3

pipeline = AutoreasonPipeline(config)
result = asyncio.run(pipeline.run(
    task_prompt="Write a blog post about sustainable architecture",
    content_type="blog",
))

# result.html          -> rendered HTML string
# result.final_draft   -> winning Draft object
# result.total_cost    -> actual USD spend
# result.converged     -> whether streak=2 was reached
# result.images        -> list of ImageSpec with file paths
# result.rounds        -> per-round details (winner, cost, streak)
```

## Budget control

Every API call is tracked with per-model token pricing. The pipeline checks affordability before each step and stops gracefully when the budget is exhausted.

```bash
# Set a $5 cap
cmsbot generate "..." --budget 5.00

# Limit to 2 refinement rounds
cmsbot generate "..." --max-rounds 2
```

The `BudgetTracker` automatically signals when utilization exceeds 60%, allowing the pipeline to downgrade to cheaper models.

## Project structure

```
src/cmsbot/
  __init__.py          # Exports AutoreasonPipeline, Config
  cli.py               # Click CLI (generate, check, models)
  pipeline.py          # Autoreason orchestration loop
  config.py            # Environment/provider configuration
  budget.py            # Token cost tracking and budget enforcement
  models.py            # Data models (Draft, Critique, PipelineResult, etc.)
  renderer.py          # Jinja2 HTML rendering
  templates/           # HTML templates (blog.html, social.html)
  agents/
    author.py          # Initial draft writer
    strawman.py        # Adversarial critic
    candidates.py      # Rewriter + Synthesizer agents
    judge.py           # Blind judge panel with Borda count
    images.py          # Image planning + DALL-E generation
  providers/
    __init__.py        # build_provider() factory
    base.py            # LLMProvider abstract base
    anthropic.py       # Claude provider
    openai_provider.py # GPT + OpenAI-compatible provider
    gemini.py          # Gemini provider
```

## License

MIT
