# CMSBOT refactor plan

Started 2026-11-11, last updated 2026-11-12. This is the working plan; update it as steps land. Setup instructions are in
[`hub.md`](hub.md), skills in [`../skills/README.md`](../skills/README.md).

## What was actually wrong

The pieces existed. The loop between them did not.

| Symptom | Cause found in the code |
|---|---|
| Generation, distribution, analytics and improvement don't connect | Each kept its own state: files on a laptop, Micros' database, Grok Bot's own memory. Nothing was shared, so nothing could close the loop |
| Grok Bot doesn't reliably know what is approved | Approval lived only in the Micros admin database. The only export was a markdown file pulled to a laptop twice a day |
| Trends and CMSBOT content are at odds | Trends had no way into planning. Two planners, two calendars |
| No learning from results | No metrics came back at all: no metrics table or endpoint, and no tracking tags on links, so traffic could not be tied to a post |
| Review is slow and costly | The Micros queue page shows social copy read-only. A one-word fix meant sending it back to an agent or editing files and re-running |
| Every project needs its own dashboard | Review was built inside one product |
| Adding a tool is hard | Capabilities were Python modules wired into one CLI; a new method meant changing the pipeline |
| Video did not work | The vendor path produced templated posts that TikTok removed; core only ever produced scripts |
| Cost is opaque | Cost was tracked per run in local files, not per project or per result |

Also found: `main` was 15 commits behind `feat/content-engine-v2`, the branch Micros actually pins.
This work is built on top of that branch.

## Shape of the fix

Three parts, with one rule: **state lives in one place, and every agent and person reaches it the
same way.**

1. **Hub** (`src/cmsbot/hub/`). One small service for all projects: plans, drafts, review, approved
   copy, posts, metrics, costs, trend signals, learnings. People use its review page. Agents use its
   MCP endpoint (Claude, Grok Bot, Codex all speak MCP) or REST. Roles keep agents from approving and
   keep posting agents from seeing anything unapproved.
2. **Skills** (`skills/`). The methods: onboard, plan, write, short video, distribute, analyze. A
   skill is a folder of instructions run by whatever agent the user already pays for, so drafting
   uses the existing Claude subscription instead of metered API calls. Adding a capability is adding
   a folder.
3. **Tools** (the existing Python). Deterministic work that should not be left to a model: claim and
   surface gates, rendering, statistics, cost roll-up. The multipass and derive pipelines stay as the
   unattended API path and feed the Hub through `cmsbot hub push`.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where state and review live | One standalone Hub for all projects, SQLite, one container | One small bill in total, not one per project; runs on a laptop until it is worth hosting |
| How agents connect | MCP with per-agent tokens and roles | Works for Claude and Grok Bot alike; no agent-specific glue |
| Who drafts | The user's own agent harness via skills; API pipeline optional | Cost, and methods can be swapped without code |
| Trends vs pillars | Posting/trend agents submit signals; planning turns the ones that fit a pillar into clusters, capped at about a third of a period | One calendar instead of two |
| Small review fixes | Reviewer edits inline and approves in one step; approval is tied to the exact copy | Removes the round trip to an agent |
| Judging results | Same-age comparison, per-channel local baseline, shrinkage, exploration floor | Avoids recency and small-sample mistakes |
| Method changes | Proposed by an agent with evidence, in effect only after a person accepts | Keeps self-improvement reviewable |
| Character account vs brand account | Per channel, recorded in the brand kit and on every item as `persona`, then compared on results | The branded TikTok underperformed; this makes it a measured choice per channel. Disclosure rules still apply |
| Video stack | Seedance and Grok Imagine through APIs; text and captions rendered by code; no browser automation | Matches the tools available; the browser-driven parts of the reference workflow were its brittle parts |
| First projects | Micros Tracker, Haidaa, InfiniOffice | Owner's call |
| Micros blogs and newsletters | Stay in the Micros admin for now; social kinds go through the Hub | Micros already has the email sending and deploy logic |
| Media files | In a bucket (R2 or S3) or on the Hub's disk, under `<project>/<item>/`; never in the database | Video volumes; one bucket serves all projects |
| Credentials | Each agent keeps its own; the Hub holds only its tokens and the bucket keys | Agents collaborate through the Hub without sharing secrets |

## Status

Done in step 1 (this branch):
- Hub service: data model, roles, lifecycle, hash-bound approval, claim ledger and gap blockers,
  distribution claim/post, metrics, conversions, costs and budget, signals, learnings.
- Review page: all projects in one queue, inline editing for every content kind, per-post character
  counts, claim verification, approve / schedule / send back / reject, keyboard navigation, insights.
- MCP endpoint (19 tools) and REST API over the same rules.
- Performance analysis with the four guards.
- `cmsbot hub` commands, including `push` from existing asset sets.
- Six skills, container file, docs, tests.

Done in step 2:
- Carousels in the Hub: renderer in core themed from each brand kit, live preview while reviewing,
  automatic re-render on save or approve, and a blocker when images are older than the slide text.
- Media storage: Hub disk by default, any S3-compatible bucket (Cloudflare R2, S3) by configuration;
  uploads go through the Hub.
- Per-project access: reviewer and agent tokens can be limited to one project, checked everywhere.

Done in step 3:
- Video renderer: script + footage + voiceover + brand kit to a captioned 1080x1920 MP4 with a
  contact sheet and checks; shots without footage render as branded cards.
- Two-stage video review in the Hub (script, then video), re-render on reviewer edits, and only the
  finished video handed to the posting agent.

Done in step 4:
- The Hub is live at its own address on a small AWS instance.
- Generation through the Hub: footage and voiceover tools with a per-item cap, the monthly budget
  as a hard stop, a no-spend-before-script-approval rule, and automatic cost records.

Done in step 5:
- Settings tab: access tokens (create, rotate, revoke, expiry, last used), provider keys stored
  encrypted per Hub or per project, and projects with budgets.
- First-run setup with a one-time code, and a workflow that publishes the Hub image, so a new
  user runs one container and configures everything in the browser.

Done in step 6:
- Writing checks without a model on every draft (limits, banned words, invented experience,
  figures outside the claims, structure patterns).
- Rendering and generation run as background jobs with a status call; found on the first live run,
  where a 70-second render outlived a 30-second proxy limit.

Done in step 7:
- Anchors and series: a default series per project, a per-anchor planner with counts per content
  type and channel, the anchor's tree of pieces with status, and import of already-published pieces.
- First live generation run (still, clip, render) and the fixes it led to.

Done in step 8:
- Brand tab: voice, look and accounts per project, colours read from uploaded pictures or the
  site's styles, live slide preview, and agent-drafted kits that a reviewer accepts.

Done in step 9:
- Skills in the Hub: built-ins seeded and served to agents, a start-here skill, upload and edit,
  per-project versions, history and restore, and agent-proposed changes with evidence cautions.
- Rhythm and what's due: one call that tells each role what to do now, run logging, snapshot
  reminders at 24h/72h/7d, and the same list on the Insights tab.

Done in step 10:
- An all-round `agent` role, and each connected agent is told its scope (`whoami`, role-filtered
  tool list).

Done in step 11 (2026-11-14, from the first live posting queue and video):
- Posting times per project and channel (`hub/cadence.py`, Settings tab). Approval takes the next free
  time; a one-press schedule for what was approved before. `whats_due` shows the cadence to every role.
- Tracking tags are added once at hand-over (they were added twice wherever copy used `{{link}}`), a
  piece with no link of its own points at its anchor, and `{{link}}` with nothing to fill it blocks approval.
- Video: every segment is encoded with the same colour tags, and a new `picture` check compares the
  picture's length with the sound's. A live render mixing generated clips with stills came out with
  25 s of sound and 10 s of picture while every check passed. Not reproduced off the Hub (ffmpeg 6.1
  and a current build both render it whole), so confirm with a live render after deploying.

## Where things stand (2026-11-12)

**Live:** the Hub runs at its own address on one small AWS Lightsail instance (about $13 a month for
all projects). InfiniOffice exists on it with one imported anchor (the bookkeeping guide) and a
13-piece series drafted by an outside agent. A generator token and a Grok Bot token exist.

**Proven live:** first-run setup, tokens, import, series planning, an outside agent drafting through
the MCP endpoint, a generated still and a Grok video clip, a rendered video.

**Not yet proven live:** no piece has gone draft → approve → post → metrics → analysis through the
Hub. Grok Bot has not been connected. Seedance (after the audio fix) and ElevenLabs have not
completed a real call. The what's-due loop, skill proposals and the Brand tab's site reader have
only been tested locally. Cost rates for generation are placeholders.

**Decided today, not built:** Hub-run drafting with model API keys and wake hooks were discussed and
deferred. Splitting agents means starting each one separately or on its own schedule.

## Trial and product decision

The owner is considering turning CMSBOT into a product. The agreed position: not on today's
evidence. Run it on the three projects first, then decide.

- **Review date: 2026-12-10** (four weeks). A reminder is scheduled.
- **What to look at:** pieces drafted, approved and posted per project; reviewer time per week;
  share of posts with a 7-day numbers snapshot; visits and signups attributed to individual posts
  (`utm_content=cmsbot-<id>`); spend per project and cost per signup; how often runs happened without
  being prompted (the last-runs table); what broke.
- **Decide to productize only if** the loop ran without hand-holding and there are results worth
  putting on a landing page. Otherwise it stays an internal, open-source tool.
- If it goes ahead, the gaps are: accounts and sign-in instead of tokens, multi-tenant hosting,
  billing, onboarding, and channel posting that does not depend on a third-party agent.

## Next, in order

1. **Redeploy and finish the InfiniOffice run.** Fill in the Brand tab, re-read the guide, redo its
   claims, review the 13 pieces, approve some, reject test item 1.
2. **Connect Grok Bot** as distributor and post the approved pieces. This is the first real test of
   the hand-off and of metrics coming back.
3. **Schedule the agents** (daily wake, ask what's due) and let the loop run for the trial.
4. **Onboard Haidaa and Micros:** brand kits, default series, tokens. Micros blogs and newsletters
   stay in the Micros admin; its social pieces go through the Hub.
5. **Wire analytics:** GA4, Mixpanel, Search Console and Ahrefs through the analysis agent's
   connectors; confirm attribution by `utm_content` works on each site.
6. **Finish generation:** retry Seedance, set an ElevenLabs voice, replace placeholder rates with
   real prices, find a higher resolution for Grok video or prefer Seedance.
7. **Smaller gaps:** delete or archive for items; private-until-approved media if wanted; uploaded
   brand pictures feeding footage prompts; stamping `persona` on drafts automatically; skills with
   helper files.
8. **Later:** Postgres or Cloudflare D1 storage; photo-backed carousels; Hub-run drafting with API
   keys; wake hooks; retiring the file-based commands the Hub replaces; merging to `main` (which also
   publishes the container image).

## Open questions for the owner

- Which accounts will Grok Bot post to for Haidaa and InfiniOffice, and when are the new ones
  (InfiniOffice X, Meta app, TikTok) created?
- Should media stay publicly reachable by link before approval, or be private until approved?
- Monthly budgets for Haidaa and Micros, and who reviews each.
- When to merge this branch to `main`.
