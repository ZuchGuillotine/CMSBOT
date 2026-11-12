---
name: cmsbot-write
description: Draft content for a planned CMSBOT item (anchor article, thread, post, carousel, newsletter, video script) or revise one a reviewer sent back, then submit it for review. Use for scheduled drafting runs and for "needs revision" items.
---

# Write or revise

Needs: the `cmsbot-hub` tools. Uses your own writing ability; no model API key is required. (The
headless pipeline, `cmsbot generate --method multipass` and `cmsbot derive`, remains for unattended
API runs; send its output with `cmsbot hub push`.)

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
6. Re-read against the brand kit: banned words, disallowed claims, no first-person experience that
   nobody had. Fix, then `submit_for_review`.

## Derivatives
One `save_draft` per piece with `parent_id` = the anchor. State only facts from the anchor's claims
and list them in `body.claim_ids` (`"L1"` is the first claim). Write for the channel, not a resize:

| kind / channel | body |
|---|---|
| thread (x, threads) | `{"posts": [...]}`; each post stands alone; x posts <= 280 characters |
| post (linkedin, facebook, reddit) | `{"text": ...}`; reddit: write as a participant, check the subreddit's rules, no link-drop |
| carousel (instagram, tiktok, linkedin) | `{"slides": [{"type", "headline", "body"}], "caption": ...}`; one idea per slide |
| short_video script (tiktok, instagram, youtube) | `{"hook", "beats": [{"narration", "visual", "overlay_text"}], "ending", "caption"}` |
| newsletter (email) | `{"subject", "preheader", "markdown"}` |

Put `{{link}}` where the anchor's URL belongs; the Hub adds tracking tags when it is posted.
Set `attributes` from the plan. After each paid tool call, `record_cost`.
