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

Nothing has to be built or put in an `.env` file. Start the container with a folder for its data:

```bash
docker run -d --name cmsbot-hub --restart=always -p 8787:8787 -v cmsbot-hub-data:/data ghcr.io/zuchguillotine/cmsbot-hub:latest
```

```bash
docker logs cmsbot-hub
```

The log shows a one-time setup code. Open the Hub in a browser, enter the code and your name, and
you get the first reviewer token. Everything else is done on the **Settings** tab:

- **Access tokens:** create one per person or agent, with a role and optionally one project and an
  expiry. Each is shown once, with ready-to-paste connection details. Rotate replaces a token and
  withdraws the old one at once; Revoke withdraws it. The last all-projects reviewer can't be revoked.
- **Provider keys:** xAI, OpenRouter and ElevenLabs keys, for the whole Hub or for one project (a
  project's own key wins). Saved encrypted, never displayed again, with a Test button.
- **Projects:** add a project and set its monthly budget, media cap per item, and whether claims
  must be verified.

The published image is built from `main` by `.github/workflows/hub-image.yml`. To build it yourself
instead: `docker build -t cmsbot-hub .`. To run without Docker: `pip install -e ".[hub]"` then
`cmsbot hub serve` (rendering then needs ffmpeg and a headless Chrome on the machine).

A project's brand kit and pillars are loaded from its `project.json`:

```bash
export CMSBOT_HUB_URL=https://<your-hub> CMSBOT_HUB_TOKEN=<reviewer token>
```

```bash
cmsbot hub project projects/<slug>.json --budget 100
```

Send existing pipeline output for review (safe to re-run):

```bash
cmsbot hub push output/asset-sets/2026/2026-11/<asset-set-id> --project <slug> --link https://site/blog/<slug>
```

## Host it

One process and one data folder serve every project, so it needs the smallest instance a host
sells with a persistent disk for `/data`: a few dollars a month in total, not per project. No
managed database or queue. Put HTTPS in front (the host's TLS, or Caddy) and set
`CMSBOT_HUB_PUBLIC_URL=https://<your-hub>` so posting agents get full media links. Keep `/data`
on local disk, run one container against it, and back it up: it holds the database, the media and
`secret.key`, which decrypts the stored provider keys. Anyone with a copy of that whole folder can
read those keys, so treat backups of it as sensitive. Cloud agents such as Grok Bot can only reach
a Hub that has a public HTTPS address.

Optional environment variables: `CMSBOT_HUB_SECRET_KEY` (supply the encryption key yourself instead
of the generated file, for example from a secret manager), the `CMSBOT_MEDIA_*` bucket settings
below, and provider keys by name as a fallback when nothing is saved in Settings.

## Skills

A skill is a written method an agent follows (plan a period, write a piece, post, analyse). The Hub
holds them so every connected agent gets the same current version, whatever harness it runs in.

- **Built in:** `cmsbot-start` (read first by any new agent), `cmsbot-onboard`, `cmsbot-plan`,
  `cmsbot-write`, `cmsbot-short-video`, `cmsbot-distribute`, `cmsbot-analyze`. They are installed
  automatically and refreshed on upgrade unless you have edited the Hub's copy.
- **Agents:** `list_skills`, then `get_skill(name, project)`. The connection's own instructions tell a
  new agent to read `cmsbot-start`. Each skill comes with a stamp such as `cmsbot-write@3`, which
  agents set on the drafts they make with it.
- **Skills tab:** read and edit any skill, upload a new one (a `SKILL.md`: name and one-line
  description, then the method), see every version, and restore an earlier one. With a project
  selected you edit that project's own version, which wins there; other projects keep the hub-wide one.
- **Local copy:** `cmsbot hub skills pull` writes the Hub's versions into a skills folder for
  harnesses that load skills from disk.

### Improving a skill from results
The performance report breaks results down by skill stamp, with the same guards as everything else
(same age, per-channel baseline, small samples discounted). An agent can propose a new version with
`propose_skill_change`; it appears on the Skills tab as a line-by-line difference with the agent's
reason and evidence, plus the Hub's own cautions: no measured posts for the current version, too few
to judge, no clear difference from typical, or a change that would affect every project. One
proposal per skill at a time, so a result can be attributed to one change. A person accepts or
rejects; nothing an agent proposes takes effect on its own.

## Rhythm and what's due

Agents do not keep their own calendar. At the start of a run they call `whats_due(project)` and do
what is listed for their role; at the end of a planning or analysis run they call `log_run`. A run
that was missed is simply still due next time.

| Due item | For | When |
|---|---|---|
| revise, write | drafting agent | pieces sent back, failed, or planned |
| review, decide | reviewer | drafts waiting; proposed learnings or skill changes |
| post | posting agent | approved and due now |
| metrics | posting agent | a post reached 24 hours, 72 hours or 7 days without its numbers |
| plan, analyze | drafting / analysis agent | the last recorded run is older than the project's rhythm |

The Insights tab shows the same list with who it is waiting on, when each kind of run last happened
and what it did, and the rhythm (plan every N days, analyse every N days) per project. Scheduling
itself stays in the agent's harness: schedule each agent to wake daily and ask what is due.

## Brand kit

Each project has a brand kit on the **Brand** tab. Everything that writes or designs for the project
reads it: drafting agents get it in every brief, the writing checks use its words to avoid, and
slides and videos are drawn in its colours, fonts and logo.

- **Site or app link:** "Read site" fills in the name and description and lists the colours used in
  the site's stylesheets, with a suggested assignment and a logo candidate.
- **Voice:** a free-text description of the tone, words to use and to avoid, things never to claim,
  and calls to action.
- **Look:** upload palette or mood pictures; the Hub reads the colours from each. Click a role
  (background, text, accent...) and then a swatch, or use the colour pickers. A live slide preview
  shows the result before saving. Pictures are kept as reference for image and video prompts.
- **Accounts:** per channel, the handle, whether it posts as the brand or as a consistent character,
  and any disclosure needed when the product is mentioned.

An agent can draft a kit with `propose_brand`; it appears as a banner on the Brand tab and changes
nothing until a reviewer loads it and saves.

## Anchors and their series

An anchor is the piece that holds the facts, usually a blog post or guide. Everything made from it
(threads, posts, carousels, short videos, a newsletter) is linked to it and restates its verified
claims. Open an anchor from the **Anchors** tab to see its series.

- **Default series (recipe):** on the Settings tab, set per project what each new anchor gets, for
  example three X threads and one LinkedIn post.
- **Per anchor:** on the anchor's page, raise or lower each count with the steppers and press Update
  series. One guide may be worth eight threads and three videos; another, one carousel. Added pieces
  appear as "to write" and drafting agents pick them up from the brief. Lowering a count removes only
  pieces nobody has started.
- **Already published:** "Import published piece" takes an address (and the text, if the page can't
  be read). The piece is recorded as published, never enters review or posting, and gets the default
  series. Agents do the same with `import_published`, and adjust with `plan_series`.

## Writing quality

Text is drafted by the agent the project already uses, following `skills/cmsbot-write`, so the Hub
needs no text-model key. What keeps drafts from reading as machine-written is split in two:

- **Method, in the skill:** gather real material first, decide the shape before the sentences, and
  avoid the structural habits listed there. Rewording afterwards changes little.
- **Checks, in the Hub (`check_draft`, and shown on every item):** platform length limits, the
  brand's banned words (`lexicon.avoid` in the brand kit), invented first-person experience, figures
  that are not in the anchor's verified claims, and measured patterns such as a closing that restates
  the opening or contrast frames. Items marked "fix" are objective; "note" items are for a person
  to judge. Only an over-length post blocks approval.

The older API pipeline (`cmsbot generate --method multipass`, `cmsbot derive`) still exists for
unattended runs. It reads Anthropic, OpenAI or Gemini keys from its own environment, not from the
Hub, and sends its output with `cmsbot hub push`.

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
figure when it gives one (OpenRouter does), otherwise an estimate from the rates on the Settings
tab; set those to your real prices.

A reviewer enters the keys on the Settings tab. They are encrypted in the Hub's database with a
key kept beside it (or supplied as `CMSBOT_HUB_SECRET_KEY`), shown afterwards only as their last four
characters, and never sent to an agent. Use Test after saving. A project that has its own provider
account can have its own key, so its spend lands on its own bill.

## Who can see what

Every token has a role and, optionally, one project. A token limited to a project can't read or
change anything in another: items, briefs, signals, learnings, spend and settings are all checked.

On the Settings tab, create a reviewer token with a project chosen. That person signs in to the
same page and sees only that project, and can hand out or withdraw access to it and nothing else.
Do the same for agents: a Grok Bot that posts for one project gets a distributor token for that
project. Tokens are stored hashed, shown once, can expire, and show when they were last used.

### Roles

| Role | Can | Cannot |
|---|---|---|
| reviewer (a person) | everything, including approve, verify claims, settings, access | |
| agent | everything the three roles below can | approve, verify claims, change settings or access |
| generator | plan, draft, render, generate media, submit | approve; post; edit a piece once it is with the reviewer |
| distributor | see approved copy that is due, claim it, record the post, report numbers and trends | see or edit drafts |
| analyst | read everything, report results, propose learnings and skill changes | draft, post |

Use **agent** when one agent does all the work. Use the narrow roles when different agents (or
vendors) do different jobs: a distributor has never seen an unapproved draft, so it cannot post
one by mistake. No agent role can approve.

## Where credentials live

Each secret stays with the one party that uses it. The Hub and the repo hold none of the others.

| Secret | Lives in | Never in |
|---|---|---|
| Social account logins and API keys (X, Meta, LinkedIn, TikTok) | The posting agent's own secret store (Grok Bot's connected apps and secrets) | The Hub, the repo, a brand kit |
| Image, video and voice generation keys (xAI, OpenRouter for Seedance, ElevenLabs) | The Hub, entered under Settings and stored encrypted | Any agent, the repo, a brand kit |
| Text model keys, only if the unattended API pipeline is used | That runner's environment or an untracked local `.env` | The Hub, the repo |
| Analytics access (GA4, Mixpanel, Search Console, Ahrefs) | The analysis agent's connectors (signed in by the owner) | The Hub, the repo |
| Media bucket keys | The Hub host's environment variables | Any agent |
| Hub tokens | Created under Settings, one per agent, kept in that agent's secret store | The repo; another agent |

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
