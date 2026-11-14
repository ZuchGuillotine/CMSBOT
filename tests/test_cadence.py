from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from cmsbot.hub import cadence, due
from cmsbot.hub.app import create_app
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store, parse_iso

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
DIST = Actor("grok", "distributor")
RULES = {"timezone": "America/Los_Angeles", "channels": {"x": {"times": ["13:00", "9:00"], "per_day": 2},
                                                           "linkedin": {"times": ["08:30"], "days": ["Mon", "wed", "fri"]}}}
MONDAY = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)  # 08:00 in Los Angeles


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.create_token("ben", "admin")
    h.upsert_project(ADMIN, "acme", "Acme")
    return h


def approved(hub, channel="x", when=None, **extra):
    item = hub.create_item(GEN, "acme", kind="post", channel=channel, title="T", body={"text": "words"}, **extra)
    hub.submit(GEN, item["id"])
    return hub.approve(ADMIN, item["id"], scheduled_at=when)


def test_cadence_is_cleaned_and_only_a_person_sets_it(hub):
    saved = cadence.set_cadence(hub, ADMIN, "acme", RULES)
    assert saved["channels"]["x"] == {"times": ["09:00", "13:00"], "per_day": 2, "days": list(cadence.DAYS)}
    assert saved["channels"]["linkedin"] == {"times": ["08:30"], "per_day": 1, "days": ["mon", "wed", "fri"]}
    with pytest.raises(HubError) as denied:
        cadence.set_cadence(hub, GEN, "acme", RULES)
    assert denied.value.status == 403
    for bad in ({"timezone": "Mars/Olympus"}, {"channels": {"x": {"times": ["25:00"]}}}, {"channels": {"myspace": {"times": ["09:00"]}}},
                {"channels": {"x": {"times": ["09:00"], "days": ["someday"]}}}):
        with pytest.raises(HubError):
            cadence.set_cadence(hub, ADMIN, "acme", bad)
    assert cadence.set_cadence(hub, ADMIN, "acme", {"channels": {"x": {"times": []}}})["channels"] == {}


def test_next_slot_is_local_time_skips_taken_times_full_days_and_days_off(hub):
    assert cadence.next_slot(hub, "acme", "x", now=MONDAY) is None  # no cadence: nothing changes
    cadence.set_cadence(hub, ADMIN, "acme", RULES)
    assert cadence.next_slot(hub, "acme", "x", now=MONDAY) == "2026-11-16T17:00:00Z"  # 09:00 Pacific
    assert cadence.next_slot(hub, "acme", "tiktok", now=MONDAY) is None  # a channel with no times
    approved(hub, when="2026-11-16T17:00:00Z")
    assert cadence.next_slot(hub, "acme", "x", now=MONDAY) == "2026-11-16T21:00:00Z"
    approved(hub, when="2026-11-16T21:00:00Z")
    assert cadence.next_slot(hub, "acme", "x", now=MONDAY) == "2026-11-17T17:00:00Z"  # Monday holds its two
    # 08:30 Monday is still ahead at 08:00; an hour later the next linkedin day is Wednesday
    assert cadence.next_slot(hub, "acme", "linkedin", now=MONDAY) == "2026-11-16T16:30:00Z"
    assert cadence.next_slot(hub, "acme", "linkedin", now=MONDAY.replace(hour=17)) == "2026-11-18T16:30:00Z"


def test_a_post_made_today_counts_towards_the_day(hub):
    cadence.set_cadence(hub, ADMIN, "acme", {"timezone": "UTC", "channels": {"x": {"times": ["10:00", "20:00"], "per_day": 1}}})
    item = approved(hub, when="2020-01-01T00:00:00Z")  # a past time means post now
    hub.claim(DIST, item["id"])
    hub.mark_posted(DIST, item["id"], post_url="https://x.com/p/1", post_id="1")
    posted_on = parse_iso(hub.get_item(ADMIN, item["id"])["publications"][0]["posted_at"])
    early = posted_on.replace(hour=0, minute=0, second=0)
    assert parse_iso(cadence.next_slot(hub, "acme", "x", now=early)).date() > posted_on.date()


def test_approval_takes_the_next_free_time_unless_the_reviewer_names_one(hub):
    assert approved(hub)["status"] == "approved"  # no cadence: due as soon as approved, as before
    cadence.set_cadence(hub, ADMIN, "acme", RULES)
    first, second = approved(hub), approved(hub)
    assert first["status"] == second["status"] == "scheduled"
    assert parse_iso(first["scheduled_at"]) > datetime.now(timezone.utc)
    assert second["scheduled_at"] > first["scheduled_at"]
    assert approved(hub, when="2027-01-01T12:00:00Z")["scheduled_at"] == "2027-01-01T12:00:00Z"
    assert approved(hub, channel="tiktok")["status"] == "approved"
    # the posting agent gets them in time order, and only what is due inside its window
    queue = [p["id"] for p in hub.ready_to_post(DIST, channel="x", due_within_hours=24 * 400)]
    assert queue.index(first["id"]) < queue.index(second["id"])
    assert first["id"] not in [p["id"] for p in hub.ready_to_post(DIST, channel="x", due_within_hours=0)]


def test_backlog_gets_times_in_turn_between_anchors_and_keeps_approval(hub):
    a = hub.import_published(ADMIN, "acme", url="https://acme.com/a", title="A", markdown="# A\n\n" + "Words here. " * 30)
    b = hub.import_published(ADMIN, "acme", url="https://acme.com/b", title="B", markdown="# B\n\n" + "Words here. " * 30)
    from_a = [approved(hub, parent_id=a["id"])["id"] for _ in range(3)]
    from_b = [approved(hub, parent_id=b["id"])["id"]]
    other = approved(hub, channel="tiktok")["id"]
    with pytest.raises(HubError):
        cadence.schedule_backlog(hub, GEN, "acme")
    cadence.set_cadence(hub, ADMIN, "acme", RULES)
    result = cadence.schedule_backlog(hub, ADMIN, "acme")
    assert [s["id"] for s in result["scheduled"]] == [from_a[0], from_b[0], from_a[1], from_a[2]]
    assert result["no_times_for_channel"] == [{"id": other, "channel": "tiktok"}]
    times = [s["scheduled_at"] for s in result["scheduled"]]
    assert times == sorted(times) and len(set(times)) == 4
    item = hub.get_item(ADMIN, from_a[0])
    assert item["status"] == "scheduled" and not item["changed_since_approval"]
    assert from_a[0] in [p["id"] for p in hub.ready_to_post(DIST, due_within_hours=24 * 400)]
    assert cadence.schedule_backlog(hub, ADMIN, "acme")["scheduled"] == []  # nothing left to place


def test_everyone_sees_the_cadence_and_a_dump_of_untimed_posts_is_flagged(hub):
    for _ in range(4):
        approved(hub)
    flagged = [t for t in due.whats_due(hub, ADMIN, "acme")["for_you"] if t["task"] == "cadence"]
    assert flagged and flagged[0]["count"] == 4
    cadence.set_cadence(hub, ADMIN, "acme", RULES)
    cadence.schedule_backlog(hub, ADMIN, "acme")
    seen = due.whats_due(hub, DIST, "acme")
    assert seen["cadence"]["channels"]["x"]["times"] == ["09:00", "13:00"]
    assert seen["cadence"]["queued"]["x"]["count"] == 4 and seen["cadence"]["queued"]["x"]["next_at"]
    assert not [t for t in seen["for_you"] + seen["for_others"] if t["task"] in ("cadence", "post")]


def test_cadence_over_http(hub):
    auth = {"Authorization": f"Bearer {hub.create_token('ben2', 'admin')}"}
    agent = {"Authorization": f"Bearer {hub.create_token('bot', 'generator')}"}
    approved_id = approved(hub, channel="tiktok")["id"]
    with TestClient(create_app(hub=hub, with_mcp=False)) as client:
        assert client.put("/api/projects/acme/cadence", json=RULES, headers=agent).status_code == 403
        saved = client.put("/api/projects/acme/cadence", json=RULES, headers=auth)
        assert saved.status_code == 200 and saved.json()["cadence"]["timezone"] == "America/Los_Angeles"
        assert client.post("/api/projects/acme/cadence/backlog", headers=agent).status_code == 403
        body = client.post("/api/projects/acme/cadence/backlog", headers=auth).json()
        assert body["scheduled"] == [] and body["no_times_for_channel"][0]["id"] == approved_id
        assert client.get("/api/projects/acme/due", headers=agent).json()["cadence"]["channels"]["x"]["per_day"] == 2
