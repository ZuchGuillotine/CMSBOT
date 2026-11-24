"""Performance analysis that tries not to fool itself.

Four guards, each against a specific way content analytics goes wrong:
  same age       a post is judged on the snapshot nearest a fixed age (default 7 days); younger posts wait,
                 so this week's posts never look worse than last month's just because they are new
  local baseline each post is compared with the median of other posts on the same channel within +/-28 days,
                 so account growth, seasonality and channel differences don't pass as content effects
  shrinkage      an attribute value's average is pulled toward "no effect" until it has enough posts
  exploration    the suggested mix keeps a floor for every option, so an early unlucky run isn't abandoned
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any

from cmsbot.hub.store import parse_iso

METRIC_FIELDS = (
    "impressions", "reach", "views", "likes", "comments", "saves", "shares", "clicks", "follows",
    "watch_seconds", "avg_watch_pct",
)
ENGAGEMENT_FIELDS = ("likes", "comments", "saves", "shares")
MIN_POSTS = 5           # below this an attribute value is reported as "not enough data"
PRIOR_STRENGTH = 4.0    # pseudo-posts at "no effect" used for shrinkage
BASELINE_WINDOW_DAYS = 28
MIN_BASELINE_POSTS = 3
EXPLORATION_FLOOR = 0.10
INTERVAL_Z = 1.64       # 90% interval
EPS = 1e-4
MIN_NOISE = 0.15      # never claim more certainty than about +/-15% per post
SKIP_DIMENSIONS = {"channel", "account"}  # lift is already relative to the channel


def pick_snapshot(snapshots: list[dict[str, Any]], age_hours: float) -> dict[str, Any] | None:
    """Nearest snapshot between 0.75x and 2x the target age; metrics are cumulative, so earlier ones are unfair."""
    eligible = [s for s in snapshots if 0.75 * age_hours <= s["age_hours"] <= 2 * age_hours]
    return min(eligible, key=lambda s: abs(s["age_hours"] - age_hours)) if eligible else None


def engagement_rate(data: dict[str, float]) -> float | None:
    base = data.get("reach") or data.get("impressions") or data.get("views")
    if not base:
        return None
    return sum(data.get(field, 0.0) for field in ENGAGEMENT_FIELDS) / base


def _baseline(post: dict[str, Any], peers: list[dict[str, Any]]) -> float | None:
    others = [p for p in peers if p["item_id"] != post["item_id"]]
    near = [p for p in others if abs((p["when"] - post["when"]).days) <= BASELINE_WINDOW_DAYS]
    pool = near if len(near) >= MIN_BASELINE_POSTS else others
    if len(pool) < MIN_BASELINE_POSTS:
        return None
    return statistics.median(p["rate"] for p in pool)


def analyse(posts: list[dict[str, Any]], *, age_hours: float = 168, dimensions: list[str] | None = None,
            seed: int | None = None) -> dict[str, Any]:
    measured, waiting, unusable = [], 0, 0
    for post in posts:
        snapshot = pick_snapshot(post["snapshots"], age_hours)
        if snapshot is None:
            waiting += 1
            continue
        rate = engagement_rate(snapshot["data"])
        if rate is None:
            unusable += 1
            continue
        measured.append({**post, "rate": rate, "data": snapshot["data"], "when": parse_iso(post["posted_at"])})

    by_channel: dict[str, list[dict[str, Any]]] = {}
    for post in measured:
        by_channel.setdefault(post["dims"]["channel"], []).append(post)

    comparable = []
    for channel_posts in by_channel.values():
        for post in channel_posts:
            baseline = _baseline(post, channel_posts)
            if baseline is not None:
                post["lift"] = math.log((post["rate"] + EPS) / (baseline + EPS))
                comparable.append(post)

    channels = []
    for channel, channel_posts in sorted(by_channel.items()):
        cost = sum(p["cost_usd"] for p in channel_posts)
        signups = sum(p["signups"] for p in channel_posts)
        channels.append({
            "channel": channel, "posts": len(channel_posts),
            "median_engagement_rate": round(statistics.median(p["rate"] for p in channel_posts), 5),
            "sessions": sum(p["sessions"] for p in channel_posts), "signups": signups,
            "cost_usd": round(cost, 2), "cost_per_signup_usd": round(cost / signups, 2) if signups else None,
        })

    spread = statistics.pstdev([p["lift"] for p in comparable]) if len(comparable) >= 2 else 0.0
    names = dimensions or sorted({k for p in comparable for k in p["dims"]} - SKIP_DIMENSIONS)
    rng = random.Random(seed)
    breakdown: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        groups: dict[str, list[float]] = {}
        for post in comparable:
            value = post["dims"].get(name)
            if value:
                groups.setdefault(value, []).append(post["lift"])
        if len(groups) < 2:
            continue
        # Noise is the spread of posts around their own group's average, not the spread between groups.
        residuals = [lift - sum(lifts) / len(lifts) for lifts in groups.values() if len(lifts) > 1 for lift in lifts]
        freedom = len(residuals) - sum(1 for lifts in groups.values() if len(lifts) > 1)
        noise = math.sqrt(sum(r * r for r in residuals) / freedom) if freedom > 0 else (spread or 1.0)
        noise = max(noise, MIN_NOISE)
        rows = []
        for value, lifts in groups.items():
            n = len(lifts)
            shrunk = sum(lifts) / (n + PRIOR_STRENGTH)
            error = noise / math.sqrt(n + PRIOR_STRENGTH)
            low, high = shrunk - INTERVAL_Z * error, shrunk + INTERVAL_Z * error
            if n < MIN_POSTS:
                verdict = "not enough data"
            elif low > 0:
                verdict = "above baseline"
            elif high < 0:
                verdict = "below baseline"
            else:
                verdict = "no clear difference"
            rows.append({"value": value, "posts": n, "typical_multiple": round(math.exp(shrunk), 2),
                         "range": [round(math.exp(low), 2), round(math.exp(high), 2)], "verdict": verdict,
                         "_mean": shrunk, "_error": error})
        wins = dict.fromkeys(groups, 0)
        draws = 2000
        for _ in range(draws):
            best = max(rows, key=lambda r: rng.gauss(r["_mean"], r["_error"]))
            wins[best["value"]] += 1
        floor = min(EXPLORATION_FLOOR, 1 / len(rows))
        for row in rows:
            share = wins[row["value"]] / draws
            row["suggested_share"] = round(floor + (1 - floor * len(rows)) * share, 2)
            del row["_mean"], row["_error"]
        breakdown[name] = sorted(rows, key=lambda r: -r["suggested_share"])

    caveats = []
    if waiting:
        caveats.append(f"{waiting} post(s) have no snapshot near {age_hours:g}h yet and are left out until they do.")
    if unusable:
        caveats.append(f"{unusable} post(s) have no reach, impressions or views, so no rate can be computed.")
    if len(measured) - len(comparable):
        caveats.append(f"{len(measured) - len(comparable)} post(s) are on a channel with fewer than "
                       f"{MIN_BASELINE_POSTS + 1} measured posts, so they have no baseline to compare against.")
    if len(comparable) < 20:
        caveats.append("Fewer than 20 comparable posts: treat every difference as a hypothesis, not a finding.")
    return {
        "age_hours": age_hours, "posts_total": len(posts), "posts_measured": len(measured),
        "posts_comparable": len(comparable), "channels": channels, "by_attribute": breakdown, "caveats": caveats,
        "how_to_read": "typical_multiple is engagement relative to that channel's recent median (1.0 = typical), "
                       "shrunk toward 1.0 when there are few posts. Act only on 'above baseline' / 'below baseline'. "
                       "suggested_share is a mix for the next period that still tests every option.",
    }
