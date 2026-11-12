from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cmsbot.hub import due, skills
from cmsbot.hub.app import create_app
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
DIST = Actor("grok", "distributor")
ANALYST = Actor("ana", "analyst")
BODY = "# Method\n\nDo the thing carefully, in this order, and say what you did."


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.create_token("ben", "admin")
    skills.seed(h)
    h.upsert_project(ADMIN, "acme", "Acme")
    return h


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_builtin_skills_are_seeded_once_and_start_comes_first(hub):
    names = [s["name"] for s in skills.listing(hub, GEN)]
    assert names[0] == "cmsbot-start" and {"cmsbot-write", "cmsbot-plan", "cmsbot-analyze", "cmsbot-distribute"} <= set(names)
    skills.seed(hub)
    assert [s["version"] for s in skills.listing(hub, GEN)] == [1] * len(names)
    got = skills.get(hub, GEN, "cmsbot-write")
    assert got["stamp"] == "cmsbot-write@1" and "check_draft" in got["body"] and "cmsbot-write@1" in got["use"]


def test_upgrade_refreshes_untouched_builtins_but_not_edited_ones(hub, monkeypatch):
    skills.save(hub, ADMIN, name="cmsbot-plan", description="Edited", body=BODY)
    monkeypatch.setattr(skills, "builtin", lambda: [{"name": "cmsbot-plan", "description": "New", "body": BODY + " v2"},
                                                    {"name": "cmsbot-write", "description": "New", "body": BODY + " v2"}])
    skills.seed(hub)
    assert skills.get(hub, GEN, "cmsbot-plan")["description"] == "Edited"
    assert skills.get(hub, GEN, "cmsbot-write")["version"] == 2 and skills.get(hub, GEN, "cmsbot-write")["body"].endswith("v2")


def test_project_version_overrides_and_continues_numbering(hub):
    hub.upsert_project(ADMIN, "other", "Other")
    saved = skills.save(hub, ADMIN, name="cmsbot-write", description="Acme's way", body=BODY, project="acme", note="tighter")
    assert saved["stamp"] == "cmsbot-write@2" and saved["scope"] == "acme"
    assert skills.get(hub, GEN, "cmsbot-write", "other")["stamp"] == "cmsbot-write@1"
    skills.retire(hub, ADMIN, "cmsbot-write", "acme")
    assert skills.get(hub, GEN, "cmsbot-write", "acme")["scope"] == "hub"
    with pytest.raises(HubError):
        skills.save(hub, GEN, name="x-skill", description="d", body=BODY)
    with pytest.raises(HubError):
        skills.save(hub, Actor("dana", "admin", "other"), name="cmsbot-write", description="d", body=BODY, project="acme")
    for bad in ({"name": "Bad Name", "description": "d", "body": BODY}, {"name": "ok-name", "description": "", "body": BODY},
                {"name": "ok-name", "description": "d", "body": "short"}):
        with pytest.raises(HubError):
            skills.save(hub, ADMIN, **bad)


def test_proposed_change_needs_a_person_and_carries_cautions(hub):
    current = skills.get(hub, ANALYST, "cmsbot-write", "acme")
    with pytest.raises(HubError):
        skills.propose(hub, ANALYST, name="cmsbot-write", body=current["body"] + "\nNew rule.", rationale="", project="acme")
    made = skills.propose(hub, ANALYST, name="cmsbot-write", body=current["body"] + "\nNew rule.", project="acme",
                          rationale="Threads opening with a number did better on 6 posts.")
    assert any("No measured posts carry the stamp cmsbot-write@1" in c for c in made["caveats"])
    assert skills.get(hub, GEN, "cmsbot-write", "acme")["version"] == 1  # nothing changed yet
    with pytest.raises(HubError) as second:
        skills.propose(hub, ANALYST, name="cmsbot-write", body=current["body"] + "\nOther.", rationale="again", project="acme")
    assert second.value.status == 409
    assert next(s for s in skills.listing(hub, ADMIN, "acme") if s["name"] == "cmsbot-write")["proposals_waiting"] == 1
    assert due.whats_due(hub, ADMIN, "acme")["for_you"][-1]["task"] == "decide"
    with pytest.raises(HubError):
        skills.decide(hub, ANALYST, made["proposal_id"], True)
    skills.decide(hub, ADMIN, made["proposal_id"], True)
    accepted = skills.get(hub, GEN, "cmsbot-write", "acme")
    assert accepted["stamp"] == "cmsbot-write@2" and accepted["body"].endswith("New rule.")
    history = skills.detail(hub, ADMIN, "cmsbot-write", "acme")["history"]
    assert [h["status"] for h in history] == ["active"]
    hub_wide = skills.propose(hub, ANALYST, name="cmsbot-plan", body=BODY, rationale="Seems better.")
    assert any("every project" in c for c in hub_wide["caveats"])
    skills.decide(hub, ADMIN, hub_wide["proposal_id"], False)
    assert skills.get(hub, GEN, "cmsbot-plan")["version"] == 1


def test_restore_makes_an_earlier_version_current(hub):
    skills.save(hub, ADMIN, name="house-style", description="d", body=BODY + " one")
    skills.save(hub, ADMIN, name="house-style", description="d", body=BODY + " two")
    first = next(h for h in skills.detail(hub, ADMIN, "house-style")["history"] if h["version"] == 1)
    restored = skills.restore(hub, ADMIN, first["id"])
    assert restored["version"] == 3 and restored["body"].endswith("one")


def test_parse_and_render_round_trip():
    text = "---\nname: reddit-replies\ndescription: \"Use when answering.\"\n---\n\n# Reddit\n\nAnswer first."
    parsed = skills.parse(text)
    assert parsed == {"name": "reddit-replies", "description": "Use when answering.", "body": "# Reddit\n\nAnswer first."}
    assert skills.parse(skills.render(parsed)) == parsed
    assert skills.parse("no front matter")["name"] == ""


def test_whats_due_follows_the_work(hub):
    start = due.whats_due(hub, GEN, "acme")
    assert [t["task"] for t in start["for_you"]] == ["plan"] and start["last_runs"]["plan"] is None
    due.log_run(hub, GEN, "acme", "plan", "Planned the week.")
    item = hub.create_item(GEN, "acme", kind="post", channel="x", title="P", body={"text": "hello"})
    planned = hub.create_item(GEN, "acme", kind="post", channel="x", title="Later", body={})
    assert [t["task"] for t in due.whats_due(hub, GEN, "acme")["for_you"]] == ["write"]
    hub.submit(GEN, item["id"])
    assert due.whats_due(hub, ADMIN, "acme")["for_you"][0]["task"] == "review"
    hub.approve(ADMIN, item["id"])
    posting = due.whats_due(hub, DIST, "acme")
    assert [(t["task"], t["items"]) for t in posting["for_you"]] == [("post", [item["id"]])]
    hub.mark_posted(DIST, item["id"], post_url="https://x.com/1", posted_at=iso(3.2))
    metrics = due.whats_due(hub, DIST, "acme")["for_you"][0]
    assert metrics["task"] == "metrics" and [s["snapshot"] for s in metrics["items"]] == ["72h"]  # 24h window has passed
    hub.report_metrics(DIST, item["id"], {"impressions": 100, "likes": 3}, measured_at=iso(0.1))
    assert due.whats_due(hub, DIST, "acme")["for_you"] == []
    assert [t["task"] for t in due.whats_due(hub, ANALYST, "acme")["for_you"]] == ["analyze"]
    due.log_run(hub, ANALYST, "acme", "analyze", "Read the report.")
    assert due.whats_due(hub, ANALYST, "acme")["for_you"] == []
    hub.request_changes(ADMIN, planned["id"], "Not needed") if False else None
    with pytest.raises(HubError):
        due.log_run(hub, GEN, "acme", "dance")


def test_overdue_runs_and_late_seven_day_numbers(hub):
    hub.db.insert("events", {"item_id": None, "project": "acme", "actor": "claude", "role": "generator", "action": "run:plan",
                             "detail": {"summary": "old"}, "at": iso(10)})
    plan = due.whats_due(hub, GEN, "acme")["for_you"][0]
    assert plan["task"] == "plan" and plan["overdue_days"] == pytest.approx(3, abs=0.2)
    due.set_rhythm(hub, ADMIN, "acme", {"plan_every_days": 14, "analyze_every_days": 30})
    assert due.whats_due(hub, GEN, "acme")["for_you"] == []
    item = hub.create_item(GEN, "acme", kind="post", channel="x", title="P", body={"text": "hello"})
    hub.submit(GEN, item["id"])
    hub.approve(ADMIN, item["id"])
    hub.mark_posted(DIST, item["id"], posted_at=iso(20))
    late = due.whats_due(hub, DIST, "acme")["for_you"][0]
    assert late["items"][0]["snapshot"] == "168h" and late["items"][0]["late"] is True and late["overdue_days"] is not None
    anchor = hub.import_published(ADMIN, "acme", url="https://acme.com/g", title="G", markdown="# G\n\n" + "text " * 80)
    assert all(s["item_id"] != anchor["id"] for s in due.snapshots_due(hub, "acme", datetime.now(timezone.utc)))
    with pytest.raises(HubError):
        due.set_rhythm(hub, GEN, "acme", {})


def test_skills_and_due_over_http_and_mcp(hub):
    admin, agent = hub.create_token("r", "admin"), hub.create_token("a", "generator")
    with TestClient(create_app(hub=hub)) as client:
        h = {"Authorization": f"Bearer {admin}"}
        up = client.post("/api/skills", headers=h, json={"project": "acme", "text": "---\nname: reddit-replies\ndescription: Use for subreddit answers.\n---\n\n" + BODY})
        assert up.status_code == 200 and up.json()["stamp"] == "reddit-replies@1"
        assert client.post("/api/skills", headers={"Authorization": f"Bearer {agent}"}, json={"text": "---\nname: x-y\ndescription: d\n---\n" + BODY}).status_code == 403
        assert client.get("/api/skills/reddit-replies?project=acme&manage=true", headers=h).json()["active"]["version"] == 1
        assert client.put("/api/projects/acme/rhythm", headers=h, json={"plan_every_days": 5}).json()["rhythm"]["plan_every_days"] == 5

        def call(tool, **arguments):
            response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
                                   headers={"Authorization": f"Bearer {agent}", "Accept": "application/json, text/event-stream"})
            return response.json()["result"]

        listed = call("list_skills", project="acme")["structuredContent"]["result"]
        assert listed[0]["name"] == "cmsbot-start" and any(s["name"] == "reddit-replies" for s in listed)
        assert "First three calls" in call("get_skill", name="cmsbot-start")["structuredContent"]["body"]
        assert call("whats_due", project="acme")["structuredContent"]["for_you"][0]["task"] == "plan"
        assert call("log_run", project="acme", task="plan", summary="done")["structuredContent"]["recorded"] is True
