import json
from datetime import datetime, timedelta, timezone

import pytest

from cmsbot.hub import stats
from cmsbot.hub.push import plan_asset_set
from cmsbot.hub.service import Actor, Hub, HubError, tracked_link
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
DIST = Actor("grok", "distributor")
ANALYST = Actor("analyst", "analyst")


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.upsert_project(ADMIN, "micros", "Micros", settings={"monthly_budget_usd": 10})
    h.upsert_pillar(ADMIN, "micros", "sleep", "Sleep")
    return h


def draft(hub, **extra):
    values = {"kind": "thread", "channel": "x", "title": "T", "body": {"posts": ["one", "two"]}}
    values.update(extra)
    return hub.create_item(GEN, "micros", **values)


def test_tokens_authenticate_and_revoke(hub):
    token = hub.create_token("grok", "distributor", "micros")
    actor = hub.authenticate(token)
    assert (actor.role, actor.project) == ("distributor", "micros")
    assert hub.authenticate("nope") is None
    hub.revoke_token(hub.list_tokens()[0]["id"])
    assert hub.authenticate(token) is None


def test_agents_cannot_approve_or_edit_after_submitting(hub):
    item = draft(hub)
    assert item["status"] == "drafting"
    hub.submit(GEN, item["id"])
    with pytest.raises(HubError) as denied:
        hub.approve(GEN, item["id"])
    assert denied.value.status == 403
    with pytest.raises(HubError) as locked:
        hub.update_item(GEN, item["id"], body={"posts": ["changed"]})
    assert locked.value.status == 409


def test_posting_agent_sees_only_approved_copy(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    assert hub.ready_to_post(DIST) == []
    with pytest.raises(HubError):
        hub.list_items(DIST)
    with pytest.raises(HubError):
        hub.get_item(DIST, item["id"])
    with pytest.raises(HubError):
        hub.claim(DIST, item["id"])
    hub.approve(ADMIN, item["id"])
    assert [i["id"] for i in hub.ready_to_post(DIST)] == [item["id"]]


def test_reviewer_edits_and_approves_in_one_step(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    approved = hub.edit_and_approve(ADMIN, item["id"], body={"posts": ["one", "two, fixed"]})
    assert approved["status"] == "approved"
    assert approved["changed_since_approval"] is False
    assert hub.ready_to_post(DIST)[0]["body"]["posts"][1] == "two, fixed"
    assert [r["role"] for r in approved["revisions"]] == ["admin", "generator"]


def test_editing_approved_copy_withdraws_approval(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    hub.approve(ADMIN, item["id"])
    edited = hub.update_item(ADMIN, item["id"], title="New title")
    assert edited["status"] == "in_review"
    assert hub.ready_to_post(DIST) == []


def test_claim_is_exclusive_and_post_is_recorded(hub):
    item = draft(hub, link="https://example.com/a?x=1")
    hub.submit(GEN, item["id"])
    hub.approve(ADMIN, item["id"])
    payload = hub.claim(DIST, item["id"])
    assert "utm_content=cmsbot-%d" % item["id"] in payload["link"]
    with pytest.raises(HubError):
        hub.claim(Actor("other", "distributor"), item["id"])
    hub.mark_posted(DIST, item["id"], post_url="https://x.com/p/1", post_id="1")
    assert hub.get_item(ADMIN, item["id"])["status"] == "published"
    assert hub.ready_to_post(DIST) == []


def test_stale_claim_is_released(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    hub.approve(ADMIN, item["id"])
    hub.claim(DIST, item["id"])
    hub.db.update("items", "id", item["id"], {"claimed_at": "2020-01-01T00:00:00Z"})
    assert hub.claim(Actor("other", "distributor"), item["id"])["id"] == item["id"]


def test_scheduled_items_wait_until_due(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert hub.approve(ADMIN, item["id"], scheduled_at=later)["status"] == "scheduled"
    assert hub.ready_to_post(DIST) == []
    assert len(hub.ready_to_post(DIST, due_within_hours=24 * 4)) == 1


def test_gaps_and_claims_block_approval(hub):
    hub.upsert_project(ADMIN, "micros", "Micros", settings={"require_verified_claims": True})
    anchor = draft(hub, kind="blog", channel="blog", body={"markdown": "x"}, gaps=["[dose]"])
    hub.set_claims(GEN, anchor["id"], [{"text": "A"}, {"text": "B"}])
    child = draft(hub, parent_id=anchor["id"], body={"posts": ["p"], "claim_ids": ["L2"]})
    hub.submit(GEN, anchor["id"])
    hub.submit(GEN, child["id"])
    assert len(hub.get_item(ADMIN, anchor["id"])["blockers"]) == 3
    assert hub.get_item(ADMIN, child["id"])["blockers"] == ["Claim not verified: B"]
    claims = hub.get_item(ADMIN, anchor["id"])["claims"]
    hub.review_claim(ADMIN, claims[1]["id"], status="verified")
    assert hub.approve(ADMIN, child["id"])["status"] == "approved"
    with pytest.raises(HubError):
        hub.approve(ADMIN, anchor["id"])
    hub.review_claim(ADMIN, claims[0]["id"], status="verified")
    assert hub.edit_and_approve(ADMIN, anchor["id"], gaps=[])["status"] == "approved"


def test_request_changes_returns_item_to_agent_with_note(hub):
    item = draft(hub)
    hub.submit(GEN, item["id"])
    hub.request_changes(ADMIN, item["id"], "Lead with the trial result.")
    brief = hub.brief(GEN, "micros")
    assert brief["needs_revision"][0]["review_notes"] == "Lead with the trial result."
    revised = hub.update_item(GEN, item["id"], body={"posts": ["trial result", "two"]})
    assert revised["status"] == "drafting"


def test_external_ref_makes_push_idempotent(hub):
    first = draft(hub, external_ref="set:thread:1")
    again = draft(hub, external_ref="set:thread:1", title="T2")
    assert again["id"] == first["id"] and again["title"] == "T2"
    hub.submit(GEN, first["id"])
    untouched = draft(hub, external_ref="set:thread:1", title="T3")
    assert untouched["title"] == "T2"


def test_project_scoped_token_cannot_cross_projects(hub):
    hub.upsert_project(ADMIN, "haidaa", "Haidaa")
    scoped = Actor("claude", "generator", "haidaa")
    with pytest.raises(HubError) as denied:
        hub.create_item(scoped, "micros", kind="post", channel="x", title="t", body={"text": "x"})
    assert denied.value.status == 403


def test_costs_roll_up_against_budget(hub):
    hub.record_cost(GEN, "micros", amount_usd=4, category="video", vendor="seedance")
    spend = hub.record_cost(GEN, "micros", amount_usd=7, category="llm")
    assert spend["total_usd"] == 11 and spend["over_budget"] is True
    assert spend["by_category"] == {"video": 4, "llm": 7}


def test_signals_feed_the_brief_and_clusters(hub):
    signal = hub.submit_signal(DIST, "micros", source="grok-trends", topic="magnesium threonate", channel="x", score=0.8)
    assert hub.brief(GEN, "micros")["signals"][0]["topic"] == "magnesium threonate"
    cluster = hub.create_cluster(GEN, "micros", "Threonate week", pillar="sleep", origin="trend", signal_id=signal["id"])
    assert cluster["origin"] == "trend"
    assert hub.brief(GEN, "micros")["signals"] == []


def test_learnings_need_a_person_to_take_effect(hub):
    learning = hub.propose_learning(ANALYST, "micros", "Lead threads with the study result.")
    assert hub.brief(GEN, "micros")["learnings"] == []
    with pytest.raises(HubError):
        hub.decide_learning(ANALYST, learning["id"], "accepted")
    hub.decide_learning(ADMIN, learning["id"], "accepted")
    assert hub.brief(GEN, "micros")["learnings"][0]["statement"].startswith("Lead threads")


def test_tracked_link_replaces_existing_utm():
    link = tracked_link("https://a.com/p?utm_source=old&k=1#top", channel="x", campaign="sleep", item_id=7)
    assert link == "https://a.com/p?k=1&utm_source=x&utm_medium=social&utm_campaign=sleep&utm_content=cmsbot-7#top"


def _post(item_id, day, rate, age=168.0, channel="x", **dims):
    posted = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return {"item_id": item_id, "posted_at": posted.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snapshots": [{"age_hours": age, "data": {"impressions": 1000, "likes": rate * 1000}}],
            "dims": {"channel": channel, "kind": "thread", **dims}, "sessions": 0, "signups": 0, "revenue_usd": 0, "cost_usd": 0}


def test_stats_excludes_posts_too_young_to_compare():
    posts = [_post(i, i, 0.02) for i in range(6)] + [_post(99, 20, 0.001, age=20)]
    report = stats.analyse(posts)
    assert report["posts_measured"] == 6
    assert any("no snapshot near" in c for c in report["caveats"])


def test_stats_shrinks_small_samples_and_keeps_exploring():
    posts = [_post(i, i, 0.02, hook="question") for i in range(8)]
    posts += [_post(100 + i, i, 0.06, hook="stat") for i in range(8)]
    posts += [_post(200, 3, 0.2, hook="story")]
    report = stats.analyse(posts, seed=1)
    rows = {r["value"]: r for r in report["by_attribute"]["hook"]}
    assert rows["story"]["verdict"] == "not enough data"
    assert rows["stat"]["verdict"] == "above baseline"
    assert rows["question"]["verdict"] == "below baseline"
    assert rows["story"]["typical_multiple"] < 3  # one lucky post is not trusted at face value
    assert all(r["suggested_share"] >= 0.1 for r in rows.values())
    assert abs(sum(r["suggested_share"] for r in rows.values()) - 1) < 0.02


def test_stats_baseline_is_per_channel():
    posts = [_post(i, i, 0.01, channel="x", hook="a") for i in range(6)]
    posts += [_post(50 + i, i, 0.10, channel="instagram", hook="b") for i in range(6)]
    report = stats.analyse(posts, seed=1)
    # instagram's higher native rate must not make hook "b" look better
    assert all(r["verdict"] == "no clear difference" for r in report["by_attribute"]["hook"])


def test_performance_end_to_end(hub):
    ids = []
    for index in range(5):
        item = draft(hub, title=f"T{index}", attributes={"hook": "stat" if index % 2 else "question"})
        hub.submit(GEN, item["id"])
        hub.approve(ADMIN, item["id"])
        hub.mark_posted(DIST, item["id"], posted_at="2026-10-0%dT12:00:00Z" % (index + 1))
        hub.report_metrics(DIST, item["id"], {"impressions": 1000, "likes": 10 + index, "bogus": 1},
                           measured_at="2026-10-%02dT12:00:00Z" % (index + 8))
        ids.append(item["id"])
    hub.report_conversions(ANALYST, "micros", [{"utm_content": f"cmsbot-{ids[0]}", "day": "2026-10-03", "sessions": 40, "signups": 2}], source="ga4")
    hub.record_cost(GEN, "micros", amount_usd=3, category="llm", item_id=ids[0])
    report = hub.performance(ADMIN, "micros", seed=1)
    assert report["posts_comparable"] == 5
    assert report["channels"][0]["signups"] == 2 and report["channels"][0]["cost_per_signup_usd"] == 1.5
    assert {r["value"] for r in report["by_attribute"]["hook"]} == {"stat", "question"}


def test_plan_asset_set_maps_anchor_and_derivatives(tmp_path):
    root = tmp_path / "2026-11-10-mag"
    (root / "source").mkdir(parents=True)
    (root / "distribution" / "derivatives").mkdir(parents=True)
    (root / "source" / "content.md").write_text("# Title here\n\nBody.\n")
    (root / "source" / "claims.json").write_text(json.dumps({"claims": [{"text": "A", "citation": "c", "verification": {"url": "https://u"}}]}))
    (root / "distribution" / "derivatives" / "thread.json").write_text(json.dumps(
        {"kind": "thread", "cost_usd": 0.1, "items": [{"angle": "Angle", "posts": ["see {{anchor_url}}"], "claim_ids": ["L1"]}]}))
    plan = plan_asset_set(root, link="https://site/blog/x")
    assert [(p["kind"], p["channel"]) for p in plan] == [("blog", "blog"), ("thread", "x")]
    assert plan[0]["claims"][0]["citation_url"] == "https://u"
    assert plan[1]["body"]["posts"] == ["see https://site/blog/x"]
    assert plan[1]["external_ref"] == "2026-11-10-mag:thread:1"


def test_writing_checks_flag_objective_problems(hub):
    hub.upsert_project(ADMIN, "micros", "Micros", brand={"lexicon": {"avoid": ["game-changing", "unlock"]}})
    anchor = draft(hub, kind="blog", channel="blog", body={"markdown": "x"})
    hub.set_claims(GEN, anchor["id"], [{"text": "A review pooled 3 trials with 151 adults.", "citation": "Mah 2021"}])
    long_post = "This game-changing mineral helped my client sleep 47% longer. " + "word " * 60
    child = draft(hub, parent_id=anchor["id"], body={"posts": ["151 adults, three trials. https://example.com/" + "a" * 300, long_post],
                                                      "claim_ids": ["L1"]})
    found = {c["check"]: c for c in child["writing_checks"]}
    assert found["length"]["detail"].startswith("Post 2 is") and found["length"]["level"] == "fix"
    assert "game-changing" in found["brand words"]["detail"]
    assert "47%" in found["figures"]["detail"] and "151" not in found["figures"]["detail"]
    assert "invented experience" in found
    hub.submit(GEN, child["id"])
    assert any("Post 2 is" in b for b in hub.get_item(ADMIN, child["id"])["blockers"])  # over the limit blocks approval
    clean = draft(hub, parent_id=anchor["id"], body={"posts": ["A review pooled 3 trials."], "claim_ids": ["L1"]})
    assert [c for c in clean["writing_checks"] if c["level"] == "fix"] == []
