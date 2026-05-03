# Next-Generation CMSBOT Architecture Audit

## Executive Summary

CMSBOT should stop trying to be a full custom multi-agent platform. The right next architecture is a content operating system with durable project state, content artifacts, analytics, and governance, while Codex, Claude Code, and modern LLM tools do the heavy cognitive work through resumable tasks, repo-aware execution, scheduled runs, memory, skills, and pull requests.

The current codebase already has useful primitives:

- Autoreason drafting in `src/cmsbot/pipeline.py`
- project profiles in `src/cmsbot/project.py`
- calendar and asset-set layout in `src/cmsbot/content_ops.py`
- derivative exports in `src/cmsbot/social_threads.py` and `src/cmsbot/social_derivatives.py`
- analytics indexing in `src/cmsbot/analytics.py`
- image planning, multi-provider generation, image judging, SVG diagrams, and post-processing in `src/cmsbot/agents/images.py`, `src/cmsbot/agents/image_judge.py`, and `src/cmsbot/postprocess.py`
- SEO evaluation in `seo/`
- slideshow and performance scripts in `slideshow/`

The redesign should preserve those domain-specific pieces, but reduce custom LLM orchestration. Modern Codex can read, edit, and run code in cloud tasks, work in parallel, and create PRs from GitHub-backed environments. ChatGPT Projects and Tasks now provide durable workspaces, scheduled prompts, memory, and recurring execution. Claude Code provides GitHub automation, slash-command workflows, skills, and subagents. These are now better primitives than a homegrown agent swarm for many workflow and coding tasks.

Sources used for capability assumptions:

- OpenAI Codex cloud docs: https://developers.openai.com/codex/cloud
- OpenAI Codex product page: https://openai.com/codex/
- OpenAI ChatGPT Tasks help: https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- OpenAI ChatGPT Projects help: https://help.openai.com/en/articles/10169521-projects-in-chatgpt
- OpenAI Memory FAQ: https://help.openai.com/en/articles/8590148-memory-in-chatgpt
- Anthropic Claude Code GitHub Actions: https://code.claude.com/docs/en/github-actions
- Anthropic Claude Code skills/slash commands: https://code.claude.com/docs/en/slash-commands
- Anthropic Claude Code subagents: https://code.claude.com/docs/en/sub-agents

## 1. Capability Mapping

| CMSBOT function | Offload to Codex / modern LLM tools | Keep custom-built | Hybrid decision and why |
|---|---|---|---|
| Project onboarding | Let Codex/Claude review a product repo, inspect docs, propose `project.json`, contexts, styles, screenshots, and adapters. | Keep `ProjectProfile`, JSON schema, context rendering, validation. | Hybrid. Project discovery is exploratory and repo-specific, which modern agents do well. The schema must stay deterministic so outputs are reusable across projects. |
| Content calendar planning | Use scheduled Codex/ChatGPT tasks to review analytics, SEO output, and project state, then propose calendar diffs. | Keep `content-calendar.json`, `asset_set_id`, `slot_id`, cadence schema, deterministic file layout. | Hybrid. The planner should become a stateful assistant, but the canonical calendar must be a reviewable file, not hidden chat memory. |
| Topic and keyword research | Offload exploratory research, competitor scans, and topic clustering to Codex/ChatGPT/Claude tasks with browser/API access where allowed. | Keep SEO ingestion, topic brief schema, scoring fields, source attribution, dedupe. | Hybrid. Discovery changes constantly; deterministic scoring and persisted briefs keep the system auditable. |
| Blog drafting | Offload much of ideation, outline, draft, and rewrite work to external agent tasks or model APIs. | Keep content artifact schemas, prompt assembly, budget tracking, renderer, image hooks. | Hybrid. CMSBOT should not own every debate between agents. It should own the source artifact and enforce constraints. |
| Autoreason loop | Stop expanding it as the central orchestration model. Use it as one optional drafting mode. | Keep existing implementation for deterministic batch runs and offline generation. | Hybrid. Autoreason is valuable for repeatable local generation, but modern agents now handle multi-step critique and revision better across longer sessions. |
| Threads/carousels/video scripts | Let agent coworkers create variants, inspect platform norms, and propose edits. | Keep structured export schemas and distribution folders. | Hybrid. Platform taste is model-sensitive and trend-sensitive; artifact shape must remain machine-readable. |
| De-AI optimization | Offload qualitative review, examples, and rewrite passes to premium models, Codex/Claude review tasks, or human-guided sessions. | Keep rubrics, platform tolerance profiles, reviewer outputs, before/after provenance. | Hybrid. Detection and taste are fuzzy; pass/fail gates and reviewer records must be durable. |
| Image generation and judging | Use current and future image/video model APIs through providers; let agents create briefs and inspect failures. | Keep provider abstraction, generated/processed/review folders, post-processing, model cost accounting, image judge schema. | Hybrid. Visual quality changes rapidly, but CMSBOT’s proprietary value is prompt packaging, selection records, and downstream asset organization. |
| Publishing | Offload adapter implementation and troubleshooting to Codex/Claude. | Keep publishing disabled by default in core; product repos own adapters and approvals. | Mostly custom boundary. Publishing has credentials, compliance, and brand risk. Agents can implement adapters, but should not publish without gates. |
| Analytics ingestion | Let agents build or maintain importers for platform exports and APIs. | Keep normalized `performance-index.json`, per-asset `analytics/performance.json`, scoring functions, lineage. | Hybrid. API details change; normalized records and strategy feedback are CMSBOT’s durable layer. |
| Prompt evolution | Let Codex/Claude propose prompt and rubric changes from analytics and failure logs. | Keep prompt/rubric files versioned and reviewed in PRs. | Hybrid. Agents can suggest changes; merge control and evaluation fixtures stay in Git. |
| Workflow scheduling | Use ChatGPT Tasks, Codex Automations, GitHub Actions, cron, or Claude Code Actions. | Keep idempotent CLI commands and state files so any scheduler can call them. | Mostly offload. Do not build a custom scheduler unless deployment constraints demand it. |
| Long-running implementation | Offload to Codex cloud tasks, Claude Code Actions, or PR-driven agent workflows. | Keep `AGENTS.md`, `CLAUDE.md`, tests, fixtures, docs, and acceptance criteria. | Mostly offload. Modern code agents are now better at repo changes than a CMSBOT-specific coding worker would be. |
| Memory/context retention | Use ChatGPT Projects, saved memory, Claude memory files, and repo docs for agent context. | Keep canonical memory in project pack files and generated strategy files. | Hybrid. Chat memory is useful but not enough for production. CMSBOT needs file-backed memory that can be diffed and versioned. |

## 2. Architecture Redesign

### Target architecture

```text
Human / Codex / Claude
        |
        v
Control Layer
  - goals
  - schedules
  - workflow state
  - approvals
  - budget policy
        |
        v
CMSBOT Core
  - project profile schema
  - calendar schema
  - asset-set filesystem
  - artifact schemas
  - provider registry
  - budget tracker
  - analytics index
        |
        v
Execution Layer
  - model API calls
  - Codex tasks
  - Claude Code Actions
  - SEO scripts
  - image/video providers
  - product adapters
        |
        v
Data Layer
  - project pack
  - content-calendar.json
  - asset-sets/*
  - performance-index.json
  - strategy-memory.md
  - prompt/rubric files
```

### Control layer

The control layer should become a thin state machine, not a custom agent runtime.

Keep in CMSBOT:

- `goals/` directory with durable goal specs
- `workflow-runs/` directory with state, logs, failures, cost summaries, and handoff notes
- `content-calendar.json`
- `performance-index.json`
- explicit approval states: `planned`, `drafted`, `review_ready`, `approved`, `published`, `measured`, `needs_revision`

Offload to Codex/Claude:

- long-running implementation tasks
- repo migration work
- adapter creation
- exploratory audit and diagnosis
- PR production and review
- failure investigation

### LLM execution layer

CMSBOT should expose a small set of commandable jobs:

- `cmsbot calendar plan`
- `cmsbot batch run --id <entry-id>`
- `cmsbot export-thread`
- `cmsbot export-carousel`
- `cmsbot export-video-script`
- `cmsbot analytics import-*`
- `cmsbot review de-ai`
- `cmsbot goal status`

Codex and Claude should call these commands from tasks rather than CMSBOT embedding a complex agent planner internally.

### Data layer

The durable state should live in files:

```text
marketing/cmsbot/
  project.json
  contexts/
  styles/
  goals/
    q2-content-engine.goal.md
    weekly-analytics.goal.md
  strategy/
    strategy-memory.md
    platform-profiles.json
    de-ai-rubrics.json
    model-routing.json
  output/
    calendar/content-calendar.json
    calendar/performance-index.json
    asset-sets/...
```

This gives modern agents a stable substrate. Chat memory can help, but the repo files are the source of truth.

### Human checkpoints

Human approval should be required for:

- publishing
- budget increases
- new provider credentials
- claim-sensitive content
- prompt/rubric changes that affect brand voice or compliance
- automated deletion or overwrite of content
- calendar changes that alter strategic pillars or cadence

Everything else can be autonomous within a budget and a bounded goal.

## 3. Workflow Redesign

### A. Content Creation Loop

1. Strategy scan
   - Scheduled agent reads `performance-index.json`, SEO briefs, recent asset manifests, and `strategy-memory.md`.
   - Agent proposes calendar updates or opens a PR.

2. Topic selection
   - CMSBOT deterministic planner selects candidate topics from `content_ops`, SEO briefs, and analytics signals.
   - Agent may enrich the topic with competitive context and examples.

3. Source draft
   - CMSBOT can run current Autoreason for local batch generation.
   - For high-value content, Codex/Claude should run a goal task: inspect project context, draft source content, run CMSBOT export commands, and submit artifacts in a PR or output folder.

4. Refinement
   - Use a rubric-driven review: factuality, brand voice, platform fit, de-AI score, claims risk, CTA clarity.
   - Premium model or human review is triggered for high-risk content.

5. Platform adaptation
   - CMSBOT writes `distribution/x`, `distribution/instagram`, `distribution/tiktok`, etc.
   - Agents can propose stronger variants but must preserve artifact schemas.

6. Approval
   - Human reviews `review.md`, artifacts, and de-AI report.
   - Status moves to `approved` only after explicit confirmation.

### B. Distribution + Analytics Loop

1. Publishing
   - Product repo adapters handle CMS, scheduler, Google Drive, social platform, or manual handoff.
   - Core emits portable artifacts only.

2. Performance tracking
   - Automated imports pull SEO, GA4, GSC, social exports, and platform API data into normalized records.
   - Records are keyed by `asset_set_id`, `slot_id`, platform, format, pillar, topic, and hook.

3. Signal extraction
   - Deterministic scoring calculates baseline performance.
   - Agent task summarizes what changed, what worked, what failed, and which experiments to run next.

4. Feedback
   - Update `strategy-memory.md`, calendar priorities, platform profiles, and prompt/rubric candidates.
   - Prompt changes happen through PRs, not silent mutation.

### C. Continuous Optimization Loop

1. Weekly agent reads the previous 7 to 30 days.
2. Agent proposes:
   - pillar weight changes
   - format mix changes
   - hook patterns to reuse
   - content gaps
   - underperforming topics to pause
3. CMSBOT validates the proposal against budget and cadence.
4. Human approves strategic changes above thresholds.
5. Approved changes are written into `project.json`, `strategy-memory.md`, or calendar files.

## 4. Codex Integration Strategy

### Use Codex for durable goals

Codex should be treated as an engineering and operations coworker. Its job is to complete bounded goals against the repo, run tests, and propose changes. CMSBOT should store goal specs in the repo so any Codex, Claude, or human session can resume the work.

Example goal files:

```text
goals/weekly-content-calendar.goal.md
goals/analytics-ingestion.goal.md
goals/platform-profile-refresh.goal.md
goals/de-ai-rubric-evaluation.goal.md
goals/image-provider-upgrade.goal.md
```

Example recurring Codex goals:

- "Every Monday, inspect analytics, SEO output, and current calendar; propose next 14 days of content as a PR."
- "Every weekday, generate due asset sets that are still in `planned` status and prepare them for review."
- "Every Friday, summarize de-AI review failures and propose rubric/prompt changes."
- "When a GitHub issue is labeled `cmsbot-adapter`, implement or repair the product repo adapter and open a draft PR."

### Use scheduled execution

Use ChatGPT Tasks, Codex Automations, GitHub Actions, or cron for scheduling. CMSBOT should not own scheduling logic beyond idempotent commands and state transitions.

Autonomous:

- analytics import
- calendar draft generation
- content draft preparation
- artifact validation
- report generation
- PR creation for proposed changes

Human-gated:

- publishing
- strategic pillar changes
- budget increases
- model/provider changes
- compliance-sensitive claims

### Use memory carefully

Use ChatGPT Projects and Claude/Codex memory to preserve working context, but treat repo files as production memory. Chat memory is helpful for continuity; it is not a database.

Canonical memory files:

- `strategy/strategy-memory.md`
- `strategy/platform-profiles.json`
- `strategy/de-ai-rubrics.json`
- `strategy/model-routing.json`
- `output/calendar/performance-index.json`

## 5. Multi-Model Strategy

### Routing tiers

| Work type | Recommended routing |
|---|---|
| Calendar draft, topic expansion, low-risk social variants | cheap/fast model |
| Blog source draft | mid-tier model with project context |
| Final rewrite, de-AI polish, compliance-sensitive content | premium model |
| Cross-model critique | different provider from drafter |
| Image prompt planning | vision/image-aware premium or strong multimodal model |
| Analytics summarization | cheap model for summaries, premium model only for strategy pivots |
| Code changes | Codex or Claude Code tasks, model chosen by task complexity |

### Escalation rules

Escalate when:

- the content makes medical, legal, financial, or regulated claims
- de-AI score fails twice
- the same provider wrote and reviewed the content
- expected distribution reach is high
- paid media spend is attached
- output will be published without human rewrite

### Anti-monotony

Use:

- model rotation by role
- provider-diverse critique
- platform-specific style profiles
- example banks of human-approved posts
- forced angle diversity in calendar entries
- periodic "voice drift" reviews by a different model

Do not solve monotony by adding more agents. Solve it by giving fewer agents better examples, stricter schemas, and cross-provider review.

## 6. De-AI System Design

### What to build

Add `cmsbot review de-ai` with structured output:

```json
{
  "asset_set_id": "...",
  "platform": "instagram",
  "format": "carousel",
  "score": 72,
  "risk_level": "medium",
  "flags": ["generic hook", "over-explained transition"],
  "rewrite_recommendations": [],
  "requires_human_review": true
}
```

### Review dimensions

- generic phrasing
- inflated claims
- repetitive sentence structure
- overly symmetrical formatting
- platform mismatch
- CTA awkwardness
- jargon density
- lack of concrete examples
- human-edit likelihood
- visual prompt artifact risk

### Platform tolerance profiles

Store in `strategy/platform-profiles.json`:

- blog: lower tolerance for unsupported claims, higher tolerance for structured exposition
- X/Threads: lower tolerance for corporate tone, higher tolerance for sharp first-person observations
- Instagram carousel: lower tolerance for dense copy, higher tolerance for punchy labels
- TikTok/Reels: lower tolerance for polished essay language, higher need for spoken rhythm
- LinkedIn: lower tolerance for gimmicks, higher tolerance for operational examples

### Human editing interface

Do not build a heavy UI first. Use review artifacts:

- `distribution/<platform>/review.md`
- `distribution/<platform>/de_ai_report.json`
- `distribution/<platform>/approved.md`

Later, build a lightweight local review page if manual throughput becomes the bottleneck.

## 7. Automation + Cost Governance

### Automatic runs

- daily analytics import
- daily calendar freshness check
- due asset generation
- weekly strategy report
- prompt/rubric change proposal
- dependency and provider health checks

### Rate limits

Set caps by project:

- max daily LLM spend
- max weekly image spend
- max asset sets generated per day
- max premium model calls per asset set
- max rewrite loops per artifact
- max autonomous PRs per week

### Kill switches

Implement:

- `CMSBOT_AUTONOMY=off|draft|review|publish`
- `CMSBOT_MAX_DAILY_SPEND`
- `CMSBOT_MAX_ASSET_SETS_PER_RUN`
- `CMSBOT_REQUIRE_APPROVAL_FOR_PUBLISH=true`
- `CMSBOT_DISABLE_IMAGE_GENERATION=true`
- `CMSBOT_DISABLE_EXTERNAL_PUBLISH=true`

### Failure handling

Every long-running workflow should write:

- run id
- started/finished timestamp
- commands run
- cost summary
- files changed
- failure reason
- resume instruction
- next safe action

This is how Codex/Claude can resume cleanly across sessions.

## 8. Build vs Buy Analysis

### Stop building

- A custom general-purpose agent swarm.
- A custom scheduler if ChatGPT Tasks, Codex Automations, GitHub Actions, or cron can run the job.
- A custom memory system beyond file-backed strategy state.
- A custom PR/code implementation worker.
- A custom browser publishing bot in core.
- Increasingly complex hardcoded multi-agent debate flows for every content type.
- Hidden chat-only strategy state that cannot be diffed.

### Keep building

- Project pack schema.
- Content calendar schema.
- Asset-set layout.
- Analytics normalization and lineage.
- Provider abstraction and cost accounting.
- De-AI rubrics and platform profiles.
- Image/video provider packaging, judging, and post-processing.
- Product repo adapter contracts.
- Validation tools and fixtures.
- Governance policy and approval gates.

The proprietary value is not "CMSBOT has agents." The proprietary value is durable content operations state, brand-aware artifacts, analytics feedback, provider routing, and production-safe handoffs.

## 9. Implementation Roadmap

### Phase 1: Quick wins

Complexity: Low to medium

1. Add repo-backed goal specs.
   - Add `goals/*.goal.md` templates.
   - Include objective, allowed commands, budget, acceptance criteria, resume instructions.

2. Add strategy memory files.
   - `strategy/strategy-memory.md`
   - `strategy/platform-profiles.json`
   - `strategy/de-ai-rubrics.json`
   - `strategy/model-routing.json`

3. Add run state.
   - `output/runs/<run-id>/run.json`
   - Record commands, costs, artifacts, failures, and next action.

4. Add `cmsbot review de-ai`.
   - Start with rubric-based LLM review plus deterministic schema validation.
   - Emit `de_ai_report.json` and `review.md`.

5. Add Codex/Claude handoff docs.
   - `AGENTS.md` and `CLAUDE.md` should describe content-ops commands, tests, output layout, and approval gates.

### Phase 2: Structural improvements

Complexity: Medium

1. Convert Autoreason into one execution strategy.
   - Keep `AutoreasonPipeline`.
   - Add `execution_mode`: `autoreason`, `single_model`, `agent_assisted`, `human_seeded`.

2. Add goal runner commands.
   - `cmsbot goal status`
   - `cmsbot goal prepare`
   - `cmsbot goal summarize`
   - Avoid a custom autonomous planner; produce files that Codex/Claude can execute.

3. Improve analytics ingestion.
   - Add importers for social CSV exports.
   - Add per-platform metric mapping.
   - Add confidence and freshness fields.

4. Add model-routing config.
   - Support role-based routing and escalation rules from `strategy/model-routing.json`.

5. Add review gates.
   - `draft_ready`
   - `de_ai_passed`
   - `human_approved`
   - `publish_ready`

### Phase 3: Advanced automation

Complexity: Medium to high

1. Agent-assisted weekly strategy loop.
   - Scheduled Codex/Claude reads analytics and creates a PR with calendar and strategy changes.

2. Adapter automation.
   - Product repos define publish adapters.
   - Agents can repair adapters and prepare publish bundles.
   - Publishing still requires explicit approval.

3. Visual model upgrade path.
   - Add provider capability metadata.
   - Agents can propose new provider configs when image/video models improve.
   - Human approves provider and budget changes.

4. Evaluation harness.
   - Fixture project packs.
   - Golden output shape tests.
   - De-AI regression tests.
   - Cost regression tests.

5. Multi-agent coworker mode.
   - Use Codex for code and repo changes.
   - Use Claude Code subagents for focused review/research tasks when useful.
   - Use CMSBOT only as deterministic state, command, and artifact layer.

## Final Architecture Decision

CMSBOT should become a stateful content operations substrate, not a general agent platform. Its core responsibility is to define and protect the project contract, calendar, artifacts, analytics, cost policy, approval gates, and provider integrations.

Codex, Claude Code, and modern LLM tools should be used as coworkers that execute bounded goals against that substrate. This reduces custom orchestration work while increasing adaptability as model capabilities improve.

The immediate next engineering move is to add repo-backed goals, run state, and de-AI review artifacts. That gives modern agents the structure they need to work safely across long-running content operations without forcing CMSBOT to reinvent the agent runtime.
