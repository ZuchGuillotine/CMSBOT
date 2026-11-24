---
name: cmsbot-analyze
description: Pull content results into the CMSBOT Hub, attribute site traffic and signups to individual items, and propose evidence-backed changes to how content is made. Use for the weekly analytics run or when asked how content is performing.
---

# Analyze

Needs: the `cmsbot-hub` tools, plus whichever analytics connectors exist (GA4, Mixpanel, Search
Console, Ahrefs, Google Trends). Use what is connected; say what was not available.

0. **Check it is due.** `whats_due(project)`: run when `analyze` is listed for you, or the owner asked.
1. **Attribute.** Every link the Hub hands out carries `utm_content=cmsbot-<item id>`. Query GA4 or
   Mixpanel for sessions, signups and revenue grouped by `utm_content` and day, and send the rows
   with `report_conversions(project, source, rows)`. For blog items, also pull organic clicks by
   landing page from Search Console and report them the same way (source `gsc`).
2. **Fill gaps.** If the posting agent did not report a channel's numbers, pull them from the
   channel's export or API and `report_metrics`.
3. **Read.** `performance(project)`. Read `caveats` first. The report already compares posts at the
   same age against their own channel's recent baseline and shrinks small samples; do not undo that
   by eyeballing raw likes or last week's best post.
4. **Propose, sparingly.** `propose_learning` only when a verdict is "above baseline" or "below
   baseline" on at least five posts, and say it as an instruction a writer can follow ("Open threads
   with the study result, not a question"), with the numbers in `evidence`. One or two per run. A
   person accepts or rejects each; accepted ones appear in every later brief.
5. **Search and trends.** For standing demand, check keyword volume and difficulty for the pillar's
   topics (Ahrefs or Search Console) and submit durable opportunities with `submit_signal`
   (source `search`, `expires_in_days` 60+). For spikes, use Google Trends with a short expiry.
6. **Money.** Report spend against budget and cost per signup by channel from the same report. If a
   channel has cost and no signups after a fair run, say so plainly.
7. **Improve a method, rarely.** The performance report breaks results down by `skill` (the stamp of
   the skill version that made each draft). If a version is "below baseline" on at least five posts,
   or you can name a specific habit in the skill that the weaker posts share, draft a new version
   and send it with `propose_skill_change(name, body, rationale, project, evidence)`. Change one
   thing, for one project, and say what result would show it worked. Do not propose a change
   because the newest posts look better or worse (they are not yet comparable), because of one
   standout post, or while another change to that skill is still unmeasured: results could not be
   attributed. The Hub attaches its own cautions to your proposal; a person decides.
8. `log_run(project, "analyze", summary)`.
9. Write a five-line summary: what was measured, what is working, what is not, what you proposed,
   what data was missing.
