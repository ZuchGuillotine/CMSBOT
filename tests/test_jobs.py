import time

import pytest
from fastapi.testclient import TestClient

from cmsbot import video
from cmsbot.hub import generate
from cmsbot.hub.app import create_app
from cmsbot.hub.media import LocalStorage
from cmsbot.hub.service import Hub
from cmsbot.hub.store import Store

BODY = {"hook": "Hook line here.", "beats": [{"narration": "One beat of narration.", "overlay_text": "Beat"}], "ending": "End."}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def wait(client, token, job):
    for _ in range(240):
        job = client.get(f"/api/jobs/{job['id']}", headers=auth(token)).json()
        if job["status"] != "running":
            return job
        time.sleep(0.25)
    raise AssertionError("job did not finish")


@pytest.fixture
def ctx(tmp_path):
    hub = Hub(Store(":memory:"))
    admin, gen = hub.create_token("a", "admin"), hub.create_token("g", "generator")
    with TestClient(create_app(hub=hub, storage=LocalStorage(tmp_path), with_mcp=False)) as client:
        client.put("/api/projects/acme", json={"name": "Acme"}, headers=auth(admin))
        item = client.post("/api/projects/acme/items", headers=auth(gen),
                           json={"kind": "short_video", "channel": "tiktok", "title": "V", "body": BODY}).json()["id"]
        yield client, hub, admin, gen, item


def test_generation_is_refused_immediately_and_runs_as_a_job(ctx, monkeypatch):
    client, hub, admin, gen, item = ctx
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    client.put("/api/settings/XAI_API_KEY", headers=auth(admin), json={"value": "key-12345678"})
    monkeypatch.setitem(generate.PROVIDERS["grok-image"], "run", lambda prompt, **_: generate.Result(b"png", ".png", "image/png"))
    request = {"shot": 2, "prompt": "four index cards", "kind": "image"}
    early = client.post(f"/api/items/{item}/generate", headers=auth(gen), json=request)
    assert early.status_code == 409 and "not been approved" in early.json()["error"]  # no job was created
    client.post(f"/api/items/{item}/submit", headers=auth(gen))
    client.post(f"/api/items/{item}/approve-script", headers=auth(admin), json={})
    started = client.post(f"/api/items/{item}/generate", headers=auth(gen), json=request).json()["job"]
    assert started["status"] in ("running", "done")
    done = wait(client, gen, started)
    assert done["status"] == "done" and done["result"]["role"] == "shot-02" and done["result"]["cost_is_estimate"] is True
    assert client.get(f"/api/items/{item}", headers=auth(gen)).json()["cost_usd"] > 0


def test_failed_job_reports_the_reason(ctx, monkeypatch):
    client, hub, admin, gen, item = ctx
    client.put("/api/settings/XAI_API_KEY", headers=auth(admin), json={"value": "key-12345678"})

    def broken(prompt, **_):
        raise generate.ProviderError("Grok Imagine refused the request (400): bad prompt")

    monkeypatch.setitem(generate.PROVIDERS["grok-image"], "run", broken)
    job = client.post(f"/api/items/{item}/generate", headers=auth(admin), json={"shot": 1, "prompt": "x", "kind": "image"}).json()["job"]
    done = wait(client, admin, job)
    assert done["status"] == "failed" and "bad prompt" in done["error"]
    assert client.get(f"/api/items/{item}", headers=auth(admin)).json()["cost_usd"] == 0  # nothing charged for a failure


@pytest.mark.skipif(not video.can_render(), reason="needs ffmpeg and Chrome")
def test_video_render_job_and_reviewer_edit(ctx):
    client, hub, admin, gen, item = ctx
    job = client.post(f"/api/items/{item}/render", headers=auth(gen)).json()["job"]
    assert job["kind"] == "render_video"
    again = client.post(f"/api/items/{item}/render", headers=auth(gen)).json()["job"]
    assert again["id"] == job["id"] or again["status"] == "running"  # one render per item at a time
    done = wait(client, gen, job)
    assert done["status"] == "done" and done["result"]["video"].startswith("/media/acme/")
    client.post(f"/api/items/{item}/submit", headers=auth(gen))
    early = client.post(f"/api/items/{item}/approve", headers=auth(admin), json={})
    assert early.status_code == 409 and "Script stage" in early.json()["error"]  # a preview does not skip script review
    client.post(f"/api/items/{item}/approve-script", headers=auth(admin), json={})
    client.post(f"/api/items/{item}/submit", headers=auth(gen))
    edited = client.post(f"/api/items/{item}/approve", headers=auth(admin), json={"body": {**BODY, "ending": "A new ending."}})
    assert edited.status_code == 409 and "rendered again" in edited.json()["error"]
    current = client.get(f"/api/items/{item}", headers=auth(admin)).json()
    wait(client, admin, current["active_jobs"][0])
    assert client.post(f"/api/items/{item}/approve", headers=auth(admin), json={}).json()["status"] == "approved"


def test_jobs_left_running_are_failed_on_restart():
    from cmsbot.hub import jobs
    from cmsbot.hub.service import Actor

    hub = Hub(Store(":memory:"))
    admin = Actor("a", "admin")
    hub.upsert_project(admin, "acme", "Acme")
    item = hub.create_item(admin, "acme", kind="short_video", channel="tiktok", title="V", body=BODY)
    hub.db.insert("jobs", {"item_id": item["id"], "project": "acme", "kind": "render_video", "status": "running",
                           "result": {}, "error": "", "actor": "a", "created_at": "2026-01-01T00:00:00Z"})
    jobs.recover(hub)
    assert jobs.active(hub, item["id"]) == []
    assert "restarted" in jobs.get(hub, admin, 1)["error"]
