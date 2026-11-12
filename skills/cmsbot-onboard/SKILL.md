---
name: cmsbot-onboard
description: Set up a new project in the CMSBOT Hub (brand kit, content pillars, channels, personas, budget). Use when a project has no entry in the Hub yet or its brand kit is thin.
---

# Onboard a project

Goal: a project entry in the Hub that is specific enough that two different agents would write
recognisably the same brand.

1. Read what exists: the product's site, its repo docs, any past posts that the owner liked. Quote
   real phrases; do not paraphrase the brand into generic marketing language.
2. Write `projects/<slug>.json` (start from `projects/example-project.json`) with:
   - `voice`: 4-6 traits, each with a "sounds like / never sounds like" pair in `content.brand_rules`.
   - `lexicon`: `{"prefer": [...], "avoid": [...]}`. Words the brand uses and words it never uses.
   - `content.disallowed_claims`: what must never be said. For health, finance or legal topics, be exhaustive.
   - `visual`: `{"colors": [...], "fonts": [...], "logo": "...", "image_rules": [...]}` so slides and
     overlays look the same on every channel.
   - `personas`: one entry per channel account, e.g.
     `{"tiktok": {"account": "handle", "type": "character" | "brand", "who": "...", "disclosure": "..."}}`.
     A character account is a consistent presenter and topic, not the logo. Record which type each
     channel uses; the `persona` attribute on items lets results be compared instead of argued.
   - `disclosure`: per-channel text or settings required when the product is mentioned.
   - `content_ops.pillars`: 3-5 pillars, each with `key`, `label`, `goals`, `topic_seeds`.
   - `content_ops.platforms` and `cadence`: only channels someone will actually post to.
3. Ask the owner only what you cannot find: budget per month, who reviews, which accounts exist,
   and whether cited claims must be verified before approval (yes for regulated topics).
4. Register it: `cmsbot hub project projects/<slug>.json --budget <usd>` and, for regulated topics,
   add `--require-verified-claims`.
5. Create tokens for the agents that will work on it (`cmsbot hub token create ...`) and give each
   to the owner to store; never write a token into a file in the repo.
