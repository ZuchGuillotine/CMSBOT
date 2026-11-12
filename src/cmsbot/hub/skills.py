"""Skills: the written methods agents follow, kept in the Hub so every agent gets the same current
version and so a method can be improved deliberately.

  built-in   shipped with CMSBOT and refreshed on upgrade, unless someone has edited the Hub's copy
  uploaded   added by a reviewer (any SKILL.md)
  scope      hub-wide, or one project's own version, which wins for that project

Changing a skill is a change to how content is made, so it follows the same care as any other
finding: an agent proposes an edit with its evidence, a person accepts or rejects it, every version
is kept, and drafts carry the stamp of the version that made them so results are compared version
against version rather than remembered.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from cmsbot.hub import stats
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import now_iso

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
MAX_BODY = 60_000
PUBLIC = "id, project, name, version, description, status, source, note, evidence, author, created_at, decided_by"


def parse(text: str) -> dict[str, str]:
    """Split a SKILL.md into its front matter (name, description) and body."""
    text = text.replace("\r\n", "\n").strip()
    meta: dict[str, str] = {}
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.DOTALL)
    body = text
    if match:
        body = match.group(2).strip()
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip() in ("name", "description"):
                meta[key.strip()] = value.strip().strip("\"'")
    return {"name": meta.get("name", ""), "description": meta.get("description", ""), "body": body}


def render(row: dict[str, Any]) -> str:
    return f"---\nname: {row['name']}\ndescription: {row['description']}\n---\n\n{row['body'].strip()}\n"


def builtin() -> list[dict[str, str]]:
    out = []
    try:
        pack = resources.files("cmsbot").joinpath("skill_pack")
        for folder in sorted(pack.iterdir(), key=lambda p: p.name):
            file = folder.joinpath("SKILL.md")
            if folder.is_dir() and file.is_file():
                parsed = parse(file.read_text(encoding="utf-8"))
                out.append({**parsed, "name": parsed["name"] or folder.name})
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    return out


def _active(hub: Hub, name: str, project: str) -> dict[str, Any] | None:
    return hub.db.one("SELECT * FROM skills WHERE name = ? AND project = ? AND status = 'active'", (name, project))


def _insert(hub: Hub, *, project: str, name: str, description: str, body: str, status: str, source: str, note: str,
            author: str, evidence: dict[str, Any] | None = None) -> int:
    # A project's copy continues the numbering of the hub-wide skill it replaces, so one stamp never names two texts in a project.
    last = hub.db.one("SELECT MAX(version) AS v FROM skills WHERE name = ? AND project IN (?, '')", (name, project)) or {}
    return hub.db.insert("skills", {"project": project, "name": name, "version": int(last.get("v") or 0) + 1,
                                    "description": description, "body": body, "status": status, "source": source, "note": note,
                                    "evidence": evidence or {}, "author": author, "created_at": now_iso()})


def seed(hub: Hub) -> None:
    """Install built-in skills hub-wide. An upgrade refreshes a built-in only if nobody changed the Hub's copy."""
    for skill in builtin():
        current = _active(hub, skill["name"], "")
        if current is None:
            if not hub.db.one("SELECT id FROM skills WHERE name = ? AND project = ''", (skill["name"],)):
                _insert(hub, project="", name=skill["name"], description=skill["description"], body=skill["body"],
                        status="active", source="builtin", note="Shipped with CMSBOT", author="cmsbot")
        elif current["source"] == "builtin" and (current["body"] != skill["body"] or current["description"] != skill["description"]):
            hub.db.update("skills", "id", current["id"], {"status": "superseded"})
            _insert(hub, project="", name=skill["name"], description=skill["description"], body=skill["body"],
                    status="active", source="builtin", note="Updated with CMSBOT", author="cmsbot")


def _check(name: str, description: str, body: str) -> None:
    if not NAME.match(name):
        raise HubError("A skill name is lowercase letters, digits and hyphens, for example 'reddit-replies'.")
    if not description.strip():
        raise HubError("Give the skill a one-line description of when to use it; agents choose skills by it.")
    if len(body.strip()) < 40:
        raise HubError("The skill has no method in it yet.")
    if len(body) > MAX_BODY:
        raise HubError("The skill is too long; keep a skill to one method.")


def listing(hub: Hub, actor: Actor, project: str | None = None) -> list[dict[str, Any]]:
    """The skills in effect: a project's own version where it has one, otherwise the hub-wide one."""
    project = actor.project or project or ""
    if project:
        hub._scope(actor, project)
        hub._project(project)
    rows = hub.db.all(f"SELECT {PUBLIC} FROM skills WHERE status = 'active' AND project IN ('', ?) ORDER BY name, project", (project,))
    chosen: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["name"] not in chosen or row["project"]:
            chosen[row["name"]] = row
    out = []
    for row in chosen.values():
        waiting = hub.db.one("SELECT COUNT(*) AS n FROM skills WHERE name = ? AND project IN (?, ?) AND status = 'proposed'",
                             (row["name"], row["project"], project)) or {}
        out.append({"name": row["name"], "description": row["description"], "version": row["version"], "source": row["source"],
                    "scope": row["project"] or "hub", "stamp": f"{row['name']}@{row['version']}", "updated": row["created_at"],
                    "updated_by": row["author"], "proposals_waiting": waiting.get("n", 0)})
    return sorted(out, key=lambda r: (r["name"] != "cmsbot-start", r["name"]))


def get(hub: Hub, actor: Actor, name: str, project: str | None = None) -> dict[str, Any]:
    project = actor.project or project or ""
    if project:
        hub._scope(actor, project)
    row = (_active(hub, name, project) if project else None) or _active(hub, name, "")
    if not row:
        raise HubError(f"No skill '{name}'. Call list_skills to see what exists.", 404)
    return {"name": row["name"], "description": row["description"], "version": row["version"], "scope": row["project"] or "hub",
            "stamp": f"{row['name']}@{row['version']}", "body": row["body"],
            "use": f"Set the attribute skill=\"{row['name']}@{row['version']}\" on every draft you make with this skill."}


def save(hub: Hub, actor: Actor, *, name: str, description: str, body: str, project: str | None = None, note: str = "") -> dict[str, Any]:
    """A reviewer adds a skill or replaces the active version. The previous version is kept."""
    project = project or ""
    hub._manageable(actor, project or None)
    if project:
        hub._project(project)
    _check(name, description, body)
    current = _active(hub, name, project)
    if current and current["body"].strip() == body.strip() and current["description"] == description.strip():
        return get(hub, actor, name, project)
    if current:
        hub.db.update("skills", "id", current["id"], {"status": "superseded"})
    _insert(hub, project=project, name=name, description=description.strip(), body=body.strip(), status="active",
            source="edited" if current else "uploaded", note=note.strip() or ("Edited" if current else "Uploaded"), author=actor.name)
    hub._event(actor, "skill_saved", project=project or None, detail={"name": name})
    return get(hub, actor, name, project)


def evidence_caveats(hub: Hub, actor: Actor, name: str, project: str) -> list[str]:
    """What a reviewer should know before trusting a performance-based reason to change a skill."""
    if not project:
        return ["This changes the skill for every project. Results differ by project; consider changing one project's copy first."]
    current = _active(hub, name, project) or _active(hub, name, "")
    stamp = f"{name}@{current['version']}" if current else ""
    report = hub.performance(actor, project)
    rows = {r["value"]: r for r in report["by_attribute"].get("skill", [])}
    mine = rows.get(stamp)
    out = []
    if mine is None:
        out.append(f"No measured posts carry the stamp {stamp or name}, so there is no result for the current version to compare against.")
    elif mine["posts"] < stats.MIN_POSTS:
        out.append(f"The current version has only {mine['posts']} measured post(s) at 7 days; under {stats.MIN_POSTS} is too few to judge it.")
    elif mine["verdict"] == "no clear difference":
        out.append("The current version's results are not clearly different from typical; a change may be solving noise.")
    out += [c for c in report["caveats"] if "Fewer than 20" in c]
    return out


def propose(hub: Hub, actor: Actor, *, name: str, body: str, rationale: str, project: str | None = None,
            description: str = "", evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """An agent's suggested new version. One open proposal per skill, so changes are judged one at a time."""
    project = actor.project or project or ""
    if project:
        hub._scope(actor, project)
        hub._project(project)
    current = (_active(hub, name, project) if project else None) or _active(hub, name, "")
    if not current:
        raise HubError(f"No skill '{name}' to change. A reviewer can upload a new skill on the Skills tab.", 404)
    if not rationale.strip():
        raise HubError("Say why: what you observed, on how many posts, and what you expect the change to do.")
    _check(name, description or current["description"], body)
    if body.strip() == current["body"].strip():
        raise HubError("The proposed text is the same as the current version.")
    if hub.db.one("SELECT id FROM skills WHERE name = ? AND project = ? AND status = 'proposed'", (name, project)):
        raise HubError("A proposal for this skill is already waiting for the reviewer. One change at a time.", 409)
    proposal_id = _insert(hub, project=project, name=name, description=(description or current["description"]).strip(), body=body.strip(),
                          status="proposed", source="proposed", note=rationale.strip()[:4000], author=actor.name, evidence=evidence)
    hub._event(actor, "skill_proposed", project=project or None, detail={"name": name})
    return {"proposal_id": proposal_id, "status": "waiting for a reviewer on the Skills tab",
            "caveats": evidence_caveats(hub, actor, name, project)}


def detail(hub: Hub, actor: Actor, name: str, project: str | None = None) -> dict[str, Any]:
    """Everything the Skills tab shows for one skill in one scope: current text, history, open proposals."""
    project = project or ""
    hub._manageable(actor, project or None)
    own = hub.db.all("SELECT * FROM skills WHERE name = ? AND project = ? ORDER BY version DESC", (name, project))
    active = next((r for r in own if r["status"] == "active"), None)
    inherited = None if active or not project else _active(hub, name, "")
    if not own and not inherited:
        raise HubError(f"No skill '{name}'.", 404)
    proposals = [r for r in own if r["status"] == "proposed"]
    return {"name": name, "scope": project or "hub", "active": active, "inherited": inherited,
            "history": [{k: r[k] for k in ("id", "version", "status", "source", "note", "author", "created_at")} for r in own],
            "proposals": [{**r, "caveats": evidence_caveats(hub, actor, name, project)} for r in proposals]}


def decide(hub: Hub, actor: Actor, skill_id: int, accept: bool) -> dict[str, Any]:
    row = hub.db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not row or row["status"] != "proposed":
        raise HubError("That proposal is no longer open.", 404)
    hub._manageable(actor, row["project"] or None)
    if accept:
        current = _active(hub, row["name"], row["project"])
        if current:
            hub.db.update("skills", "id", current["id"], {"status": "superseded"})
        hub.db.update("skills", "id", skill_id, {"status": "active", "source": "edited", "decided_by": actor.name})
    else:
        hub.db.update("skills", "id", skill_id, {"status": "rejected", "decided_by": actor.name})
    hub._event(actor, "skill_accepted" if accept else "skill_rejected", project=row["project"] or None, detail={"name": row["name"]})
    return {"name": row["name"], "accepted": accept}


def restore(hub: Hub, actor: Actor, skill_id: int) -> dict[str, Any]:
    """Make an earlier version current again (as a new version, so the record stays in order)."""
    row = hub.db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not row or row["status"] not in ("superseded", "rejected"):
        raise HubError("Only an earlier version can be restored.", 404)
    return save(hub, actor, name=row["name"], description=row["description"], body=row["body"], project=row["project"],
                note=f"Restored version {row['version']}")


def retire(hub: Hub, actor: Actor, name: str, project: str | None = None) -> None:
    """Stop offering a skill in this scope. A project's own copy falls back to the hub-wide one."""
    project = project or ""
    hub._manageable(actor, project or None)
    current = _active(hub, name, project)
    if not current:
        raise HubError(f"No active skill '{name}' in this scope.", 404)
    hub.db.update("skills", "id", current["id"], {"status": "retired", "decided_by": actor.name})
    hub._event(actor, "skill_retired", project=project or None, detail={"name": name})
