---
name: cmsbot-write
description: Draft content for a planned CMSBOT item (anchor article, thread, post, carousel, newsletter, video script) or revise one a reviewer sent back, then submit it for review. Use for scheduled drafting runs and for "needs revision" items.
---

# Write or revise

Needs: the `cmsbot-hub` tools. Uses your own writing ability; no model API key is required. (The
headless pipeline, `cmsbot generate --method multipass` and `cmsbot derive`, remains for unattended
API runs; send its output with `cmsbot hub push`.)

## What to write
`get_brief(project)` lists `needs_revision` (do these first) and `to_write`: pieces a person has
planned, each with a kind, a channel, the anchor it comes from (`parent_id`) and a one-line brief in
`audit.brief`. Write exactly those. Do not add pieces nobody planned; if the owner asks for more or
fewer from an anchor, change the plan with `plan_series(anchor_id, [{kind, channel, count}])` and
then write what it lists. Fill a planned piece with `save_draft(item_id=...)`.

Within one anchor's series, give every piece its own angle: a different claim, objection, example or
reader question. Read the sibling titles in `get_series(anchor_id)` first so two threads do not say
the same thing.

For a piece that is already live, `import_published(project, url)` brings it in as an anchor. Then
call `set_claims` on it with every factual claim the series might repeat, before writing any piece.

## Revisions first
`list_items(project, status="needs_revision,failed")`. For each, `get_item`, read `review_notes`,
change what the note asks and nothing else, `save_draft(item_id=...)`, `submit_for_review`. If you
cannot do what the note asks, `report_failure` with the reason.

## An anchor
1. `get_brief(project)`: brand kit, lexicon, disallowed claims, accepted learnings.
2. Gather material before writing: real sources with URLs, numbers with where they come from, the
   common belief the piece corrects. Never invent a figure, quote, link or anecdote. What you cannot
   confirm goes in `gaps` as a bracketed note; the reviewer fills it or sends it back.
3. Decide the shape before the sentences: who reads this, the one point, what goes first, what is
   left out. Vary openings and endings from the last few pieces in the cluster list.
4. Write it. `save_draft(kind="blog", channel="blog", body={"markdown": ...}, gaps=[...])`.
5. `set_claims(item_id, [{text, citation, citation_url}])`: every factual claim a derivative might
   repeat, one per entry. The reviewer verifies each once, here.
6. `check_draft(item_id)`. Correct everything marked "fix" (length, banned words, invented
   experience, figures outside the claims) and save again. "note" items are measured patterns
   (a closing that restates the opening, announcing what the piece will do, contrast frames, generic
   phrases); fix them by changing the structure, not by swapping words. Then `submit_for_review`.

## What makes text read as machine-written
It is mostly shape, decided before the first sentence, and rewording afterwards barely helps.
Avoid: an opening that announces the topic; three parallel points of equal length; every paragraph
ending on a tidy summary line; "it's not X, it's Y"; a closing that restates the opening; hedges and
intensifiers standing in for a fact; a balanced "on the other hand" where the evidence is not
balanced. Prefer: starting with the most specific thing you have; uneven sections sized by how much
there is to say; a named source, number or example in place of a general claim; ending when the
last useful thing has been said. If the harness has a long-form writing skill installed (for
example `multipass-writing`), use it for anchors and apply this file for the Hub steps.

## Derivatives
One `save_draft` per piece with `parent_id` = the anchor. Run `check_draft` on each before submitting. State only facts from the anchor's claims
and list them in `body.claim_ids` (`"L1"` is the first claim). Write for the channel, not a resize:

| kind / channel | body |
|---|---|
| thread (x, threads) | `{"posts": [...]}`; each post stands alone; x posts <= 280 characters |
| post (linkedin, facebook, reddit) | `{"text": ...}`; reddit: write as a participant, check the subreddit's rules, no link-drop |
| carousel (instagram, tiktok, linkedin) | `{"slides": [{"type", "headline", "body"}], "caption": ...}`; one idea per slide |
| short_video script (tiktok, instagram, youtube) | `{"hook", "beats": [{"narration", "visual", "overlay_text"}], "ending", "caption"}` |
| newsletter (email) | `{"subject", "preheader", "markdown"}` |

For a carousel, call `render_carousel(item_id)` after saving and before submitting: it turns the slides
into images in the project's brand style. Never ask an image model to draw the words. Attach other
files (video, photos) with `cmsbot hub upload <item_id> <file>`.

Put `{{link}}` where the anchor's URL belongs; the Hub adds tracking tags when it is posted.
Set `attributes` from the plan. After each paid tool call, `record_cost`.
