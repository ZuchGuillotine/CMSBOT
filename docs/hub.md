# CMSBOT Hub

One small service that holds content state for every project and is the single place where people
review, agents hand work over, and results come back.

```
 drafting agents (Claude, Codex)          people                 posting agents (Grok Bot, ...)
   get_brief, save_draft,          review page: edit inline,       ready_to_post, claim_post,
   set_claims, submit_for_review   verify claims, approve          mark_posted, report_metrics,
            │                              │                        submit_signal
            └──────────────┬───────────────┴───────────────┬────────────────┘
                           ▼                               ▼
                     CMSBOT Hub  (one SQLite file: projects, pillars, clusters, items, revisions,
                                  claims, posts, metrics, conversions, costs, signals, learnings)
                           ▲
            analysis agents: report_conversions, performance, propose_learning
```

## Rules it enforces

- Agents never approve. Only a reviewer token can approve, edit after submission, or verify claims.
- Approval is tied to the exact copy. Any later edit withdraws it. The reviewer can fix a word and
  approve in one step, so small corrections don't go back to an agent.
- A posting agent sees only approved copy that is due, takes it with a claim so nothing is posted
  twice, and gets the link with tracking tags already added (`utm_content=cmsbot-<id>`).
- Gaps (facts the agent could not confirm) and, where the project requires it, unverified claims
  block approval.
- A change to the method (a "learning") reaches drafting agents only after a person accepts it.

## Run it

```bash
pip install -e ".[hub]"
cmsbot hub init --reviewer "your name"
```

```bash
cmsbot hub serve
```

Open http://127.0.0.1:8787 and paste the reviewer token. Then, in another shell:

```bash
export CMSBOT_HUB_TOKEN=<reviewer token>
cmsbot hub project projects/example-project.json --budget 100
```

```bash
cmsbot hub token create claude-weekly --role generator
```

```bash
cmsbot hub token create grok-bot --role distributor
```

```bash
cmsbot hub token create analyst --role analyst
```

Send existing pipeline output for review (safe to re-run):

```bash
cmsbot hub push output/asset-sets/2026/2026-11/<asset-set-id> --project <slug> --link https://site/blog/<slug>
```

## Host it

It is one process and one file, shared by all projects, so it needs the smallest instance a host
sells plus a persistent disk for `/data`. That is typically a few dollars a month in total, not per
project; check your host's current prices. Nothing else is required: no managed database, no queue.

```bash
docker build -t cmsbot-hub .
```

```bash
docker run -p 8787:8787 -v cmsbot-hub-data:/data cmsbot-hub
```

Serve it over HTTPS (the host's built-in TLS is enough). Back up `/data/hub.sqlite3`.
It can run on a laptop while you evaluate it; cloud agents such as Grok Bot can only reach it once
it has a public HTTPS address.

## Media files

The Hub's database holds text and links. Image, video and audio files go to a storage backend:

| Backend | When | Setup |
|---|---|---|
| Hub disk (default) | Evaluating, or small volumes | Nothing. Files sit beside the database and are served at `/media/...` |
| S3-compatible bucket | Real use, video | Set the variables below on the Hub. Works with Cloudflare R2, AWS S3, Backblaze B2 |

```bash
CMSBOT_MEDIA_BUCKET=cmsbot-media
CMSBOT_MEDIA_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
CMSBOT_MEDIA_ACCESS_KEY_ID=...
CMSBOT_MEDIA_SECRET_ACCESS_KEY=...
CMSBOT_MEDIA_PUBLIC_URL=https://media.example.com
```

Files are stored under `<project>/<item id>/`, so one bucket serves every project and a project's
files can be listed, costed or deleted together. Agents upload through the Hub
(`cmsbot hub upload <item_id> <file>`), so only the Hub holds bucket keys. File names contain a
random part: links cannot be guessed, but they are public, because posting APIs have to fetch them.
Set `CMSBOT_HUB_PUBLIC_URL` so posting agents receive full links when files are on the Hub's disk.

## Carousels

A carousel item's body is a slide spec (see `src/cmsbot/carousel/__init__.py`). Slides are laid out
as HTML in the project's colours, fonts and wordmark (the `visual` section of its brand kit) and
screenshotted to 1080x1350 PNGs, so words are always real type.

- Agents: `render_carousel(item_id)` (MCP) or `cmsbot hub render <item_id>`; add `--local` to render
  on your own machine and upload when the Hub has no browser.
- Reviewers: the review page shows a live preview while editing slide text. Saving or approving
  renders the images again. A carousel can't be approved while its images are older than its text.
- Files only: `cmsbot carousel render spec.json --out slides/ --project-file projects/x.json`.

Rendering needs a headless Chrome on whichever machine renders. The container installs one.

## Short video

A `short_video` item's body is a script: hook, beats (narration, on-screen line, visual note),
ending. It is reviewed twice.

1. **Script.** The reviewer reads and edits the words and presses "Approve script". The item returns
   to the drafting agent. "Render preview" at this stage builds the script as branded cards with
   captions, to judge pacing before anything is spent on footage.
2. **Video.** The agent attaches footage and sound by role (`shot-01`…, `voiceover`, `timing`,
   `music`) and renders. The reviewer sees the video, a contact sheet and a list of checks, and
   approves. Editing words here renders the video again on save or approve. The posting agent
   receives only the finished video, never the working files.

The renderer (`src/cmsbot/video/`) draws every word in code over the footage: wordmark, on-screen
lines, source and captions, in the brand kit's colours and fonts, at 1080x1920. Captions follow
word timestamps when a `timing` file is attached; otherwise they are spread across the voiceover by
word count, and the checks say so. It needs `ffmpeg` and a headless Chrome on the rendering machine;
the container has both. Generating the footage itself (Seedance, Grok Imagine) is a separate step
that is not built yet.

## Generating footage and voice

Paid generation runs inside the Hub, so the keys, the spend caps and the cost record are in one
place and every agent can use them without holding a key.

| Tool | Does | Provider (key on the Hub) |
|---|---|---|
| `media_options` | Shows what is configured and how much cap and budget is left | |
| `generate_footage` | A still or a clip for one shot, attached as `shot-NN`; a clip starts from the shot's still | Grok Imagine (`XAI_API_KEY`), Seedance through OpenRouter (`OPENROUTER_API_KEY`) |
| `generate_voiceover` | Speaks the script, attached with word timings for exact captions | ElevenLabs (`ELEVENLABS_API_KEY`; voice from the brand kit's `voiceover.voice_id` or `ELEVENLABS_VOICE_ID`) |

Rules: nothing is generated before a person approves the script; each item has a media cap
(project setting `item_media_cap_usd`, default $10); the project's `monthly_budget_usd` is a hard
stop; every call is written to the cost record against the item. Costs are the provider's reported
figure when it gives one (OpenRouter does), otherwise an estimate from the `CMSBOT_RATE_*` values;
set those to your real rates.

Put the keys in a file only root can read and pass it to the container:

```bash
sudo install -m 600 /dev/null /srv/cmsbot-hub/hub.env
```

```bash
sudo nano /srv/cmsbot-hub/hub.env
```

```bash
docker run ... --env-file /srv/cmsbot-hub/hub.env ...
```

The file holds lines such as `XAI_API_KEY=...`, `OPENROUTER_API_KEY=...`, `ELEVENLABS_API_KEY=...`.
A managed secret store (AWS Secrets Manager, SSM Parameter Store) is worth it once the Hub runs
somewhere with an instance role, such as ECS or EC2; on a single Lightsail box the server would
need a stored AWS key to read it, which protects nothing extra.

## Who can see what

Every token has a role and, optionally, one project. A token limited to a project can't read or
change anything in another: items, briefs, signals, learnings, spend and settings are all checked.

```bash
cmsbot hub token create "dana (haidaa)" --role admin --project haidaa
```

That reviewer signs in to the same review page and sees only Haidaa. Do the same for agents: a
Grok Bot that posts for one project gets `--role distributor --project <slug>`. Tokens are stored
hashed, shown once, and revoked with `cmsbot hub token revoke <id>`; `token list` shows who holds what.

## Where credentials live

Each secret stays with the one party that uses it. The Hub and the repo hold none of the others.

| Secret | Lives in | Never in |
|---|---|---|
| Social account logins and API keys (X, Meta, LinkedIn, TikTok) | The posting agent's own secret store (Grok Bot's connected apps and secrets) | The Hub, the repo, a brand kit |
| Image, video and voice generation keys (xAI, OpenRouter for Seedance, ElevenLabs) | The Hub host's environment, in a root-only file outside the repo and image | Any agent, the repo, a brand kit |
| Text model keys, only if the unattended API pipeline is used | That runner's environment or an untracked local `.env` | The Hub, the repo |
| Analytics access (GA4, Mixpanel, Search Console, Ahrefs) | The analysis agent's connectors (signed in by the owner) | The Hub, the repo |
| Media bucket keys | The Hub host's environment variables | Any agent |
| Hub tokens | One per agent, in that agent's secret store | The repo; another agent |

What goes in the repo is only non-secret configuration: account handles, property ids, pillar names.
The Hub is what lets agents work together without sharing secrets: they exchange drafts, approvals
and results through it, and each keeps its own keys.

## Connect agents

MCP endpoint: `https://<your-hub>/mcp`, with the header `Authorization: Bearer <token>`.
Clients that can store only a URL can use `https://<your-hub>/mcp?token=<token>`.

Claude Code:

```bash
claude mcp add --transport http cmsbot-hub https://<your-hub>/mcp --header "Authorization: Bearer <generator token>"
```

Grok Bot: add a custom connector of type "Remote HTTPS" with that URL and the header carrying the
distributor token, then give it the text of `skills/cmsbot-distribute/SKILL.md` as its instructions.

Give each agent its own token and the narrowest role. `--project <slug>` limits a token to one project.

## REST

Everything the MCP tools do is also under `/api` with the same token (interactive reference at
`/api/docs`). `cmsbot hub brief <project>` and `cmsbot hub ready` are thin wrappers for scripts.

## How results are judged

`performance` (MCP) or the Insights tab compares posts in a way that avoids the usual traps:

| Trap | Guard |
|---|---|
| New posts look worse because they are new | Each post is judged on the snapshot nearest a fixed age (7 days by default); younger posts wait |
| Channels and seasons differ | Each post is compared with the median of other posts on the same channel within 28 days |
| One lucky post rewrites the strategy | Averages are pulled toward "typical" until a value has enough posts; under 5 is "not enough data" |
| Winners crowd out everything else | The suggested mix keeps a floor for every option |

Report metrics at about 24h, 72h and 7d. Site results come from GA4 or Mixpanel grouped by
`utm_content` and are sent with `report_conversions`.
