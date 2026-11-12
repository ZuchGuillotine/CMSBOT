import pytest
from fastapi.testclient import TestClient

from cmsbot.hub import fetch
from cmsbot.hub.app import create_app
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
TEXT = "# Guide\n\n" + "Bookkeeping is mostly data entry. " * 12


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.create_token("ben", "admin")
    h.upsert_project(ADMIN, "acme", "Acme")
    return h


def counts(series):
    return {(c["kind"], c["channel"]): c["count"] for c in series["current"]}


def test_import_is_published_and_gets_the_default_series(hub):
    hub.set_recipe(ADMIN, "acme", [{"kind": "thread", "channel": "x", "count": 3}, {"kind": "post", "channel": "linkedin", "count": 1},
                                   {"kind": "carousel", "channel": "instagram", "count": 0}])
    anchor = hub.import_published(GEN, "acme", url="https://acme.com/blog/guide", title="Guide", markdown=TEXT)
    assert anchor["status"] == "published" and anchor["publications"][0]["post_url"] == "https://acme.com/blog/guide"
    assert hub.ready_to_post(Actor("grok", "distributor")) == []  # an imported piece is never handed out for posting
    series = hub.series(ADMIN, anchor["id"])
    assert counts(series) == {("thread", "x"): 3, ("post", "linkedin"): 1}
    assert all(c["status"] == "planned" for c in series["children"])
    child = hub.get_item(ADMIN, series["children"][0]["id"])
    assert child["link"] == "https://acme.com/blog/guide" and "anchor #%d" % anchor["id"] in child["audit"]["brief"]
    with pytest.raises(HubError) as twice:
        hub.import_published(GEN, "acme", url="https://acme.com/blog/guide", title="Guide", markdown=TEXT)
    assert twice.value.status == 409
    hub.set_claims(GEN, anchor["id"], [{"text": "A claim."}])  # an agent may record claims on an imported anchor
    assert len(hub.get_item(ADMIN, anchor["id"])["claims"]) == 1


def test_series_can_grow_and_shrink_without_touching_started_work(hub):
    anchor = hub.import_published(ADMIN, "acme", url="https://acme.com/a", title="A", markdown=TEXT)
    grown = hub.plan_series(ADMIN, anchor["id"], [{"kind": "thread", "channel": "x", "count": 8},
                                                  {"kind": "short_video", "channel": "tiktok", "count": 3}])
    assert grown["added"] == 11 and counts(grown) == {("thread", "x"): 8, ("short_video", "tiktok"): 3}
    first = next(c for c in grown["children"] if c["kind"] == "thread")
    hub.update_item(GEN, first["id"], body={"posts": ["started"]})
    assert hub.get_item(GEN, first["id"])["status"] == "drafting"
    shrunk = hub.plan_series(ADMIN, anchor["id"], [{"kind": "thread", "channel": "x", "count": 0},
                                                   {"kind": "short_video", "channel": "tiktok", "count": 1}])
    assert shrunk["removed"] == 9 and counts(shrunk) == {("thread", "x"): 1, ("short_video", "tiktok"): 1}
    assert shrunk["kept"] == ["1 thread on x already started and kept"]
    assert [i["id"] for i in hub.brief(GEN, "acme")["to_write"]] == [c["id"] for c in shrunk["children"] if c["status"] == "planned"]


def test_planner_rejects_what_it_does_not_offer(hub):
    anchor = hub.import_published(ADMIN, "acme", url="https://acme.com/a", title="A", markdown=TEXT)
    for plan in ([{"kind": "thread", "channel": "email", "count": 1}], [{"kind": "blog", "channel": "blog", "count": 1}],
                 [{"kind": "thread", "channel": "x", "count": 99}]):
        with pytest.raises(HubError):
            hub.plan_series(ADMIN, anchor["id"], plan)
    child = hub.plan_series(ADMIN, anchor["id"], [{"kind": "thread", "channel": "x", "count": 1}])["children"][0]
    with pytest.raises(HubError):
        hub.series(ADMIN, child["id"])
    with pytest.raises(HubError):
        hub.set_recipe(GEN, "acme", [])
    with pytest.raises(HubError):
        hub.plan_series(Actor("grok", "distributor"), anchor["id"], [])


def test_anchor_listing_and_series_over_http(hub):
    token = hub.create_token("r", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(hub=hub, with_mcp=False)) as client:
        imported = client.post("/api/projects/acme/import", headers=headers,
                               json={"url": "https://acme.com/b", "title": "B", "markdown": TEXT}).json()
        client.post("/api/projects/acme/items", headers=headers, json={"kind": "post", "channel": "x", "title": "loose", "body": {"text": "t"}})
        planned = client.put(f"/api/items/{imported['id']}/series", headers=headers,
                             json={"plan": [{"kind": "carousel", "channel": "instagram", "count": 1}]}).json()
        assert planned["added"] == 1
        anchors = client.get("/api/items?anchors=true", headers=headers).json()
        assert [a["id"] for a in anchors] == [imported["id"]]
        assert client.put("/api/projects/acme/recipe", headers=headers,
                          json={"recipe": [{"kind": "thread", "channel": "x", "count": 2}]}).json()["recipe"][0]["count"] == 2
        assert {"kind": "thread", "channels": ["x", "threads"]} in client.get("/api/me", headers=headers).json()["derivatives"]
        refused = client.post("/api/projects/acme/import", headers=headers, json={"url": "http://127.0.0.1/x"})
        assert refused.status_code == 400 and "paste the text" in refused.json()["error"]


def test_page_text_extraction_and_address_checks():
    page = ("<html><head><title>Guide | Acme</title></head><body><nav><p>Home</p></nav><article><h1>The guide</h1>"
            "<p>First &amp; second.</p><h2>Step 1</h2><ul><li>One</li></ul><script>x()</script></article><footer><p>©</p></footer></body></html>")
    out = fetch.extract(page)
    assert out["title"] == "The guide"
    assert out["markdown"] == "# The guide\n\nFirst & second.\n\n## Step 1\n\n- One"
    for bad in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "http://10.0.0.5/"):
        with pytest.raises(fetch.FetchError):
            fetch.check_public(bad)
