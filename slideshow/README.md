# CMSBOT Slideshow Pipeline

Repurpose blogs into short-form slideshow specs, image packs, and review pages.

## Setup

The slideshow scripts can read a CMSBOT project profile JSON file for brand voice, CTAs, site URL, and screenshot references.

```bash
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
```

Optional project-pack overrides:

```bash
export CMSBOT_SLIDESHOW_CONFIG_FILE=/path/to/marketing/cmsbot/slideshow/config.json
export CMSBOT_SLIDESHOW_HOOK_PROMPT_FILE=/path/to/marketing/cmsbot/slideshow/hook_generation.md
export CMSBOT_SLIDESHOW_SCREENSHOTS_DIR=/path/to/marketing/cmsbot/screenshots
export CMSBOT_SLIDESHOW_OUTPUT_DIR=/path/to/marketing/cmsbot/output/slideshow
```

Optional project profile:

```bash
../projects/example-project.json
```

## Generate slideshow specs

```bash
python blog_to_slideshow.py --blog-file ../output/my-post/index.html --project-file ../projects/example-project.json
python blog_to_slideshow.py --blog-slug how-we-handle-inbound-routing --project-file ../projects/example-project.json
```

## Full pipeline

```bash
./run_pipeline.sh --blog-slug how-we-handle-inbound-routing --project-file ../projects/example-project.json
./run_pipeline.sh --project-file ../projects/example-project.json --batch
```

The pipeline produces:

- JSON slideshow specs
- per-slideshow `script.txt` overlay instructions
- generated or copied images
- post-processed assets
- `review.html` for manual approval

If a product repo exports `CMSBOT_SLIDESHOW_SCREENSHOTS_DIR`, app-screenshot slides will use that directory instead of the core repo's `slideshow/screenshots/` folder.

## Performance tracking

Manual social logging still works:

```bash
python track_performance.py log --id stack-mistakes-v1 --platform tiktok --views 45000 --saves 1200
```

For blog SEO performance, import the latest combined GSC + GA4 evaluation output:

```bash
../run.sh seo evaluate
../run.sh slideshow track import-seo-eval
```

Or point at a specific report:

```bash
python track_performance.py import-seo-eval --file ../seo/output/evaluation_YYYY-MM-DD.json
```

This stores blog records with pageviews, organic sessions, conversions, impressions, clicks, ranking position, and the evaluator's performance score so the same local database can track both SEO content and social posts.
