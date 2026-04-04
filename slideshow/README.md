# CMSBOT Slideshow Pipeline

Repurpose blogs into short-form slideshow specs, image packs, and review pages.

## Setup

The slideshow scripts can read a CMSBOT project profile JSON file for brand voice, CTAs, site URL, and screenshot references.

```bash
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
```

Spec generation defaults to `anthropic`, but you can swap providers or raise the completion cap:

```bash
export CMSBOT_SLIDESHOW_PROVIDER=openai        # or anthropic, gemini, openai_compat, anthropic_opus
export CMSBOT_SLIDESHOW_MODEL=gpt-5.4-mini     # optional explicit model override
export CMSBOT_SLIDESHOW_MAX_TOKENS=8192        # optional larger JSON budget
```

Image generation defaults to `gemini`, but slideshow image generation can also use Grok or DALL-E:

```bash
export CMSBOT_SLIDESHOW_IMAGE_PROVIDER=grok    # or gemini, dalle
export XAI_API_KEY=...                         # for Grok
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
python blog_to_slideshow.py --blog-file ../output/my-post/index.html --provider openai --max-tokens 8192
```

## Full pipeline

```bash
./run_pipeline.sh --blog-slug how-we-handle-inbound-routing --project-file ../projects/example-project.json
./run_pipeline.sh --project-file ../projects/example-project.json --batch
CMSBOT_SLIDESHOW_IMAGE_PROVIDER=grok ./run_pipeline.sh --project-file ../projects/example-project.json --batch
```

The pipeline produces:

- JSON slideshow specs
- per-slideshow `script.txt` overlay instructions
- generated or copied images
- post-processed assets
- `review.html` for manual approval

Standalone image generation examples:

```bash
python generate_images.py --batch output/batch_2026-04-01 --provider gemini
python generate_images.py --batch output/batch_2026-04-01 --provider grok
python generate_images.py --batch output/batch_2026-04-01 --provider dalle
python generate_images.py --batch output/batch_2026-04-01 --provider grok --prompt-only
```

Artifact layout inside each slideshow folder:

- `images/` contains the copied screenshots or generated raw slide images
- `processed/` contains the post-processed JPEGs to publish
- `review_grid.jpg` is the contact-sheet style review thumbnail page for that slideshow

If a product repo exports `CMSBOT_SLIDESHOW_SCREENSHOTS_DIR`, app-screenshot slides will use that directory instead of the core repo's `slideshow/screenshots/` folder.
