#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/.venv"
SEO_DIR="$REPO_ROOT/seo"

echo "[bootstrap] repo: $REPO_ROOT"

if [[ ! -d "$VENV" ]]; then
  echo "[bootstrap] creating Python virtual environment"
  python3 -m venv "$VENV"
fi

echo "[bootstrap] installing CMSBOT package"
"$VENV/bin/pip" install -e "$REPO_ROOT"

if [[ -f "$SEO_DIR/package.json" ]]; then
  echo "[bootstrap] installing SEO dependencies"
  (
    cd "$SEO_DIR"
    npm install
  )
fi

echo "[bootstrap] done"
