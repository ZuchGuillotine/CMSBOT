"""When approved posts go out. A project can give each channel its daily posting times. An item approved
without a time then takes the next free one, so the posting agent finds a spread-out queue instead of
everything due at once. A channel with no times keeps the old behaviour: approved means due now.

Only a person sets the cadence or reschedules; agents read it in `whats_due`."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cmsbot.hub.service import CHANNELS, Actor, Hub, HubError
from cmsbot.hub.store import now_iso, parse_iso

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_TIMES = 12
LOOKAHEAD_DAYS = 180
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def get(hub: Hub, project: str) -> dict[str, Any]:
    return hub._project(project)["settings"].get("cadence") or {}


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HubError(f"'{name}' is not a time zone name. Use one like America/Los_Angeles or UTC.") from exc


def _clock(value: Any) -> str:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise HubError(f"'{value}' is not a time of day. Write times as HH:MM on a 24-hour clock, for example 09:00.")
    return f"{int(match[1]):02d}:{match[2]}"


def clean(values: dict[str, Any]) -> dict[str, Any]:
    """{"timezone": "America/Los_Angeles", "channels": {"x": {"times": ["09:00", "13:00"], "per_day": 2,
    "days": ["mon", ...]}}} -> the same, validated. A channel with no times is left out."""
    zone = str(values.get("timezone") or "UTC").strip()
    _zone(zone)
    channels: dict[str, Any] = {}
    for channel, rule in (values.get("channels") or {}).items():
        if channel not in CHANNELS:
            raise HubError(f"'{channel}' is not a channel. Channels: {', '.join(CHANNELS)}.")
        rule = rule if isinstance(rule, dict) else {"times": rule}
        times = sorted({_clock(t) for t in rule.get("times") or []})
        if not times:
            continue
        if len(times) > MAX_TIMES:
            raise HubError(f"At most {MAX_TIMES} times a day for one channel.")
        try:
            per_day = int(rule.get("per_day") or len(times))
        except (TypeError, ValueError) as exc:
            raise HubError("per_day is a whole number of posts.") from exc
        wanted = {str(d).strip().lower()[:3] for d in rule.get("days") or DAYS}
        unknown = wanted - set(DAYS)
        if unknown:
            raise HubError(f"Days are {', '.join(DAYS)}; got {', '.join(sorted(unknown))}.")
        channels[channel] = {"times": times, "per_day": max(1, min(per_day, len(times))), "days": [d for d in DAYS if d in wanted]}
    return {"timezone": zone, "channels": channels}


def set_cadence(hub: Hub, actor: Actor, project: str, values: dict[str, Any]) -> dict[str, Any]:
    hub._manageable(actor, project)
    row = hub._project(project)
    saved = clean(values)
    hub.db.update("projects", "slug", project, {"settings": {**row["settings"], "cadence": saved}})
    return saved


def next_slot(hub: Hub, project: str, channel: str, *, now: datetime | None = None) -> str | None:
    """The next free posting time for this channel, or None when the channel has no times set.
    A day is full once it holds `per_day` posts, counting what is scheduled and what was already posted."""
    rules = get(hub, project)
    rule = (rules.get("channels") or {}).get(channel)
    if not rule:
        return None
    zone = _zone(rules.get("timezone") or "UTC")
    now = now or datetime.now(timezone.utc)
    queued = [parse_iso(r["scheduled_at"]) for r in hub.db.all(
        "SELECT scheduled_at FROM items WHERE project = ? AND channel = ? AND scheduled_at IS NOT NULL "
        "AND status IN ('scheduled', 'posting')", (project, channel))]
    posted = [parse_iso(r["posted_at"]) for r in hub.db.all(
        "SELECT p.posted_at FROM publications p JOIN items i ON i.id = p.item_id "
        "WHERE i.project = ? AND p.channel = ? AND p.posted_by != 'imported'", (project, channel))]
    per_day = Counter(moment.astimezone(zone).date() for moment in queued + posted)
    taken = {moment.strftime(STAMP) for moment in queued}
    today = now.astimezone(zone).date()
    for offset in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=offset)
        if DAYS[day.weekday()] not in rule["days"] or per_day[day] >= rule["per_day"]:
            continue
        for clock in rule["times"]:
            hour, minute = (int(part) for part in clock.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
            if slot > now and slot.strftime(STAMP) not in taken:
                return slot.strftime(STAMP)
    return None


def schedule_backlog(hub: Hub, actor: Actor, project: str) -> dict[str, Any]:
    """Give a time to every approved item that has none, oldest approval first, taking turns between
    anchors so one guide does not fill a week. Approval is untouched: a time is not part of the copy."""
    hub._manageable(actor, project)
    hub._project(project)
    waiting = hub.db.all("SELECT * FROM items WHERE project = ? AND status = 'approved' AND scheduled_at IS NULL "
                         "ORDER BY approved_at, id", (project,))
    lanes: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for item in waiting:
        lanes.setdefault((item["channel"], item["parent_id"] or item["id"]), []).append(item)
    ordered: list[dict[str, Any]] = []
    while any(lanes.values()):
        for key in list(lanes):
            if lanes[key]:
                ordered.append(lanes[key].pop(0))
    scheduled, left = [], []
    for item in ordered:
        slot = next_slot(hub, project, item["channel"])
        if not slot:
            left.append({"id": item["id"], "channel": item["channel"]})
            continue
        hub.db.update("items", "id", item["id"], {"status": "scheduled", "scheduled_at": slot, "updated_at": now_iso()})
        hub._event(actor, "scheduled", item=item, detail={"scheduled_at": slot, "by": "cadence"})
        scheduled.append({"id": item["id"], "channel": item["channel"], "scheduled_at": slot})
    return {"project": project, "scheduled": sorted(scheduled, key=lambda s: s["scheduled_at"]), "no_times_for_channel": left}


def outlook(hub: Hub, project: str) -> dict[str, Any]:
    """What `whats_due` shows every role: the cadence in force and what is queued behind it."""
    rows = hub.db.all("SELECT channel, MIN(scheduled_at) AS next_at, COUNT(*) AS n FROM items WHERE project = ? "
                      "AND status = 'scheduled' AND scheduled_at IS NOT NULL GROUP BY channel", (project,))
    return {**get(hub, project), "queued": {r["channel"]: {"count": r["n"], "next_at": r["next_at"]} for r in rows}}
