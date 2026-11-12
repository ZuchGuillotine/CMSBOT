import sqlite3

import pytest
from fastapi.testclient import TestClient

from cmsbot.hub.app import create_app
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.create_token("ben", "admin")
    h.upsert_project(ADMIN, "acme", "Acme")
    h.upsert_project(ADMIN, "other", "Other")
    return h


def test_issue_rotate_and_revoke(hub):
    issued = hub.issue_token(ADMIN, "grok-bot", "distributor", "acme")
    assert hub.authenticate(issued["token"]).project == "acme"
    fresh = hub.rotate_token(ADMIN, issued["id"])
    assert hub.authenticate(issued["token"]) is None
    assert hub.authenticate(fresh["token"]).name == "grok-bot"
    hub.revoke_token(fresh["id"], ADMIN)
    assert hub.authenticate(fresh["token"]) is None
    listed = hub.list_tokens(ADMIN)
    assert all("token" not in row and "token_hash" not in row for row in listed)
    assert next(r for r in listed if r["id"] == fresh["id"])["created_by"] == "ben"


def test_tokens_expire_and_record_use(hub):
    issued = hub.issue_token(ADMIN, "temp", "analyst", None, expires_in_days=30)
    assert hub.authenticate(issued["token"]) is not None
    assert next(r for r in hub.list_tokens() if r["id"] == issued["id"])["last_used_at"]
    hub.db.update("tokens", "id", issued["id"], {"expires_at": "2020-01-01T00:00:00Z"})
    assert hub.authenticate(issued["token"]) is None
    assert next(r for r in hub.list_tokens() if r["id"] == issued["id"])["expired"] is True


def test_last_all_project_reviewer_cannot_be_revoked(hub):
    only = next(r for r in hub.list_tokens() if r["role"] == "admin")
    with pytest.raises(HubError) as refused:
        hub.revoke_token(only["id"], ADMIN)
    assert refused.value.status == 409
    hub.issue_token(ADMIN, "second", "admin")
    hub.revoke_token(only["id"], ADMIN)


def test_project_reviewer_manages_only_their_project(hub):
    scoped = Actor("dana", "admin", "acme")
    assert hub.issue_token(scoped, "acme-bot", "generator", "acme")["project"] == "acme"
    for attempt in (lambda: hub.issue_token(scoped, "x", "generator", None), lambda: hub.issue_token(scoped, "x", "admin", "other"),
                    lambda: hub.set_setting(scoped, "XAI_API_KEY", "k"), lambda: hub.list_settings(scoped, "other")):
        with pytest.raises(HubError) as denied:
            attempt()
        assert denied.value.status == 403
    assert {r["project"] for r in hub.list_tokens(scoped)} == {"acme"}
    with pytest.raises(HubError):
        hub.issue_token(Actor("bot", "generator"), "x", "generator")


def test_settings_are_encrypted_masked_and_layered(tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    path = tmp_path / "hub.sqlite3"
    hub = Hub(Store(path))
    hub.upsert_project(ADMIN, "acme", "Acme")
    hub.set_setting(ADMIN, "XAI_API_KEY", "hub-wide-secret-9876")
    hub.set_setting(ADMIN, "XAI_API_KEY", "acme-own-secret-1111", "acme")
    assert hub.setting("XAI_API_KEY") == "hub-wide-secret-9876"
    assert hub.setting("XAI_API_KEY", "acme") == "acme-own-secret-1111"
    assert hub.setting("OPENROUTER_API_KEY", "acme") == "env-key"
    rows = {r["name"]: r for r in hub.list_settings(ADMIN, "acme")}
    assert rows["XAI_API_KEY"]["hint"] == "…1111" and rows["XAI_API_KEY"]["source"] == "this project"
    assert rows["OPENROUTER_API_KEY"]["source"] == "environment"
    assert "secret" not in str(hub.list_settings(ADMIN)) .replace("'kind': 'secret'", "")
    raw = sqlite3.connect(path).execute("SELECT sealed FROM settings").fetchall()
    assert all("secret" not in row[0] for row in raw)
    assert (tmp_path / "secret.key").stat().st_mode & 0o077 == 0
    hub.clear_setting(ADMIN, "XAI_API_KEY", "acme")
    assert hub.setting("XAI_API_KEY", "acme") == "hub-wide-secret-9876"
    reopened = Hub(Store(path))  # survives a restart with the same key file
    assert reopened.setting("XAI_API_KEY") == "hub-wide-secret-9876"
    with pytest.raises(HubError):
        hub.set_setting(ADMIN, "CMSBOT_RATE_XAI_IMAGE", "cheap")
    with pytest.raises(HubError):
        hub.set_setting(ADMIN, "PATH", "x")


def test_first_run_setup_in_the_browser():
    hub = Hub(Store(":memory:"))
    assert hub.needs_setup()
    code = hub.setup_code()
    with TestClient(create_app(hub=hub, with_mcp=False)) as client:
        code = hub.setup_code()  # create_app issued its own; take a fresh one for the test
        assert client.get("/api/setup").json() == {"needed": True}
        assert client.post("/api/setup", json={"code": "nope", "name": "Ben"}).status_code == 403
        token = client.post("/api/setup", json={"code": code, "name": "Ben"}).json()["token"]
        assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()["role"] == "admin"
        assert client.get("/api/setup").json() == {"needed": False}
        assert client.post("/api/setup", json={"code": code, "name": "Eve"}).status_code == 409
        headers = {"Authorization": f"Bearer {token}"}
        made = client.post("/api/tokens", headers=headers, json={"name": "bot", "role": "generator"}).json()
        assert client.get("/api/tokens", headers={"Authorization": f"Bearer {made['token']}"}).status_code == 403
        assert client.put("/api/settings/XAI_API_KEY", headers=headers, json={"value": "abcdefgh1234"}).json()["hint"] == "…1234"
        assert "abcdefgh1234" not in client.get("/api/settings", headers=headers).text
    assert hub.setup_code() is None


def test_existing_database_gains_new_columns(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tokens (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, project TEXT, "
                 "token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    conn.close()
    hub = Hub(Store(path))
    token = hub.create_token("ben", "admin")
    assert hub.authenticate(token).role == "admin"


def test_all_round_agent_does_every_agent_job_but_never_a_reviewers(hub):
    from cmsbot.hub import due

    token = hub.issue_token(ADMIN, "one-agent", "agent", "acme")["token"]
    agent = hub.authenticate(token)
    assert token.startswith("cms_age_") and agent.role == "agent"
    item = hub.create_item(agent, "acme", kind="post", channel="x", title="P", body={"text": "hello"})   # drafts
    hub.submit(agent, item["id"])
    for forbidden in (lambda: hub.approve(agent, item["id"]), lambda: hub.update_item(agent, item["id"], body={"text": "sneak"}),
                      lambda: hub.claim(agent, item["id"]), lambda: hub.set_brand(agent, "acme", {"tone": "x"}),
                      lambda: hub.issue_token(agent, "more", "admin"), lambda: hub.set_setting(agent, "XAI_API_KEY", "k"),
                      lambda: hub.decide_learning(agent, 1, "accepted")):
        with pytest.raises(HubError) as denied:
            forbidden()
        assert denied.value.status in (403, 409)
    assert hub.ready_to_post(agent) == []                          # sees drafts, but nothing is postable until approved
    assert hub.list_items(agent, project="acme")[0]["id"] == item["id"]
    hub.approve(ADMIN, item["id"])
    assert hub.claim(agent, item["id"])["body"] == {"text": "hello"}   # posts
    hub.mark_posted(agent, item["id"], post_url="https://x.com/1")
    hub.report_metrics(agent, item["id"], {"impressions": 10})
    hub.report_conversions(agent, "acme", [], source="ga4")           # analyses
    hub.propose_learning(agent, "acme", "Try shorter hooks.")
    listed = {t["who"] for t in due.whats_due(hub, agent, "acme")["for_you"]}
    assert "reviewer" not in listed
    other = hub.upsert_project(ADMIN, "other", "Other")
    with pytest.raises(HubError):
        hub.create_item(agent, other["slug"], kind="post", channel="x", title="t", body={"text": "x"})
