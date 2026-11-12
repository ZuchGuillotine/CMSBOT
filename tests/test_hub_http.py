import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient

from cmsbot.hub.app import create_app
from cmsbot.hub.service import Hub
from cmsbot.hub.store import Store


@pytest.fixture
def ctx():
    hub = Hub(Store(":memory:"))
    tokens = {role: hub.create_token(role, role) for role in ("admin", "generator", "distributor")}
    with TestClient(create_app(hub=hub)) as client:
        client.put("/api/projects/micros", json={"name": "Micros"}, headers=auth(tokens["admin"]))
        yield client, tokens


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def rpc(client, token, method, params=None, query=""):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers.update(auth(token))
    return client.post("/mcp" + query, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=headers)


def call(client, token, name, **arguments):
    result = rpc(client, token, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    return result


def test_rest_requires_a_token(ctx):
    client, _ = ctx
    assert client.get("/api/items").status_code == 401
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "CMSBOT review" in client.get("/").text


def test_rest_review_flow(ctx):
    client, tokens = ctx
    created = client.post("/api/projects/micros/items", headers=auth(tokens["generator"]),
                          json={"kind": "thread", "channel": "x", "title": "T", "body": {"posts": ["a"]}}).json()
    item = created["id"]
    assert client.post(f"/api/items/{item}/submit", headers=auth(tokens["generator"])).json()["status"] == "in_review"
    assert client.post(f"/api/items/{item}/approve", headers=auth(tokens["generator"])).status_code == 403
    assert client.get("/api/ready", headers=auth(tokens["distributor"])).json() == []
    approved = client.post(f"/api/items/{item}/approve", headers=auth(tokens["admin"]), json={"body": {"posts": ["a, edited"]}})
    assert approved.json()["status"] == "approved"
    assert client.get("/api/ready", headers=auth(tokens["distributor"])).json()[0]["body"]["posts"] == ["a, edited"]


def test_mcp_requires_a_token(ctx):
    client, tokens = ctx
    assert rpc(client, "", "tools/list").status_code == 401
    assert rpc(client, "wrong", "tools/list").status_code == 401
    assert rpc(client, "", "tools/list", query=f"?token={tokens['distributor']}").status_code == 200


def test_mcp_draft_to_post_loop(ctx):
    client, tokens = ctx
    names = {t["name"] for t in rpc(client, tokens["generator"], "tools/list").json()["result"]["tools"]}
    assert {"get_brief", "save_draft", "submit_for_review"} <= names
    posting = {t["name"] for t in rpc(client, tokens["distributor"], "tools/list").json()["result"]["tools"]}
    assert {"ready_to_post", "claim_post", "mark_posted", "report_metrics"} <= posting
    saved = call(client, tokens["generator"], "save_draft", project="micros", kind="post", channel="linkedin",
                 title="P", body={"text": "hello"})
    item = saved["structuredContent"]["id"]
    call(client, tokens["generator"], "submit_for_review", item_id=item)
    refused = call(client, tokens["distributor"], "claim_post", item_id=item)
    assert refused["isError"] and "not approved" in refused["content"][0]["text"]
    client.post(f"/api/items/{item}/approve", headers=auth(tokens["admin"]))
    claimed = call(client, tokens["distributor"], "claim_post", item_id=item)
    assert claimed["structuredContent"]["body"] == {"text": "hello"}
    posted = call(client, tokens["distributor"], "mark_posted", item_id=item, post_url="https://li/p/1")
    assert posted["structuredContent"]["status"] == "published"
    metrics = call(client, tokens["distributor"], "report_metrics", item_id=item, metrics={"impressions": 500, "likes": 9})
    assert metrics["structuredContent"]["recorded"] == {"impressions": 500.0, "likes": 9.0}


def test_upload_and_serve_media(tmp_path):
    from cmsbot.hub.media import LocalStorage

    hub = Hub(Store(":memory:"))
    admin, gen = hub.create_token("a", "admin"), hub.create_token("g", "generator")
    with TestClient(create_app(hub=hub, storage=LocalStorage(tmp_path))) as client:
        client.put("/api/projects/acme", json={"name": "Acme"}, headers=auth(admin))
        item = client.post("/api/projects/acme/items", headers=auth(gen),
                           json={"kind": "short_video", "channel": "tiktok", "title": "V", "body": {"hook": "h"}}).json()["id"]
        up = client.post(f"/api/items/{item}/media?name=clip.mp4&role=video", content=b"\x00\x01", headers=auth(gen))
        assert up.status_code == 200
        url = up.json()["media"][0]["url"]
        assert client.get(url).content == b"\x00\x01"
        assert client.post(f"/api/items/{item}/media?name=x.sh", content=b"#!", headers=auth(gen)).status_code == 400
        assert client.get("/media/acme/%2e%2e/%2e%2e/%2e%2e/etc/passwd").status_code in (401, 404)
        preview = client.post(f"/api/items/{item}/preview", headers=auth(gen))
        assert preview.status_code == 400  # not a carousel


def test_each_role_sees_only_its_tools_and_is_told_its_scope():
    from cmsbot.hub.app import ROLE_TOOLS, tools_for

    hub = Hub(Store(":memory:"))
    tokens = {role: hub.create_token(f"{role}-1", role) for role in ("admin", "agent", "generator", "distributor", "analyst")}
    with TestClient(create_app(hub=hub)) as client:
        client.put("/api/projects/acme", json={"name": "Acme"}, headers=auth(tokens["admin"]))
        client.put("/api/projects/other", json={"name": "Other"}, headers=auth(tokens["admin"]))
        scoped = hub.create_token("grok-acme", "distributor", "acme")
        seen = {role: {t["name"] for t in rpc(client, token, "tools/list").json()["result"]["tools"]} for role, token in tokens.items()}
        everything = seen["admin"]
        assert set().union(*ROLE_TOOLS.values()) <= everything, "a tool named in ROLE_TOOLS does not exist"
        assert everything == set().union(*ROLE_TOOLS.values()), "a new tool has no role assigned; add it to ROLE_TOOLS"
        assert seen["distributor"] == tools_for("distributor", everything)
        assert not seen["distributor"] & {"save_draft", "list_items", "get_item", "get_brief", "generate_footage"}
        assert not seen["generator"] & {"claim_post", "mark_posted", "ready_to_post"}
        assert not seen["analyst"] & {"save_draft", "claim_post", "generate_footage"}
        assert seen["agent"] == seen["generator"] | seen["distributor"] | seen["analyst"]
        me = call(client, scoped, "whoami")["structuredContent"]
        assert (me["name"], me["role"], me["projects"], me["limited_to_project"]) == ("grok-acme", "distributor", ["acme"], "acme")
        assert "claim_post" in me["your_tools"] and "save_draft" not in me["your_tools"] and "See or edit drafts" in me["you_cannot"]
        hidden = call(client, tokens["distributor"], "save_draft", project="acme", kind="post", channel="x", title="t", body={"text": "x"})
        assert hidden["isError"] and "not part of the distributor role" in hidden["content"][0]["text"]
        assert call(client, tokens["agent"], "whoami")["structuredContent"]["projects"] == ["acme", "other"]
