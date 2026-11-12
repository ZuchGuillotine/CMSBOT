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
    assert {"get_brief", "save_draft", "submit_for_review", "ready_to_post", "claim_post", "mark_posted", "report_metrics"} <= names
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
