#!/bin/bash
# run_pipeline.sh — Full slideshow content pipeline orchestrator.
#
# Usage:
#   ./run_pipeline.sh --blog-slug "how-to-build-a-supplement-stack"
#   ./run_pipeline.sh --batch          # Process all unprocessed blog posts
#   ./run_pipeline.sh --batch --prompt-only  # Generate everything except images
#   ./run_pipeline.sh --project-file ../projects/example-project.json --batch
#
# Environment variables required:
#   ANTHROPIC_API_KEY  — For Claude (hook/text generation)
#   GOOGLE_API_KEY     — For Nano Banana Pro (image generation) [optional if --prompt-only]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
DATE=$(date +%Y-%m-%d)
BATCH_DIR="$OUTPUT_DIR/batch_$DATE"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[PIPELINE]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
step() { echo -e "\n${BLUE}━━━ STEP $1 ━━━${NC}"; }

# Parse arguments
BLOG_SLUG=""
BATCH_MODE=false
PROMPT_ONLY=false
SKIP_IMAGES=false
SKIP_REVIEW=false
PROJECT_FILE=""
API_BASE=""
BLOG_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --blog-slug) BLOG_SLUG="$2"; shift 2 ;;
    --batch) BATCH_MODE=true; shift ;;
    --prompt-only) PROMPT_ONLY=true; shift ;;
    --skip-images) SKIP_IMAGES=true; shift ;;
    --skip-review) SKIP_REVIEW=true; shift ;;
    --project-file) PROJECT_FILE="$2"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    *) error "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -n "$PROJECT_FILE" ]]; then
  BLOG_ARGS+=(--project-file "$PROJECT_FILE")
fi

if [[ -n "$API_BASE" ]]; then
  BLOG_ARGS+=(--api-base "$API_BASE")
fi

# Validate
if [[ -z "$BLOG_SLUG" && "$BATCH_MODE" == "false" ]]; then
  error "Must specify --blog-slug <slug> or --batch"
  exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  error "ANTHROPIC_API_KEY not set"
  exit 1
fi

if [[ "$PROMPT_ONLY" == "false" && "$SKIP_IMAGES" == "false" && -z "${GOOGLE_API_KEY:-}" ]]; then
  warn "GOOGLE_API_KEY not set — will generate prompts only for AI images"
  PROMPT_ONLY=true
fi

# ─────────────────────────────────────────────────
step "1: Blog → Slideshow Specs"
# ─────────────────────────────────────────────────

if [[ -n "$BLOG_SLUG" ]]; then
  log "Processing single blog: $BLOG_SLUG"
  python3 "$SCRIPT_DIR/blog_to_slideshow.py" --blog-slug "$BLOG_SLUG" "${BLOG_ARGS[@]}"
elif [[ "$BATCH_MODE" == "true" ]]; then
  log "Processing all unprocessed blog posts..."
  python3 "$SCRIPT_DIR/blog_to_slideshow.py" --batch "${BLOG_ARGS[@]}"
fi

# Find the batch directory (just created)
if [[ ! -d "$BATCH_DIR" ]]; then
  # Try to find the most recent batch
  BATCH_DIR=$(ls -td "$OUTPUT_DIR"/batch_* 2>/dev/null | head -1)
  if [[ -z "$BATCH_DIR" ]]; then
    error "No batch directory found after Step 1"
    exit 1
  fi
fi

log "Batch directory: $BATCH_DIR"

# Count generated slideshows
SHOW_COUNT=$(find "$BATCH_DIR" -name "script.txt" | wc -l | tr -d ' ')
log "Generated $SHOW_COUNT slideshow specs"

# ─────────────────────────────────────────────────
step "2: Generate Images"
# ─────────────────────────────────────────────────

if [[ "$SKIP_IMAGES" == "true" ]]; then
  log "Skipping image generation (--skip-images)"
elif [[ "$PROMPT_ONLY" == "true" ]]; then
  log "Generating image prompts only (--prompt-only)"
  python3 "$SCRIPT_DIR/generate_images.py" --batch "$BATCH_DIR" --prompt-only
  log "Prompts saved to $BATCH_DIR/all_prompts.json"
  warn "To generate actual images, run: python3 generate_images.py --batch $BATCH_DIR"
else
  log "Generating AI images with Nano Banana Pro..."
  python3 "$SCRIPT_DIR/generate_images.py" --batch "$BATCH_DIR"
fi

# ─────────────────────────────────────────────────
step "3: Post-Processing"
# ─────────────────────────────────────────────────

# Check if there are any images to process
IMAGE_COUNT=$(find "$BATCH_DIR" -path "*/images/*" \( -name "*.jpg" -o -name "*.png" \) 2>/dev/null | wc -l | tr -d ' ')

if [[ "$IMAGE_COUNT" -gt 0 ]]; then
  log "Post-processing $IMAGE_COUNT images (film grain, compression, metadata strip)..."
  python3 "$SCRIPT_DIR/postprocess.py" --batch "$BATCH_DIR"
else
  warn "No images found to post-process."
  warn "Add your app screenshots to: $SCRIPT_DIR/screenshots/"
  warn "Then re-run: python3 generate_images.py --batch $BATCH_DIR"
fi

# ─────────────────────────────────────────────────
step "4: Build Review Page"
# ─────────────────────────────────────────────────

if [[ "$SKIP_REVIEW" == "true" ]]; then
  log "Skipping review page (--skip-review)"
else
  python3 "$SCRIPT_DIR/build_review.py" --batch "$BATCH_DIR"
  log "Review page: file://$BATCH_DIR/review.html"
fi

# ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━ PIPELINE COMPLETE ━━━${NC}"
echo ""
echo "Batch: $BATCH_DIR"
echo "Slideshows: $SHOW_COUNT"
echo ""
echo "Next steps:"
echo "  1. Open the review page in your browser"
echo "  2. Approve/reject each slideshow"
echo "  3. For approved slideshows, use the script.txt to type overlays natively in TikTok"
echo "  4. Post images from the processed/ folder (or images/ if no post-processing ran)"
echo ""
if [[ "$PROMPT_ONLY" == "true" ]]; then
  echo -e "${YELLOW}NOTE: Image generation was skipped. To generate images:${NC}"
  echo "  export GOOGLE_API_KEY=your_key"
  echo "  python3 $SCRIPT_DIR/generate_images.py --batch $BATCH_DIR"
  echo ""
fi
echo "Screenshots needed? Check each slideshow's script.txt for [Image: app_screenshot] entries."
echo "Place screenshots in: $SCRIPT_DIR/screenshots/"
