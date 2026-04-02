# CMSBOT Slideshow Pipeline

Repurpose blogs into short-form slideshow specs, image packs, and review pages.

## Setup

The slideshow scripts can read a CMSBOT project profile JSON file for brand voice, CTAs, site URL, and screenshot references.

```bash
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
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

Add project screenshots to `slideshow/screenshots/` when a spec references `app_screenshot`.
