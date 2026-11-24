"""Background jobs for work that outlasts an HTTP request: rendering video, generating footage or voice.

An agent or the review page starts a job and gets an id back at once, then asks for its status.
Proxies and agent tool calls commonly give up after about 30 seconds; a render takes a minute or
more and a generated clip several. Jobs run in this process, a few at a time.
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import now_iso

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cmsbot-job")
PUBLIC = "id, item_id, project, kind, status, result, error, actor, created_at, finished_at"


def recover(hub: Hub) -> None:
    """Jobs can't survive a restart; say so instead of leaving them 'running' forever."""
    hub.db.execute("UPDATE jobs SET status = 'failed', error = 'The Hub restarted while this was running. Start it again.', "
                   "finished_at = ? WHERE status = 'running'", (now_iso(),))


def active(hub: Hub, item_id: int) -> list[dict[str, Any]]:
    return hub.db.all(f"SELECT {PUBLIC} FROM jobs WHERE item_id = ? AND status = 'running' ORDER BY id", (item_id,))


def get(hub: Hub, actor: Actor, job_id: int) -> dict[str, Any]:
    job = hub.db.one(f"SELECT {PUBLIC} FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HubError(f"No job {job_id}.", 404)
    hub._scope(actor, job["project"])
    job["next"] = "Check again in about 15 seconds." if job["status"] == "running" else ""
    return job


def start(hub: Hub, actor: Actor, item_id: int, kind: str, work: Callable[[], dict[str, Any]],
          summarise: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run `work` in the background. A second request for the same thing returns the job already running."""
    item = hub.get_item(actor, item_id)
    running = hub.db.one(f"SELECT {PUBLIC} FROM jobs WHERE item_id = ? AND kind = ? AND status = 'running'", (item_id, kind))
    if running:
        return {**running, "next": "Already running. Check again in about 15 seconds."}
    job_id = hub.db.insert("jobs", {"item_id": item_id, "project": item["project"], "kind": kind, "status": "running",
                                    "result": {}, "error": "", "actor": actor.name, "created_at": now_iso()})

    def run() -> None:
        try:
            result = work()
            hub.db.update("jobs", "id", job_id, {"status": "done", "result": summarise(result) if summarise else result,
                                                 "finished_at": now_iso()})
        except HubError as exc:
            hub.db.update("jobs", "id", job_id, {"status": "failed", "error": str(exc), "finished_at": now_iso()})
        except Exception as exc:  # noqa: BLE001 - a job must always end in a recorded state
            traceback.print_exc()
            hub.db.update("jobs", "id", job_id, {"status": "failed", "error": f"Unexpected error: {exc}", "finished_at": now_iso()})

    _pool.submit(run)
    return get(hub, actor, job_id)
