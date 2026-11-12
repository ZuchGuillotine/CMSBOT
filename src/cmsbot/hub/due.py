"""What needs doing now. Scheduled agents ask this instead of following a calendar of their own, so a
missed run catches up on the next one and a stalled loop is visible instead of silent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import now_iso, parse_iso

SNAPSHOT_HOURS = (24, 72, 168)
DEFAULT_RHYTHM = {"plan_every_days": 7, "analyze_every_days": 7}
RUN_TASKS = ("plan", "analyze", "write", "distribute")


def rhythm(hub: Hub, project: str) -> dict[str, int]:
    saved = hub._project(project)["settings"].get("rhythm") or {}
    return {key: int(saved.get(key) or default) for key, default in DEFAULT_RHYTHM.items()}


def set_rhythm(hub: Hub, actor: Actor, project: str, values: dict[str, Any]) -> dict[str, int]:
    hub._manageable(actor, project)
    row = hub._project(project)
    clean = {}
    for key in DEFAULT_RHYTHM:
        try:
            clean[key] = max(1, min(90, int(values.get(key) or DEFAULT_RHYTHM[key])))
        except (TypeError, ValueError) as exc:
            raise HubError("Give a number of days.") from exc
    hub.db.update("projects", "slug", project, {"settings": {**row["settings"], "rhythm": clean}})
    return clean


def log_run(hub: Hub, actor: Actor, project: str, task: str, summary: str = "") -> dict[str, Any]:
    """Record that a planning, writing, posting or analysis run happened, with a line on what it did."""
    hub._scope(actor, project)
    hub._project(project)
    if task not in RUN_TASKS:
        raise HubError(f"task is one of: {', '.join(RUN_TASKS)}")
    hub.db.insert("events", {"item_id": None, "project": project, "actor": actor.name, "role": actor.role, "action": f"run:{task}",
                             "detail": {"summary": summary.strip()[:2000]}, "at": now_iso()})
    return {"project": project, "task": task, "recorded": True}


def _last_run(hub: Hub, project: str, task: str) -> dict[str, Any] | None:
    return hub.db.one("SELECT actor, detail, at FROM events WHERE project = ? AND action = ? ORDER BY id DESC LIMIT 1", (project, f"run:{task}"))


def _days_since(stamp: str | None, now: datetime) -> float | None:
    return round((now - parse_iso(stamp)).total_seconds() / 86400, 1) if stamp else None


def snapshots_due(hub: Hub, project: str, now: datetime) -> list[dict[str, Any]]:
    """Posts that have reached 24h, 72h or 7 days without a numbers snapshot near that age."""
    posts = hub.db.all(
        "SELECT p.id AS publication_id, p.item_id, p.channel, p.post_url, p.posted_at, i.title FROM publications p "
        "JOIN items i ON i.id = p.item_id WHERE i.project = ? AND p.posted_by != 'imported' AND p.channel NOT IN ('blog', 'email')", (project,))
    out = []
    for post in posts:
        age = (now - parse_iso(post["posted_at"])).total_seconds() / 3600
        have = [m["age_hours"] for m in hub.db.all("SELECT age_hours FROM metrics WHERE publication_id = ?", (post["publication_id"],))]
        for target in SNAPSHOT_HOURS:
            covered = any(0.75 * target <= a <= 2 * target for a in have)
            if covered or age < target:
                continue
            if age > 2 * target:
                if target == SNAPSHOT_HOURS[-1] and age <= 6 * target:  # the 7-day number is the one posts are compared on
                    out.append({"item_id": post["item_id"], "channel": post["channel"], "post_url": post["post_url"], "title": post["title"],
                                "snapshot": f"{target}h", "late": True, "note": "Past the window; report it anyway and it will be marked late."})
                continue
            out.append({"item_id": post["item_id"], "channel": post["channel"], "post_url": post["post_url"], "title": post["title"],
                        "snapshot": f"{target}h", "late": False, "note": ""})
    return out


def whats_due(hub: Hub, actor: Actor, project: str) -> dict[str, Any]:
    hub._scope(actor, project)
    hub._project(project)
    now = datetime.now(timezone.utc)
    pace = rhythm(hub, project)
    count = lambda status: (hub.db.one("SELECT COUNT(*) AS n FROM items WHERE project = ? AND status = ?", (project, status)) or {}).get("n", 0)  # noqa: E731
    tasks: list[dict[str, Any]] = []

    def add(task: str, who: str, n: int, detail: str, overdue_days: float | None = None, items: list[Any] | None = None) -> None:
        tasks.append({"task": task, "who": who, "count": n, "detail": detail, "overdue_days": overdue_days, "items": items or []})

    spend = hub.spend(actor, project)
    if spend["over_budget"]:
        add("budget", "reviewer", 1, f"This month's spend (${spend['total_usd']}) has reached the budget (${spend['monthly_budget_usd']}). Paid generation is stopped.")

    revise = hub.db.all("SELECT id, kind, channel, title FROM items WHERE project = ? AND status IN ('needs_revision', 'failed')", (project,))
    if revise:
        add("revise", "generator", len(revise), "Pieces a reviewer sent back, or that failed. Do these first.", items=[r["id"] for r in revise])
    if count("planned"):
        add("write", "generator", count("planned"), "Planned pieces waiting to be written (to_write in the brief).")

    oldest = hub.db.one("SELECT MIN(updated_at) AS at FROM items WHERE project = ? AND status = 'in_review'", (project,)) or {}
    if count("in_review"):
        waiting = _days_since(oldest.get("at"), now) or 0
        add("review", "reviewer", count("in_review"), f"Drafts waiting for review; the oldest has waited {waiting:g} day(s).",
            overdue_days=waiting if waiting > 3 else None)

    ready = hub.ready_to_post(Actor("due", "admin"), project=project, due_within_hours=0)
    if ready:
        add("post", "distributor", len(ready), "Approved and due now.", items=[r["id"] for r in ready])

    snapshots = snapshots_due(hub, project, now)
    if snapshots:
        add("metrics", "distributor", len(snapshots), "Posts that have reached a 24-hour, 72-hour or 7-day mark without their numbers reported.",
            overdue_days=1.0 if any(s["late"] for s in snapshots) else None, items=snapshots)

    last_plan = _last_run(hub, project, "plan")
    since_plan = _days_since((last_plan or {}).get("at"), now)
    pipeline = count("planned") + count("drafting") + count("in_review") + count("approved") + count("scheduled")
    if since_plan is None and pipeline == 0:
        add("plan", "generator", 1, "Nothing is planned and no planning run has been recorded.")
    elif since_plan is not None and since_plan >= pace["plan_every_days"]:
        add("plan", "generator", 1, f"The last planning run was {since_plan:g} day(s) ago; the rhythm is every {pace['plan_every_days']}.",
            overdue_days=round(since_plan - pace["plan_every_days"], 1))

    measured = (hub.db.one("SELECT COUNT(*) AS n FROM publications p JOIN items i ON i.id = p.item_id WHERE i.project = ? AND p.posted_by != 'imported'",
                           (project,)) or {}).get("n", 0)
    last_analysis = _last_run(hub, project, "analyze")
    since_analysis = _days_since((last_analysis or {}).get("at"), now)
    if measured and (since_analysis is None or since_analysis >= pace["analyze_every_days"]):
        add("analyze", "analyst", 1,
            "No analysis run has been recorded since posting began." if since_analysis is None else
            f"The last analysis was {since_analysis:g} day(s) ago; the rhythm is every {pace['analyze_every_days']}.",
            overdue_days=None if since_analysis is None else round(since_analysis - pace["analyze_every_days"], 1))

    proposals = (hub.db.one("SELECT COUNT(*) AS n FROM learnings WHERE project = ? AND status = 'proposed'", (project,)) or {}).get("n", 0)
    skill_proposals = (hub.db.one("SELECT COUNT(*) AS n FROM skills WHERE project IN ('', ?) AND status = 'proposed'", (project,)) or {}).get("n", 0)
    if proposals or skill_proposals:
        add("decide", "reviewer", proposals + skill_proposals,
            f"{proposals} proposed learning(s) and {skill_proposals} proposed skill change(s) waiting for a decision.")

    mine = {"generator": ("generator",), "distributor": ("distributor",), "analyst": ("analyst",), "admin": ("reviewer",)}[actor.role]
    return {
        "project": project, "checked_at": now_iso(), "rhythm": pace,
        "for_you": [t for t in tasks if t["who"] in mine], "for_others": [t for t in tasks if t["who"] not in mine],
        "last_runs": {task: ({"at": run["at"], "by": run["actor"], "days_ago": _days_since(run["at"], now), "summary": run["detail"].get("summary", "")}
                             if (run := _last_run(hub, project, task)) else None) for task in RUN_TASKS},
        "nothing_due": not tasks,
    }
