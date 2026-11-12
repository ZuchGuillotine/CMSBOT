---
name: cmsbot-plan
description: Plan the next week or month of content for a project from its pillars, open trend signals and measured results. Use for scheduled planning runs or when asked what to make next.
---

# Plan a period

Needs: the `cmsbot-hub` tools.

1. `get_brief(project)`. Read pillars, recent clusters, accepted learnings, open signals, spend.
   If `spend.over_budget` is true, stop and say so.
2. Check the backlog first. If more than about two weeks of items are still in review, plan fewer
   new ones; unreviewed drafts are not output.
3. Choose themes (clusters), one per period:
   - Pillar themes: rotate pillars by weight; skip a theme used in the last four periods.
   - Trend themes: at most about a third of the period. Take a signal only if it fits a pillar and
     the brand can say something true about it. Create it with `origin="trend"` and the `signal_id`,
     so the signal is marked used. A trend that fits no pillar is not content; leave it.
   This is how trend work and pillar work stop competing: both become clusters in one calendar.
4. For each theme, decide the anchor (the piece that holds the facts, usually a blog post) and the
   derivatives per channel. Use `performance(project)` for the mix: follow `suggested_share` for
   formats and hooks, and act only on verdicts of "above baseline" or "below baseline". Anything
   marked "not enough data" still gets its share; that is how it earns enough data.
   The number of pieces per anchor is the owner's choice, not yours: each project has a default
   series (its recipe) and the owner adjusts single anchors in the Hub. Plan anchors; the series
   follows from the recipe.
5. Create each cluster with `create_cluster`, then one planned item per piece with `save_draft`
   (pass `body={}` so it stays "planned", put the brief in `audit.brief`, and set `attributes`:
   `hook`, `format`, `style`, `persona`, `length`). Attributes are what analysis compares later, so
   use the same small vocabulary every time.
6. Report the plan in a few lines: themes, counts per channel, expected cost, and anything you
   skipped and why.
