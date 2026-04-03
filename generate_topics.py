#!/usr/bin/env python3
"""List CMSBOT topic briefs and emit ready-to-run generation commands."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cmsbot.topics import (
    build_cmsbot_command,
    format_topic_brief,
    latest_topic_brief_file,
    load_topic_briefs,
    merge_topic_briefs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--briefs-file",
        type=Path,
        default=None,
        help="Explicit topic brief JSON file. Defaults to the latest file in ./seo/output.",
    )
    parser.add_argument(
        "--curated-file",
        type=Path,
        default=None,
        help="Optional curated topic brief JSON file to merge in.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of topics to print.",
    )
    parser.add_argument(
        "--command",
        type=int,
        default=None,
        help="Print the cmsbot command for a specific topic index.",
    )
    parser.add_argument(
        "--project-file",
        type=str,
        default=None,
        help="Optional project profile to include in emitted commands.",
    )
    parser.add_argument(
        "--style",
        type=str,
        default=None,
        help="Optional style to include in emitted commands.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seo_output_dir = os.getenv("CMSBOT_SEO_OUTPUT_DIR", "seo/output")
    chosen_file = args.briefs_file or latest_topic_brief_file(seo_output_dir)
    if chosen_file is None:
        raise SystemExit("No topic briefs found. Run the SEO evaluator first or pass --briefs-file.")

    seo_briefs = load_topic_briefs(chosen_file)
    curated_briefs = load_topic_briefs(args.curated_file) if args.curated_file else []
    merged = merge_topic_briefs(seo_briefs, curated_briefs)

    if args.command is not None:
        if args.command < 0 or args.command >= len(merged):
            raise SystemExit(f"Topic index {args.command} out of range.")
        print(
            build_cmsbot_command(
                merged[args.command],
                project_file=args.project_file,
                style=args.style,
            )
        )
        return 0

    for idx, brief in enumerate(merged[: args.limit]):
        print(format_topic_brief(brief, idx))
        print()

    print(f"Loaded {len(merged)} topic briefs from {chosen_file}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
