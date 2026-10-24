# Content engine v2: CMSBOT hand-off (branch `feat/content-engine-v2`)

Status as of 2026-10-23. Read this first when picking up CMSBOT work in a new session. The
product-side plan lives in the Micros repo (`marketing/CONTENT_ENGINE_V2_PLAN.md`); Micros pins this
branch by commit in `marketing/cmsbot/core.lock`. **Do not merge to main yet**; the owner will decide when.
After any change here: run the tests, commit, push the branch, then bump `CMSBOT_CORE_REF` in the
Micros `core.lock` to the new commit.

Tests: `PYTHONPATH=src ../CMSBOT/.venv/bin/python -m pytest -q` (or this checkout's `.venv` once bootstrapped).
Note: `../CMSBOT` is a separate checkout of the same repo holding a Codex agent's uncommitted WIP.
Leave its working tree alone.

## Done on this branch

| Commit | What |
|---|---|
| `641f6d0` | Claude 5 model ids, role fallback when a provider has no key, rubric thresholds read from JSON, hash-bound approvals, judge parse failures handled |
| `eacf128`, `f2bf022` | `generate --method multipass` (material → plan → draft → structure → surface → fit), PubMed sources, citation verification, variety ledger, `shape_check` vendored |
| `5c2cce5`, `db98f96` | `derive`: newsletter / thread / video_script light run from the claim ledger, with ledger, testimonial and surface gates (see README "Derivatives") |

Live results (Micros pack): multipass blog ≈ $0.33-0.41, 24-27 of 26-29 claims verified; derive ≈ $0.07-0.13
per kind. A full weekly batch (1 blog, 1 newsletter, 5 threads, 3 videos) is about $1.

## Remaining work, in priority order

### 1. Internal links from the sitemap (gate 2 in the plan)
Problem: multipass leaves internal links as gaps (`https://microstracker.com/blog/[exact slug needed`)
because the material stage doesn't know which pages exist.
- Add `--sitemap-url` (and `CMSBOT_SITEMAP_URL`, which the project pack sets) to `generate --method multipass`.
- Fetch the sitemap once per run (httpx, follow sitemap indexes), keep same-site `/blog/`, `/learn/` and
  similar URLs, and pass a short list (title-ish slug + URL, ranked by keyword overlap with the brief, max ~25)
  to MATERIAL and DRAFT as "INTERNAL PAGES (only these exist)".
- In the fit pass, extract same-domain links from the draft; any link not in the sitemap goes into `gaps`
  and `audit.json` as `invalid_internal_links`. Also strip the malformed `[exact slug needed` pattern.
- Tests: sitemap parsing (index + urlset), link validation, prompt contains the list.

### 2. PubMed relevance
Problem: `_gather_papers` takes the top results of 2-3 queries; some papers are off-topic and crowd the
12-paper cap.
- After collecting candidates (raise per-query results to ~8), one cheap `planner`/`auditor` call ranks
  titles + first 300 chars of abstracts against the brief and returns the relevant PMIDs (keep ≤12).
  Keep the order stable for papers the model omits. Log dropped titles.
- Prefer human studies and reviews: add `AND (humans[MeSH])` only when the query planner didn't already
  scope it; check the effect on recall with the magnesium and melatonin briefs before keeping it.

### 3. Remove Micros-specific leftovers from core
- `seo/src/evaluator.ts` `buildContentPrompt` (~line 328) hardcodes the supplement/biomarker audience and
  categories. Move audience and category guidance into the project profile (`project.json`) and read it.
- `slideshow/generate_images.py` `SCENE_TEMPLATES` (~line 86-150) are supplement scenes; load them from the
  project pack (e.g. `CMSBOT_SLIDESHOW_SCENES_FILE`), with a neutral default.

### 4. Video rendering (Phase 4, later)
`derive --kind video_script` produces scripts only. Rendering (template text overlays, captions, b-roll from
Veo 3.1 `-001` / Grok Imagine) follows the Micros `marketing/TIKTOK_PLAN.md`; a person records voiceover.
Not needed for the review queue to run.

### Known derive behaviours to watch
- The ledger gate counts bare small numbers ("1", "5") from list-like phrasing as unsupported figures; the
  fix pass usually rewrites them harmlessly. If reviewers see needless edits, ignore integers < 2 that aren't
  followed by a unit.
- `first_last_overlap` from `shape_check` is noisy on 40-60 second scripts; it is reported, not enforced
  after the single fix pass.
- Thread posts can exceed 280 characters by a few when a URL is included (X counts URLs as 23).
