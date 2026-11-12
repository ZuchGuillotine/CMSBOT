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
        if entry["kind"] == "carousel":
            try:
                remote.call("POST", f"/items/{item['id']}/render")
            except click.ClickException as exc:  # the reviewer sees "images missing" until someone renders
                click.echo(f"#{item['id']} slides not rendered: {exc.message}")
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


@hub_group.command("upload")
@click.argument("item_id", type=int)
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--role", default="", help="What the file is for, e.g. video, cover, voiceover.")
def hub_upload(item_id: int, files: tuple[Path, ...], role: str) -> None:
    """Attach image, video or audio files to an item. The Hub stores them; you need no bucket keys."""
    remote = Remote()
    for path in files:
        response = remote.client.post(f"/items/{item_id}/media", params={"name": path.name, "role": role},
                                      content=path.read_bytes(), timeout=600)
        if response.status_code >= 400:
            raise click.ClickException(f"Upload of {path.name} refused ({response.status_code}): {response.text[:300]}")
        click.echo(f"{path.name} -> {response.json()['media'][-1]['url']}")


@hub_group.command("render")
@click.argument("item_id", type=int)
@click.option("--local", is_flag=True, help="Render on this machine and upload, instead of asking the Hub to render.")
def hub_render(item_id: int, local: bool) -> None:
    """Render a carousel's slide images, or a short video, and attach the result to the item."""
    remote = Remote()
    if not local:
        item = remote.call("POST", f"/items/{item_id}/render")
        if "job" in item:
            import time

            job = item["job"]
            click.echo(f"#{item_id}: rendering (job {job['id']})...")
            while job["status"] == "running":
                time.sleep(5)
                job = remote.call("GET", f"/jobs/{job['id']}")
            if job["status"] == "failed":
                raise click.ClickException(job["error"])
            item = remote.call("GET", f"/items/{item_id}")
            report = item["audit"]["video"]
            click.echo(f"#{item_id}: {report['seconds']}s video rendered by the Hub.")
            for entry in report["checks"]:
                click.echo(f"  {'ok   ' if entry['ok'] else 'CHECK'} {entry['check']}: {entry['detail']}")
            return
        click.echo(f"#{item_id}: {sum(1 for m in item['media'] if isinstance(m, dict) and m.get('role') == 'slide')} slides rendered by the Hub.")
        return
    import tempfile

    from cmsbot import carousel

    item = remote.call("GET", f"/items/{item_id}")
    brand = remote.call("GET", f"/projects/{item['project']}/brief")["brand"]
    with tempfile.TemporaryDirectory() as tmp:
        result = carousel.render_pngs(item["body"], {"name": item["project"], **brand}, Path(tmp))
        urls = []
        for path in result["files"]:
            response = remote.client.post(f"/items/{item_id}/media/raw", params={"name": path.name}, content=path.read_bytes(), timeout=600)
            if response.status_code >= 400:
                raise click.ClickException(f"Upload refused ({response.status_code}): {response.text[:300]}")
            urls.append(response.json()["url"])
    remote.call("PUT", f"/items/{item_id}/render", {"urls": urls, "style": result["style"], "hash": result["hash"]})
    click.echo(f"#{item_id}: {len(urls)} slides rendered here and attached.")


@hub_group.group("skills")
def skills_group() -> None:
    """Copy the Hub's skills to this machine for harnesses that load skills from a folder."""


@skills_group.command("pull")
@click.option("--dir", "target", default="~/.claude/skills", show_default=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--project", default=None, help="Use this project's versions where it has its own.")
def skills_pull(target: Path, project: str | None) -> None:
    """Write each skill in effect to <dir>/<name>/SKILL.md. Run again after skills change."""
    remote = Remote()
    target = target.expanduser()
    for entry in remote.call("GET", "/skills", project=project):
        skill = remote.call("GET", f"/skills/{entry['name']}", project=project)
        folder = target / skill["name"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\nname: {skill['name']}\ndescription: {skill['description']}\n---\n\n"
                                         f"<!-- {skill['stamp']} from the CMSBOT Hub; edit it there, not here -->\n\n{skill['body']}\n", encoding="utf-8")
        click.echo(f"{skill['stamp']} -> {folder / 'SKILL.md'}")


@click.group("carousel")
def carousel_group() -> None:
    """Render carousel slides from a slide spec and a project's brand kit."""


@carousel_group.command("render")
@click.argument("spec_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--project-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="project.json whose `visual` section sets colours, fonts and wordmark.")
@click.option("--item", type=int, default=0, help="Which carousel in a derive file ({\"items\": [...]}).")
@click.option("--style", default=None)
def carousel_render(spec_file: Path, out_dir: Path, project_file: Path | None, item: int, style: str | None) -> None:
    """Write slide-NN.png files (1080x1350) to --out."""
    from cmsbot import carousel

    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    if isinstance(spec.get("items"), list):
        spec = spec["items"][item]
    brand = json.loads(project_file.read_text(encoding="utf-8")) if project_file else {}
    try:
        result = carousel.render_pngs(spec, brand, out_dir, style=style)
    except carousel.CarouselError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{len(result['files'])} slides ({result['style']}) -> {out_dir}")


@click.group("video")
def video_group() -> None:
    """Render short vertical videos from a storyboard, footage and a brand kit."""


@video_group.command("render")
@click.argument("storyboard_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--assets", "assets_dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None,
              help="Folder with shot-01.mp4, shot-02.png, voiceover.m4a, timing.json, music.mp3 (all optional).")
@click.option("--project-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--item", type=int, default=0, help="Which script in a derive file ({\"items\": [...]}).")
def video_render(storyboard_file: Path, out_dir: Path, assets_dir: Path | None, project_file: Path | None, item: int) -> None:
    """Write video.mp4 (1080x1920), contact-sheet.jpg and render.json to --out."""
    from cmsbot import video

    body = json.loads(storyboard_file.read_text(encoding="utf-8"))
    if isinstance(body.get("items"), list):
        body = body["items"][item]
    brand = json.loads(project_file.read_text(encoding="utf-8")) if project_file else {}
    try:
        report = video.render(body, brand, out_dir, assets=video.find_assets(assets_dir) if assets_dir else {})
    except video.VideoError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{report['seconds']}s -> {report['video']}")
    for entry in report["checks"]:
        click.echo(f"  {'ok   ' if entry['ok'] else 'CHECK'} {entry['check']}: {entry['detail']}")
