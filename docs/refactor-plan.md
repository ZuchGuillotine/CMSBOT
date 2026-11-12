# CMSBOT refactor plan

Started 2026-11-11. This is the working plan; update it as steps land. Setup instructions are in
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

Not done yet, in order:

1. **Stand it up.** Host the Hub, create tokens, connect Claude and Grok Bot. Verify Grok Bot's
   connector against the live endpoint (its docs describe remote MCP with headers; not yet tested).
2. **Onboard the three projects.** Brand kits for Haidaa and InfiniOffice do not exist yet; run
   `cmsbot-onboard` on each. Import Micros' kit from its project pack.
3. **Micros bridge.** Micros blogs and newsletters still publish through the Micros app's own queue,
   which has its own approval. Decide between (a) reviewing those two kinds in Micros as today and
   everything else in the Hub, or (b) a small Micros endpoint that accepts Hub-approved copy so
   review happens once. (b) needs a change in the Micros repo.
4. **First live generation.** Grok Imagine, Seedance (through OpenRouter) and ElevenLabs are
   wired into the Hub with caps and cost recording, written from the providers' docs and tested
   only against stand-ins. Put keys on the Hub, make one real still, clip and voiceover, and fix
   whatever the live APIs do differently. Set the `CMSBOT_RATE_*` values to real prices.
5. **Hosting on Cloudflare.** Media can go to R2 today. Running the Hub itself there means moving
   its storage from a SQLite file to D1 and checking the current Workers/Containers options; the
   storage layer is five methods, so this is contained. Until then any small container host works.
6. **Photo-backed carousels.** Slides accept a background image; add generation of those images
   (Grok Imagine) and a second layout, and record `style` so results can be compared.
7. **Scheduled runs.** Weekly plan, weekly write, daily distribute, weekly analyze, as scheduled
   tasks in the harness already in use. Start in shadow mode: everything drafted, nothing posted.
8. **Analytics connectors.** Wire the analyze skill to GA4, Mixpanel, Search Console and Ahrefs for
   each project; add a landing-page convention for channels where links are weak (TikTok, Instagram).
9. **Retire what the Hub replaces.** File-based `review approve`, the Drive and Sheet sync, and the
   `goals/` scaffolding, once the three projects run through the Hub. Merge to `main` when the owner
   is ready.
10. **Later: improving the tool itself.** Hubs could report anonymised error and outcome summaries
    upstream. Out of scope until the basic loop is proven.

## Open questions for the owner

- Where to host the Hub (the existing AWS account, or a small separate host)?
- Micros bridge: option (a) or (b) above?
- Who reviews each project, and does Haidaa or InfiniOffice have claims that need verification?
- Which channels and accounts exist today for Haidaa and InfiniOffice?
- Monthly content budget per project.
