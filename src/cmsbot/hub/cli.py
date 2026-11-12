"""`cmsbot hub ...` commands.

Local (act on the database file): init, serve, token.
Remote (talk to a running Hub with CMSBOT_HUB_URL and CMSBOT_HUB_TOKEN): project, push, ready, brief.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
import httpx

from cmsbot.hub.push import plan_asset_set
from cmsbot.hub.service import ROLES, Hub
from cmsbot.hub.store import Store


def _db_path(db: str | None) -> str:
    return db or os.environ.get("CMSBOT_HUB_DB", "hub-data/hub.sqlite3")


class Remote:
    def __init__(self) -> None:
        self.url = os.environ.get("CMSBOT_HUB_URL", "http://127.0.0.1:8787").rstrip("/")
        token = os.environ.get("CMSBOT_HUB_TOKEN", "")
        if not token:
            raise click.ClickException("Set CMSBOT_HUB_TOKEN (and CMSBOT_HUB_URL if the Hub is not on this machine).")
        self.client = httpx.Client(base_url=self.url + "/api", headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def call(self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        try:
            response = self.client.request(method, path, json=body, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            raise click.ClickException(f"Could not reach the Hub at {self.url}: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text
            raise click.ClickException(f"Hub refused ({response.status_code}): {message}")
        return response.json()


@click.group("hub")
def hub_group() -> None:
    """Shared content state, review and distribution hand-off for every project."""


@hub_group.command("init")
@click.option("--db", default=None, help="Database file (default: $CMSBOT_HUB_DB or hub-data/hub.sqlite3).")
@click.option("--reviewer", default="reviewer", show_default=True, help="Name shown on approvals made with the first token.")
def hub_init(db: str | None, reviewer: str) -> None:
    """Create the database and print a reviewer token (shown once)."""
    hub = Hub(Store(_db_path(db)))
    if any(t["role"] == "admin" and not t["revoked"] for t in hub.list_tokens()):
        raise click.ClickException("This Hub already has a reviewer token. Use `cmsbot hub token create` for more.")
    token = hub.create_token(reviewer, "admin")
    click.echo(f"Hub database: {_db_path(db)}")
    click.echo(f"Reviewer token (store it now; it is not shown again):\n{token}")


@hub_group.command("serve")
@click.option("--db", default=None)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
def hub_serve(db: str | None, host: str, port: int) -> None:
    """Run the review page, REST API and MCP endpoint."""
    import uvicorn

    from cmsbot.hub.app import create_app

    uvicorn.run(create_app(_db_path(db)), host=host, port=port, log_level="info")


@hub_group.group("token")
def token_group() -> None:
    """Create, list and revoke access tokens."""


@token_group.command("create")
@click.argument("name")
@click.option("--role", type=click.Choice(ROLES), required=True)
@click.option("--project", default=None, help="Limit the token to one project.")
@click.option("--db", default=None)
def token_create(name: str, role: str, project: str | None, db: str | None) -> None:
    """Create a token. NAME is who or what uses it (e.g. grok-bot, claude-weekly)."""
    click.echo(Hub(Store(_db_path(db))).create_token(name, role, project))


@token_group.command("list")
@click.option("--db", default=None)
def token_list(db: str | None) -> None:
    for row in Hub(Store(_db_path(db))).list_tokens():
        state = "revoked" if row["revoked"] else "active"
        click.echo(f"{row['id']:>3}  {row['role']:<11} {row['name']:<24} {row['project'] or 'all projects':<16} {state}")


@token_group.command("revoke")
@click.argument("token_id", type=int)
@click.option("--db", default=None)
def token_revoke(token_id: int, db: str | None) -> None:
    Hub(Store(_db_path(db))).revoke_token(token_id)
    click.echo(f"Token {token_id} revoked.")


@hub_group.command("project")
@click.argument("project_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--slug", default=None, help="Override the project slug.")
@click.option("--budget", type=float, default=None, help="Monthly content budget in USD.")
@click.option("--require-verified-claims/--no-require-verified-claims", default=None,
              help="Block approval until every cited claim is verified (use for regulated topics).")
def hub_project(project_file: Path, slug: str | None, budget: float | None, require_verified_claims: bool | None) -> None:
    """Register or update a project from its project.json (brand kit and pillars)."""
    data = json.loads(project_file.read_text(encoding="utf-8"))
    remote = Remote()
    slug = slug or data.get("slug") or data["name"]
    ops = data.get("content_ops") or {}
    brand = {
        "description": data.get("description", ""), "website": data.get("website", ""),
        "audience": data.get("audience", []), "voice": data.get("voice", []),
        "differentiators": data.get("differentiators", []), "products": data.get("products", []),
        **{k: v for k, v in (data.get("content") or {}).items()},
        **{k: data[k] for k in ("visual", "personas", "disclosure", "lexicon") if k in data},
    }
    settings: dict[str, Any] = {"platforms": ops.get("platforms", {}), "cadence": ops.get("cadence", {})}
    if budget is not None:
        settings["monthly_budget_usd"] = budget
    if require_verified_claims is not None:
        settings["require_verified_claims"] = require_verified_claims
    project = remote.call("PUT", f"/projects/{slug}", {"name": data.get("name", slug), "brand": brand, "settings": settings})
    for pillar in ops.get("pillars", []):
        remote.call("PUT", f"/projects/{project['slug']}/pillars/{pillar['key']}", {
            "name": pillar.get("label") or pillar["key"], "description": "; ".join(pillar.get("goals", [])),
            "weight": pillar.get("weight", 1)})
    click.echo(f"Project '{project['slug']}' saved with {len(ops.get('pillars', []))} pillar(s).")


@hub_group.command("push")
@click.argument("asset_set_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--project", required=True)
@click.option("--cluster-id", type=int, default=None)
@click.option("--link", default="", help="Public URL of the anchor, used as the link on derivatives.")
@click.option("--submit/--no-submit", default=True, show_default=True, help="Send to review after saving.")
@click.option("--dry-run", is_flag=True)
def hub_push(asset_set_dir: Path, project: str, cluster_id: int | None, link: str, submit: bool, dry_run: bool) -> None:
    """Send a generated asset set (anchor + derivatives) to the Hub for review. Safe to re-run."""
    plan = plan_asset_set(asset_set_dir, link=link)
    if dry_run:
        for entry in plan:
            click.echo(f"{entry['kind']:<12} {entry['channel']:<10} {entry['title'][:70]}")
        return
    remote = Remote()
    anchor_id: int | None = None
    for entry in plan:
        claims = entry.pop("claims", None)
        is_anchor = entry.pop("anchor", False)
        cost = entry.pop("cost_usd", 0)
        body = {**entry, "cluster_id": cluster_id, "parent_id": None if is_anchor else anchor_id}
        item = remote.call("POST", f"/projects/{project}/items", body)
        if is_anchor:
            anchor_id = item["id"]
        if item["status"] not in ("planned", "drafting", "needs_revision", "failed"):
            click.echo(f"#{item['id']} {entry['kind']}: already {item['status']}, left alone.")
            continue
        if claims is not None:
            remote.call("PUT", f"/items/{item['id']}/claims", {"claims": claims})
        if cost and not item["cost_usd"]:
            remote.call("POST", f"/projects/{project}/costs", {"amount_usd": cost, "category": "llm", "vendor": "cmsbot",
                                                              "item_id": item["id"], "note": asset_set_dir.name})
        if submit:
            item = remote.call("POST", f"/items/{item['id']}/submit")
        click.echo(f"#{item['id']} {entry['kind']} ({entry['channel']}): {item['status']}")


@hub_group.command("ready")
@click.option("--project", default=None)
@click.option("--channel", default=None)
def hub_ready(project: str | None, channel: str | None) -> None:
    """Print approved copy that is due, as JSON."""
    click.echo(json.dumps(Remote().call("GET", "/ready", project=project, channel=channel), indent=2, ensure_ascii=False))


@hub_group.command("brief")
@click.argument("project")
def hub_brief(project: str) -> None:
    """Print what a drafting agent should read before writing, as JSON."""
    click.echo(json.dumps(Remote().call("GET", f"/projects/{project}/brief"), indent=2, ensure_ascii=False))
