#!/usr/bin/env python3
"""
track_performance.py — Track slideshow performance and feed insights back into the pipeline.

Manages a local CSV/JSON database of post performance metrics.
Generates weekly reports and identifies top-performing formats/hooks.

Usage:
  python track_performance.py log --id "stack-mistakes-v1" --platform tiktok --views 45000 --saves 1200
  python track_performance.py report --period weekly
  python track_performance.py top-formats --min-posts 5
  python track_performance.py export-winners  # Export top hooks for reuse
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PERFORMANCE_DB = SCRIPT_DIR / "performance_data.json"
PERFORMANCE_CSV = SCRIPT_DIR / "performance_data.csv"

CSV_HEADERS = [
    "post_id", "platform", "format", "hook", "source_blog",
    "posted_date", "views_24h", "views_48h", "views_7d",
    "saves", "shares", "comments", "profile_visits",
    "link_clicks", "downloads_estimate", "revenue_estimate",
    "notes"
]


def load_db() -> list[dict]:
    if PERFORMANCE_DB.exists():
        with open(PERFORMANCE_DB) as f:
            return json.load(f)
    return []


def save_db(data: list[dict]):
    with open(PERFORMANCE_DB, "w") as f:
        json.dump(data, f, indent=2)

    # Also maintain CSV for easy spreadsheet import
    with open(PERFORMANCE_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in data:
            writer.writerow(row)


def log_post(args):
    """Log performance data for a post."""
    db = load_db()

    # Check if updating existing entry
    existing = None
    for i, entry in enumerate(db):
        if entry["post_id"] == args.id and entry["platform"] == args.platform:
            existing = i
            break

    entry = {
        "post_id": args.id,
        "platform": args.platform,
        "format": args.format or "",
        "hook": args.hook or "",
        "source_blog": args.source_blog or "",
        "posted_date": args.date or datetime.now().strftime("%Y-%m-%d"),
        "views_24h": args.views_24h or 0,
        "views_48h": args.views_48h or 0,
        "views_7d": args.views or 0,
        "saves": args.saves or 0,
        "shares": args.shares or 0,
        "comments": args.comments or 0,
        "profile_visits": args.profile_visits or 0,
        "link_clicks": args.link_clicks or 0,
        "downloads_estimate": args.downloads or 0,
        "revenue_estimate": args.revenue or 0,
        "notes": args.notes or "",
        "updated_at": datetime.now().isoformat(),
    }

    if existing is not None:
        db[existing].update(entry)
        print(f"Updated: {args.id} on {args.platform}")
    else:
        db.append(entry)
        print(f"Logged: {args.id} on {args.platform}")

    save_db(db)


def generate_report(args):
    """Generate a performance report."""
    db = load_db()
    if not db:
        print("No performance data yet.")
        return

    period = args.period
    now = datetime.now()

    if period == "weekly":
        cutoff = now - timedelta(days=7)
    elif period == "monthly":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = datetime.min

    filtered = [
        e for e in db
        if datetime.fromisoformat(e.get("posted_date", "2020-01-01")) >= cutoff
    ]

    if not filtered:
        print(f"No posts in the last {period} period.")
        return

    # Aggregate stats
    total_views = sum(e.get("views_7d", 0) for e in filtered)
    total_saves = sum(e.get("saves", 0) for e in filtered)
    total_downloads = sum(e.get("downloads_estimate", 0) for e in filtered)
    total_revenue = sum(e.get("revenue_estimate", 0) for e in filtered)

    # Per-format breakdown
    format_stats = defaultdict(lambda: {"posts": 0, "views": 0, "saves": 0, "downloads": 0})
    for e in filtered:
        fmt = e.get("format", "unknown")
        format_stats[fmt]["posts"] += 1
        format_stats[fmt]["views"] += e.get("views_7d", 0)
        format_stats[fmt]["saves"] += e.get("saves", 0)
        format_stats[fmt]["downloads"] += e.get("downloads_estimate", 0)

    # Per-platform breakdown
    platform_stats = defaultdict(lambda: {"posts": 0, "views": 0, "saves": 0})
    for e in filtered:
        plat = e.get("platform", "unknown")
        platform_stats[plat]["posts"] += 1
        platform_stats[plat]["views"] += e.get("views_7d", 0)
        platform_stats[plat]["saves"] += e.get("saves", 0)

    # Top posts
    top_by_views = sorted(filtered, key=lambda x: x.get("views_7d", 0), reverse=True)[:5]
    top_by_saves = sorted(filtered, key=lambda x: x.get("saves", 0), reverse=True)[:5]

    print(f"\n{'='*60}")
    print(f"  PERFORMANCE REPORT — {period.upper()}")
    print(f"  {len(filtered)} posts | {cutoff.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    print(f"\n  TOTALS")
    print(f"  Views:     {total_views:>12,}")
    print(f"  Saves:     {total_saves:>12,}")
    print(f"  Downloads: {total_downloads:>12,}")
    print(f"  Revenue:   ${total_revenue:>11,.2f}")
    if total_views > 0:
        print(f"  Rev/1M:    ${(total_revenue / total_views * 1_000_000):>11,.2f}")
        print(f"  Save rate: {(total_saves / total_views * 100):>11.2f}%")

    print(f"\n  BY FORMAT")
    for fmt, stats in sorted(format_stats.items(), key=lambda x: x[1]["views"], reverse=True):
        avg_views = stats["views"] // max(stats["posts"], 1)
        print(f"  {fmt:<25} {stats['posts']:>3} posts | {stats['views']:>10,} views | avg {avg_views:>8,}")

    print(f"\n  BY PLATFORM")
    for plat, stats in sorted(platform_stats.items(), key=lambda x: x[1]["views"], reverse=True):
        print(f"  {plat:<15} {stats['posts']:>3} posts | {stats['views']:>10,} views | {stats['saves']:>6,} saves")

    print(f"\n  TOP 5 BY VIEWS")
    for e in top_by_views:
        print(f"  {e['views_7d']:>10,}  {e['post_id']} ({e['platform']})")

    print(f"\n  TOP 5 BY SAVES")
    for e in top_by_saves:
        print(f"  {e['saves']:>10,}  {e['post_id']} ({e['platform']})")

    print()


def top_formats(args):
    """Identify top-performing formats for scaling decisions."""
    db = load_db()
    min_posts = args.min_posts

    format_data = defaultdict(list)
    for e in db:
        fmt = e.get("format", "unknown")
        format_data[fmt].append(e)

    print(f"\nFORMAT PERFORMANCE (min {min_posts} posts)")
    print(f"{'Format':<25} {'Posts':>5} {'Avg Views':>12} {'Avg Saves':>10} {'Save %':>8} {'Action':>10}")
    print("-" * 75)

    for fmt, posts in sorted(format_data.items(), key=lambda x: sum(p.get("views_7d", 0) for p in x[1]) / len(x[1]), reverse=True):
        if len(posts) < min_posts:
            continue

        avg_views = sum(p.get("views_7d", 0) for p in posts) // len(posts)
        avg_saves = sum(p.get("saves", 0) for p in posts) // len(posts)
        save_rate = (avg_saves / max(avg_views, 1)) * 100

        # Decision logic
        if avg_views > 50000 and save_rate > 3:
            action = "SCALE UP"
        elif avg_views > 20000:
            action = "KEEP"
        elif len(posts) < 10:
            action = "TEST MORE"
        else:
            action = "RETIRE"

        print(f"{fmt:<25} {len(posts):>5} {avg_views:>12,} {avg_saves:>10,} {save_rate:>7.1f}% {action:>10}")


def export_winners(args):
    """Export top-performing hooks for reuse in future content generation."""
    db = load_db()
    if not db:
        print("No data yet.")
        return

    # Top hooks by views
    sorted_by_views = sorted(db, key=lambda x: x.get("views_7d", 0), reverse=True)
    top_hooks = []
    for e in sorted_by_views[:20]:
        if e.get("hook"):
            top_hooks.append({
                "hook": e["hook"],
                "format": e.get("format", ""),
                "views": e.get("views_7d", 0),
                "saves": e.get("saves", 0),
                "platform": e.get("platform", ""),
            })

    output_path = SCRIPT_DIR / "prompts" / "winning_hooks.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(top_hooks, f, indent=2)

    print(f"Exported {len(top_hooks)} winning hooks to {output_path}")
    print("These will be fed back to Claude as examples for future content generation.")


def main():
    parser = argparse.ArgumentParser(description="Track slideshow performance")
    subparsers = parser.add_subparsers(dest="command")

    # Log command
    log_parser = subparsers.add_parser("log", help="Log post performance")
    log_parser.add_argument("--id", required=True, help="Post/slideshow ID")
    log_parser.add_argument("--platform", required=True, choices=["tiktok", "instagram", "youtube"])
    log_parser.add_argument("--views", type=int, default=0, help="7-day views")
    log_parser.add_argument("--views-24h", type=int, default=0)
    log_parser.add_argument("--views-48h", type=int, default=0)
    log_parser.add_argument("--saves", type=int, default=0)
    log_parser.add_argument("--shares", type=int, default=0)
    log_parser.add_argument("--comments", type=int, default=0)
    log_parser.add_argument("--profile-visits", type=int, default=0)
    log_parser.add_argument("--link-clicks", type=int, default=0)
    log_parser.add_argument("--downloads", type=int, default=0)
    log_parser.add_argument("--revenue", type=float, default=0)
    log_parser.add_argument("--format", default="")
    log_parser.add_argument("--hook", default="")
    log_parser.add_argument("--source-blog", default="")
    log_parser.add_argument("--date", default="")
    log_parser.add_argument("--notes", default="")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate performance report")
    report_parser.add_argument("--period", choices=["weekly", "monthly", "all"], default="weekly")

    # Top formats command
    top_parser = subparsers.add_parser("top-formats", help="Identify top formats")
    top_parser.add_argument("--min-posts", type=int, default=5)

    # Export winners command
    subparsers.add_parser("export-winners", help="Export top hooks for reuse")

    args = parser.parse_args()

    if args.command == "log":
        log_post(args)
    elif args.command == "report":
        generate_report(args)
    elif args.command == "top-formats":
        top_formats(args)
    elif args.command == "export-winners":
        export_winners(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
