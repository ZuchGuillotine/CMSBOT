---
name: cmsbot-onboard
description: Set up a new project in the CMSBOT Hub (brand kit, content pillars, channels, personas, budget). Use when a project has no entry in the Hub yet or its brand kit is thin.
---

# Onboard a project

Goal: a brand kit specific enough that two different agents would write recognisably the same
brand. The owner edits and saves it on the Hub's **Brand** tab; you draft it for them.

1. Read what exists: the product's site, its docs, and any published writing the owner likes.
   Quote real phrases. Do not paraphrase the brand into generic marketing language, and do not
   invent a personality it does not show.
2. Draft the kit and send it with `propose_brand(project, brand)`:
   - `website`, `description`: what it is and who it is for, in the brand's own words.
   - `tone`: plain sentences on how it sounds, who is speaking to whom, and what it never sounds
     like, with two or three short quotes from its real writing as examples.
   - `lexicon`: `{"prefer": [...], "avoid": [...]}`. Drafts using an avoided word are flagged.
   - `disallowed_claims`: what must never be said. Be exhaustive for health, finance or legal topics.
   - `ctas`: the calls to action it actually uses.
   - `visual.colors`: `base, glow, ink, muted, accent, accent2` as `#rrggbb`, taken from the site.
   - `personas`: per channel, `{account, type: "brand" | "character", who, disclosure}`. A character
     account is a consistent presenter and topic, not the logo; say which each channel uses.
3. Tell the owner the draft is waiting on the Brand tab. Nothing changes until they load it, adjust
   it and save. They can also add palette pictures and a logo there.
4. Ask the owner only what you cannot find: monthly budget, who reviews, which accounts exist, and
   whether cited claims must be verified before approval (yes for regulated topics). Those are set
   on the Settings tab, along with the default series for each anchor.
