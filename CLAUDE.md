# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (requires venv — Homebrew Python is externally managed)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Run CLI
cmsbot generate "Your prompt" --type blog --budget 5.00 --max-rounds 3
cmsbot generate "Your prompt" --context-file contexts/my-brand.md --style casual
cmsbot check     # show configured providers
cmsbot models    # show model pricing table
cmsbot styles    # show available writing styles
cmsbot goal init # create goals/ and strategy/ scaffolding
cmsbot goal status
cmsbot goal prepare weekly-content-calendar --project-file projects/example-project.json
cmsbot goal summarize
cmsbot review de-ai output/asset-sets/YYYY/YYYY-MM/asset-set-id --platform instagram --format carousel
cmsbot review gates output/asset-sets/YYYY/YYYY-MM/asset-set-id
cmsbot analytics import-social-csv --file exports/instagram.csv --platform instagram --format carousel
cmsbot routing decide source_draft --trigger regulated_claims

# Run as module
python -m cmsbot generate "Your prompt"

# Lint (ruff is a dev dependency)
pip install -e ".[dev]"
ruff check src/

# Tests (none yet — pytest + pytest-asyncio are dev deps)
pytest
```

## Architecture

CMSBOT implements the **Autoreason pattern**: iterative multi-agent content refinement where agents argue over drafts until convergence, rather than optimizing a numeric score.

### Pipeline flow (pipeline.py)

```
task_prompt
  → Author A (Claude) writes draft
  → Strawman (Gemini) attacks it — problems only, no fixes
  → 3 candidates: A·keep | B·rewrite (Claude) | AB·synthesize (GPT-4o)
  → Blind judge panel: 3 judges from different providers, shuffled candidates, Borda count
  → Winner becomes new A; streak++ if A wins, else streak=0
  → Loop until streak=2 or budget/rounds exhausted
  → Image Director plans images → multiple image providers generate candidates → ImageJudge picks the winner
  → Renderer outputs styled HTML
```

### Provider system

All providers implement `LLMProvider.generate()` → `LLMResponse` (normalized content + token counts + cost). The `build_provider(name, config)` factory in `providers/__init__.py` maps string names to instances. Four providers: `anthropic`, `openai`, `gemini`, `openai_compat` (for Qwen via Together AI/OpenRouter).

### Role assignment

`Config.roles` (a `RoleAssignment` dataclass) maps pipeline roles to provider names. The pipeline calls `build_provider(roles.author, config)` etc. to wire agents to providers. Roles: `author`, `strawman`, `rewriter`, `synthesizer`, `judges` (list of 3), `image_director`.

### Budget system

`BudgetTracker` in budget.py accumulates cost per API call using a `MODEL_COSTS` pricing table. The pipeline checks `can_afford()` before each step and `should_downgrade()` (>60% utilization) to signal when cheaper models should be used. Generated image costs accumulate in `image_cost`, while the image judge is tracked like any other LLM call.

### Judge blind evaluation

`JudgePanel.evaluate()` shuffles the 3 candidates into random order, presents them as "Candidate 1/2/3" with no origin labels, runs 3 judges in parallel via `asyncio.gather()`, then uses Borda count (1st=2pts, 2nd=1pt, 3rd=0pts) to pick the winner. `_parse_ranking()` has multi-level fallback for extracting JSON arrays from LLM output.

### Context and style system

`context.py` handles loading user context files and style presets, then assembles them into an enriched `task_prompt` that all agents see. Context files live in `contexts/` (user-created, not shipped). Style presets live in `src/cmsbot/styles/` (shipped with package). Local `./styles/` directory can override built-in styles. The enriched prompt format is: `CONTEXT AND GUIDELINES:` + `WRITING STYLE:` + `TASK:`.

## Key conventions

- All LLM calls are async. The CLI bridges with `asyncio.run()`.
- Agents return `tuple[DataModel, LLMResponse]` — the domain object plus the raw response for budget tracking.
- Content is written as markdown-style prose by agents, then converted to HTML by `renderer.py` at the end.
- Templates are Jinja2 HTML in `src/cmsbot/templates/`, bundled via `tool.setuptools.package-data`.
- Env var names are mixed case for backwards compat (`Anthropic_API_Key`, `OpenAI_API_Key`, `GEMINI_API_Key`) — config.py accepts both forms, plus `XAI_API_KEY` and `UNSPLASH_ACCESS_KEY` for optional image providers.
- The `openai_provider.py` file contains both `OpenAIProvider` (standard) and `OpenAICompatProvider` (custom base_url for Qwen etc.).

## Claude workflow state

- Treat `goals/*.goal.md`, `strategy/*.json`, and `strategy/strategy-memory.md` as canonical long-running workflow memory.
- `cmsbot goal prepare <goal>` writes `output/runs/<run-id>/handoff.md` plus `run.json`; use it as the starting point for scheduled Claude work.
- `cmsbot goal status` shows active goals and recent resumable runs from `output/runs/`.
- Create a PR only when a scheduled goal produces a non-empty validated diff to tracked planning, strategy, rubric, routing, or adapter-contract files.
- Do not create PRs for routine asset output, single analytics imports, single de-AI reports, or external publishing.
- `cmsbot review de-ai` writes `distribution/<platform>/de_ai_report.json`, `distribution/<platform>/review.md`, and an `output/runs/<run-id>/run.json` record.
- `cmsbot review gates` shows `draft_ready`, `de_ai_passed`, `human_approved`, and `publish_ready`; publishing requires all gates to pass.
- Publishing remains product-repo-specific and human-gated. Core should prepare artifacts and review reports, not post externally.
