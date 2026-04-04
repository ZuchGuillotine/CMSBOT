#!/bin/bash
# CMSBOT repo entrypoint
#
# Usage:
#   ./run.sh bootstrap
#   ./run.sh generate "Write a blog post about lead routing"
#   ./run.sh project init "Northstar CRM"
#   ./run.sh topics --project-file projects/example-project.json
#   ./run.sh export-blog-draft output/my-post
#   ./run.sh seo research
#   ./run.sh slideshow pipeline --project-file projects/example-project.json --batch

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP_SCRIPT="$SCRIPT_DIR/scripts/bootstrap.sh"
VENV="$SCRIPT_DIR/.venv"
SEO_DIR="$SCRIPT_DIR/seo"
SLIDESHOW_DIR="$SCRIPT_DIR/slideshow"

ensure_bootstrapped() {
  if [[ ! -x "$VENV/bin/python" ]]; then
    echo "CMSBOT is not bootstrapped yet."
    echo "Run: ./run.sh bootstrap"
    exit 1
  fi
}

run_cmsbot() {
  ensure_bootstrapped
  "$VENV/bin/python" -m cmsbot "$@"
}

case "${1:-help}" in
  bootstrap)
    "$BOOTSTRAP_SCRIPT"
    ;;

  generate|check|styles|models|topics|project|export-blog-draft)
    run_cmsbot "$@"
    ;;

  seo)
    shift
    if [[ $# -eq 0 ]]; then
      echo "Usage: ./run.sh seo <research|autocomplete|reddit|competitors|evaluate|evaluate:gsc|evaluate:ga4|test:connectivity> [args...]"
      exit 1
    fi
    (
      cd "$SEO_DIR"
      npm run "$@"
    )
    ;;

  slideshow)
    ensure_bootstrapped
    shift
    if [[ $# -eq 0 ]]; then
      echo "Usage: ./run.sh slideshow <specs|images|postprocess|review|pipeline|track> [args...]"
      exit 1
    fi

    subcommand="$1"
    shift

    case "$subcommand" in
      specs)
        (
          cd "$SLIDESHOW_DIR"
          "$VENV/bin/python" blog_to_slideshow.py "$@"
        )
        ;;
      images)
        (
          cd "$SLIDESHOW_DIR"
          "$VENV/bin/python" generate_images.py "$@"
        )
        ;;
      postprocess)
        (
          cd "$SLIDESHOW_DIR"
          "$VENV/bin/python" postprocess.py "$@"
        )
        ;;
      review)
        (
          cd "$SLIDESHOW_DIR"
          "$VENV/bin/python" build_review.py "$@"
        )
        ;;
      track)
        (
          cd "$SLIDESHOW_DIR"
          "$VENV/bin/python" track_performance.py "$@"
        )
        ;;
      pipeline)
        (
          cd "$SLIDESHOW_DIR"
          ./run_pipeline.sh "$@"
        )
        ;;
      *)
        echo "Unknown slideshow command: $subcommand"
        exit 1
        ;;
    esac
    ;;

  help|--help|-h)
    cat <<'EOF'
CMSBOT

Commands:
  ./run.sh bootstrap
  ./run.sh generate "Prompt text" [cmsbot args...]
  ./run.sh project <init|context|voice> [args...]
  ./run.sh topics [args...]
  ./run.sh export-blog-draft <output_dir> [args...]
  ./run.sh seo <research|autocomplete|reddit|competitors|evaluate|evaluate:gsc|evaluate:ga4|test:connectivity> [args...]
  ./run.sh slideshow <specs|images|postprocess|review|pipeline|track> [args...]

Examples:
  ./run.sh bootstrap
  ./run.sh generate "Write a blog post about N-of-1 experiments" --type blog --project-file projects/example-project.json
  ./run.sh project init "Northstar CRM"
  ./run.sh topics --project-file projects/example-project.json
  ./run.sh export-blog-draft output/n-of-1-experiments
  ./run.sh seo research
  ./run.sh slideshow pipeline --project-file ../projects/example-project.json --batch
EOF
    ;;

  *)
    echo "Unknown command: $1"
    echo "Run ./run.sh help for usage."
    exit 1
    ;;
esac
