"""Hub rules: who may do what, the item lifecycle, hash-bound approval and the distribution hand-off.

Roles
  admin        a person; the only role that can approve, edit after submission, verify claims
  generator    a drafting agent; creates and edits drafts, never approves or posts
  distributor  a posting agent; sees only approved copy, claims it, reports the post and its metrics
  analyst      a reporting agent; reads everything, writes metrics, conversions, signals, learnings

Lifecycle
  planned -> drafting -> in_review -> approved -> scheduled -> posting -> published
                 ^           |
                 +-- needs_revision      (plus rejected, failed)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cmsbot import carousel, video
from cmsbot.hub import checks, stats
from cmsbot.hub.store import Store, now_iso, parse_iso
from cmsbot.hub.vault import Vault

ROLES = ("admin", "agent", "generator", "distributor", "analyst")
# "agent" is one agent doing all the agent work: everything the three narrow roles can do, and nothing a reviewer can.
AGENT_COVERS = ("generator", "distributor", "analyst")
CHANNELS = ("blog", "email", "x", "threads", "instagram", "linkedin", "tiktok", "facebook", "reddit", "youtube")
KINDS = ("blog", "newsletter", "thread", "post", "carousel", "short_video", "image", "infographic")
STATUSES = (
    "planned", "drafting", "in_review", "needs_revision", "approved", "scheduled",
    "posting", "published", "rejected", "failed",
)
AGENT_EDITABLE = {"planned", "drafting", "needs_revision", "failed"}
READY = {"approved", "scheduled"}
MAX_ATTEMPTS = 3
CLAIM_LEASE = timedelta(minutes=30)
COST_CATEGORIES = ("llm", "image", "video", "audio", "tool", "subscription", "human", "other")
POST_LIMITS = {"x": 280, "threads": 500, "linkedin": 3000, "instagram": 2200, "tiktok": 2200, "facebook": 2000}


# What can be made from an anchor, and where it can go. The series planner offers exactly these.
DERIVATIVES: dict[str, tuple[str, ...]] = {
    "thread": ("x", "threads"),
    "post": ("linkedin", "facebook", "reddit", "instagram"),
    "carousel": ("instagram", "linkedin", "tiktok"),
    "short_video": ("tiktok", "instagram", "youtube", "linkedin"),
    "newsletter": ("email",),
}
MAX_PER_CELL = 12
ANCHOR_KINDS = ("blog", "newsletter")

SETTINGS: dict[str, dict[str, str]] = {
    "XAI_API_KEY": {"label": "xAI API key", "kind": "secret", "group": "Generation", "help": "Grok Imagine stills and video."},
    "OPENROUTER_API_KEY": {"label": "OpenRouter API key", "kind": "secret", "group": "Generation", "help": "Seedance video."},
    "ELEVENLABS_API_KEY": {"label": "ElevenLabs API key", "kind": "secret", "group": "Generation", "help": "Voiceover with word timings."},
    "ELEVENLABS_VOICE_ID": {"label": "ElevenLabs voice id", "kind": "text", "group": "Generation", "help": "Default voice for voiceovers."},
    "CMSBOT_SEEDANCE_MODEL": {"label": "Seedance model id", "kind": "text", "group": "Generation",
                              "help": "Leave empty to use the newest Seedance model OpenRouter lists."},
    "CMSBOT_RATE_XAI_IMAGE": {"label": "Grok still, $ each", "kind": "number", "group": "Rates for spend checks"},
    "CMSBOT_RATE_XAI_VIDEO_PER_SECOND": {"label": "Grok video, $ per second", "kind": "number", "group": "Rates for spend checks"},
    "CMSBOT_RATE_SEEDANCE_PER_SECOND": {"label": "Seedance, $ per second", "kind": "number", "group": "Rates for spend checks"},
    "CMSBOT_RATE_ELEVENLABS_PER_1K_CHARS": {"label": "ElevenLabs, $ per 1,000 characters", "kind": "number", "group": "Rates for spend checks"},
}


class HubError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Actor:
    name: str
    role: str
    project: str | None = None  # None = every project

    def can(self, *roles: str) -> bool:
        return self.role in roles or (self.role == "agent" and any(r in AGENT_COVERS for r in roles))

    @property
    def drafts(self) -> bool:
        """True for agents that write. They are bound by the drafting limits: no edits once a piece is with the reviewer."""
        return self.role in ("generator", "agent")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def content_hash(item: dict[str, Any]) -> str | None:
    """Hash of what would be posted. Approval is bound to it."""
    if not item.get("body"):
        return None
    payload = {"title": item["title"], "body": item["body"], "media": item.get("media") or [], "link": item.get("link") or ""}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def tracked_link(link: str, *, channel: str, campaign: str, item_id: int) -> str:
    """Adds UTM tags so GA4 / Mixpanel can attribute a visit to one item (utm_content=cmsbot-<id>)."""
    if not link:
        return ""
    parts = urlsplit(link)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    query += [
        ("utm_source", channel), ("utm_medium", "social" if channel not in ("email", "blog") else channel),
        ("utm_campaign", campaign or "cmsbot"), ("utm_content", f"cmsbot-{item_id}"),
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Hub:
    def __init__(self, store: Store, public_url: str = ""):
        self.db = store
        self.public_url = public_url.rstrip("/")  # so posting agents get fetchable media links
        self.vault = Vault(store.path)

    # ---- tokens -------------------------------------------------------------------------

    def create_token(self, name: str, role: str, project: str | None = None, *, expires_in_days: float | None = None,
                     created_by: str = "") -> str:
        if role not in ROLES:
            raise HubError(f"Unknown role '{role}'. Use one of: {', '.join(ROLES)}")
        if not name.strip():
            raise HubError("Give the token a name: who or what will use it.")
        if project:
            self._project(project)
        token = f"cms_{role[:3]}_{secrets.token_urlsafe(32)}"
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).strftime("%Y-%m-%dT%H:%M:%SZ") if expires_in_days else None
        self.db.insert("tokens", {"name": name.strip(), "role": role, "project": project or None, "token_hash": hash_token(token),
                                  "created_at": now_iso(), "expires_at": expires, "created_by": created_by})
        return token

    def authenticate(self, token: str) -> Actor | None:
        if not token:
            return None
        row = self.db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked = 0", (hash_token(token),))
        if not row or (row["expires_at"] and row["expires_at"] < now_iso()):
            return None
        stamp = now_iso()
        if (row["last_used_at"] or "")[:16] != stamp[:16]:  # at most one write a minute per token
            self.db.update("tokens", "id", row["id"], {"last_used_at": stamp})
        return Actor(row["name"], row["role"], row["project"])

    def list_tokens(self, actor: Actor | None = None) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT id, name, role, project, created_at, created_by, last_used_at, expires_at, revoked "
                           "FROM tokens ORDER BY revoked, id DESC")
        if actor is not None:
            self._require(actor, "admin")
            rows = [r for r in rows if not actor.project or r["project"] == actor.project]
        for row in rows:
            row["expired"] = bool(row["expires_at"]) and row["expires_at"] < now_iso()
        return rows

    def _manageable(self, actor: Actor, project: str | None) -> None:
        """A reviewer limited to one project can only hand out or withdraw access to that project."""
        self._require(actor, "admin")
        if actor.project and project != actor.project:
            raise HubError(f"This token can only manage access to '{actor.project}'.", 403)

    def issue_token(self, actor: Actor, name: str, role: str, project: str | None = None,
                    expires_in_days: float | None = None) -> dict[str, Any]:
        self._manageable(actor, project or None)
        token = self.create_token(name, role, project or None, expires_in_days=expires_in_days, created_by=actor.name)
        row = self.db.one("SELECT id, name, role, project, expires_at FROM tokens WHERE token_hash = ?", (hash_token(token),)) or {}
        self._event(actor, "token_created", project=project or None, detail={"name": name, "role": role})
        return {**row, "token": token}

    def revoke_token(self, token_id: int, actor: Actor | None = None) -> None:
        row = self.db.one("SELECT * FROM tokens WHERE id = ?", (token_id,))
        if not row:
            raise HubError(f"No token {token_id}.", 404)
        if actor is not None:
            self._manageable(actor, row["project"])
        if row["role"] == "admin" and not row["project"] and not row["revoked"]:
            others = self.db.one("SELECT COUNT(*) AS n FROM tokens WHERE role = 'admin' AND project IS NULL AND revoked = 0 "
                                 "AND id != ? AND (expires_at IS NULL OR expires_at > ?)", (token_id, now_iso()))
            if not (others or {}).get("n"):
                raise HubError("This is the last reviewer token for all projects. Create another before revoking it, "
                               "or nobody can sign in.", 409)
        self.db.update("tokens", "id", token_id, {"revoked": 1})
        if actor is not None:
            self._event(actor, "token_revoked", project=row["project"], detail={"name": row["name"], "role": row["role"]})

    def rotate_token(self, actor: Actor, token_id: int) -> dict[str, Any]:
        """Issue a replacement with the same name, role and project, then withdraw the old one."""
        row = self.db.one("SELECT * FROM tokens WHERE id = ? AND revoked = 0", (token_id,))
        if not row:
            raise HubError(f"No active token {token_id}.", 404)
        self._manageable(actor, row["project"])
        remaining = None
        if row["expires_at"]:
            remaining = max(1.0, (parse_iso(row["expires_at"]) - parse_iso(row["created_at"])).total_seconds() / 86400)
        fresh = self.issue_token(actor, row["name"], row["role"], row["project"], remaining)
        self.db.update("tokens", "id", token_id, {"revoked": 1})
        return fresh

    # ---- first-run setup --------------------------------------------------------------------

    def needs_setup(self) -> bool:
        return not self.db.one("SELECT id FROM tokens WHERE role = 'admin' AND project IS NULL AND revoked = 0 LIMIT 1")

    def setup_code(self) -> str | None:
        """A one-time code for creating the first reviewer, shown in the server log. None once set up."""
        if not self.needs_setup():
            return None
        code = secrets.token_urlsafe(12)
        self.db.execute("INSERT INTO meta (key, value) VALUES ('setup_code', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (hash_token(code),))
        return code

    def complete_setup(self, code: str, reviewer: str) -> str:
        stored = self.db.one("SELECT value FROM meta WHERE key = 'setup_code'")
        if not self.needs_setup() or not stored:
            raise HubError("This Hub is already set up. Sign in with a reviewer token.", 409)
        if not secrets.compare_digest(stored["value"], hash_token(code.strip())):
            raise HubError("That setup code is not right. It is printed in the Hub's log when it starts.", 403)
        token = self.create_token(reviewer or "reviewer", "admin", created_by="setup")
        self.db.execute("DELETE FROM meta WHERE key = 'setup_code'")
        return token

    # ---- provider keys and other settings ---------------------------------------------------

    def set_setting(self, actor: Actor, name: str, value: str, project: str | None = None) -> dict[str, Any]:
        self._manageable(actor, project or None)
        if name not in SETTINGS:
            raise HubError(f"Unknown setting '{name}'.")
        if project:
            self._project(project)
        value = value.strip()
        if not value:
            raise HubError("The value is empty. Use remove to clear a setting.")
        if SETTINGS[name]["kind"] == "number":
            try:
                float(value)
            except ValueError as exc:
                raise HubError(f"{SETTINGS[name]['label']} must be a number.") from exc
        hint = f"…{value[-4:]}" if SETTINGS[name]["kind"] == "secret" and len(value) > 8 else ("set" if SETTINGS[name]["kind"] == "secret" else value)
        self.db.execute("DELETE FROM settings WHERE project = ? AND name = ?", (project or "", name))
        self.db.insert("settings", {"project": project or "", "name": name, "sealed": self.vault.seal(value), "hint": hint,
                                    "updated_by": actor.name, "updated_at": now_iso()})
        self._event(actor, "setting_changed", project=project or None, detail={"name": name})
        return {"name": name, "project": project or "", "hint": hint}

    def clear_setting(self, actor: Actor, name: str, project: str | None = None) -> None:
        self._manageable(actor, project or None)
        self.db.execute("DELETE FROM settings WHERE project = ? AND name = ?", (project or "", name))
        self._event(actor, "setting_removed", project=project or None, detail={"name": name})

    def setting(self, name: str, project: str | None = None) -> str | None:
        """Internal: the value in effect. A project's own value, then the Hub-wide one, then the environment."""
        for scope in ([project, ""] if project else [""]):
            row = self.db.one("SELECT sealed FROM settings WHERE project = ? AND name = ?", (scope, name))
            if row:
                value = self.vault.open(row["sealed"])
                if value:
                    return value
        return os.environ.get(name) or None

    def list_settings(self, actor: Actor, project: str | None = None) -> list[dict[str, Any]]:
        """Never returns a secret's value: only whether it is set, its last four characters, and who set it."""
        self._manageable(actor, project or None)
        stored = {(r["project"], r["name"]): r for r in self.db.all("SELECT project, name, hint, updated_by, updated_at FROM settings")}
        out = []
        for name, spec in SETTINGS.items():
            own = stored.get((project or "", name))
            inherited = stored.get(("", name)) if project else None
            source = "this project" if own and project else "hub" if (own or inherited) else "environment" if os.environ.get(name) else ""
            row = own or inherited
            out.append({"name": name, "label": spec["label"], "kind": spec["kind"], "group": spec["group"], "help": spec.get("help", ""),
                        "source": source, "hint": (row or {}).get("hint", "set" if source == "environment" else ""),
                        "updated_by": (row or {}).get("updated_by", ""), "updated_at": (row or {}).get("updated_at", ""),
                        "own": bool(own)})
        return out

    # ---- guards -------------------------------------------------------------------------

    def _require(self, actor: Actor, *roles: str) -> None:
        if not actor.can(*roles):
            raise HubError(f"Role '{actor.role}' may not do this (needs {' or '.join(roles)}).", 403)

    def _scope(self, actor: Actor, project: str) -> None:
        if actor.project and actor.project != project:
            raise HubError(f"This token is limited to project '{actor.project}'.", 403)

    def _project(self, slug: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM projects WHERE slug = ?", (slug,))
        if not row:
            raise HubError(f"No project '{slug}'.", 404)
        return row

    def _item(self, actor: Actor, item_id: int) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM items WHERE id = ?", (item_id,))
        if not row:
            raise HubError(f"No item {item_id}.", 404)
        self._scope(actor, row["project"])
        return row

    def brand(self, project: str) -> dict[str, Any]:
        row = self._project(project)
        return {"name": row["name"], **row["brand"]}

    def slides_stale(self, item: dict[str, Any]) -> bool:
        """True when a carousel's slide images were not made from its current slide text."""
        if item["kind"] != "carousel" or not item["body"].get("slides"):
            return False
        style = carousel.resolve_style(item["body"], self.brand(item["project"]))
        return (item["audit"].get("render") or {}).get("hash") != carousel.slides_hash(item["body"], style)

    @staticmethod
    def video_assets(item: dict[str, Any]) -> dict[str, str]:
        """Footage and sound attached to a video item, by role (shot-01, voiceover, music, timing)."""
        wanted = re.compile(r"shot-\d{2}|voiceover|music|timing")
        return {m["role"]: m["url"] for m in item["media"] if isinstance(m, dict) and wanted.fullmatch(str(m.get("role", "")))}

    def video_state(self, item: dict[str, Any]) -> str:
        """'' (not a video), 'script' (words not approved yet, or nothing rendered), 'stale' or 'current'.
        A preview rendered before the script is approved does not skip the script review."""
        if item["kind"] != "short_video":
            return ""
        if not item["audit"].get("script_approved"):
            return "script"
        rendered = (item["audit"].get("video") or {}).get("hash")
        if not rendered or not any(isinstance(m, dict) and m.get("role") == "video" for m in item["media"]):
            return "script"
        return "current" if rendered == video.storyboard_hash(item["body"], self.video_assets(item)) else "stale"

    def _event(self, actor: Actor, action: str, *, item: dict[str, Any] | None = None, project: str | None = None,
               detail: dict[str, Any] | None = None) -> None:
        self.db.insert("events", {
            "item_id": item["id"] if item else None, "project": item["project"] if item else project,
            "actor": actor.name, "role": actor.role, "action": action, "detail": detail or {}, "at": now_iso(),
        })

    # ---- projects, pillars, clusters ------------------------------------------------------

    def upsert_project(self, actor: Actor, slug: str, name: str, *, brand: dict[str, Any] | None = None,
                       settings: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require(actor, "admin")
        slug = slugify(slug)
        self._scope(actor, slug)
        existing = self.db.one("SELECT * FROM projects WHERE slug = ?", (slug,))
        if existing:
            values: dict[str, Any] = {"name": name or existing["name"]}
            if brand is not None:
                values["brand"] = brand
            if settings is not None:
                values["settings"] = {**existing["settings"], **settings}
            self.db.update("projects", "slug", slug, values)
        else:
            self.db.insert("projects", {"slug": slug, "name": name or slug, "brand": brand or {},
                                        "settings": settings or {}, "created_at": now_iso()})
        return self._project(slug)

    def list_projects(self, actor: Actor) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM projects ORDER BY name")
        return [row for row in rows if not actor.project or actor.project == row["slug"]]

    def upsert_pillar(self, actor: Actor, project: str, key: str, name: str, *, description: str = "",
                      weight: float = 1.0, status: str = "active") -> dict[str, Any]:
        self._require(actor, "admin")
        self._scope(actor, project)
        self._project(project)
        key = slugify(key)
        existing = self.db.one("SELECT * FROM pillars WHERE project = ? AND key = ?", (project, key))
        values = {"name": name, "description": description, "weight": weight, "status": status}
        if existing:
            self.db.update("pillars", "id", existing["id"], values)
        else:
            self.db.insert("pillars", {"project": project, "key": key, **values})
        return self.db.one("SELECT * FROM pillars WHERE project = ? AND key = ?", (project, key)) or {}

    def create_cluster(self, actor: Actor, project: str, name: str, *, pillar: str | None = None, theme: str = "",
                       keywords: list[str] | None = None, starts_on: str | None = None, ends_on: str | None = None,
                       origin: str = "pillar", signal_id: int | None = None) -> dict[str, Any]:
        """A cluster is one theme for one period (usually a week) under a pillar, or a reaction to a trend."""
        self._require(actor, "admin", "generator")
        self._scope(actor, project)
        self._project(project)
        pillar_id = None
        if pillar:
            row = self.db.one("SELECT id FROM pillars WHERE project = ? AND key = ?", (project, slugify(pillar)))
            if not row:
                raise HubError(f"No pillar '{pillar}' in {project}. Pillars are set by an admin.", 404)
            pillar_id = row["id"]
        if origin not in ("pillar", "trend"):
            raise HubError("origin must be 'pillar' or 'trend'.")
        slug = slugify(name)
        if self.db.one("SELECT id FROM clusters WHERE project = ? AND slug = ?", (project, slug)):
            raise HubError(f"Cluster '{slug}' already exists in {project}.", 409)
        cluster_id = self.db.insert("clusters", {
            "project": project, "pillar_id": pillar_id, "slug": slug, "name": name, "theme": theme,
            "keywords": keywords or [], "starts_on": starts_on, "ends_on": ends_on, "origin": origin,
            "created_at": now_iso(),
        })
        if signal_id:
            self.db.update("signals", "id", signal_id, {"status": "used", "cluster_id": cluster_id},
                           where="project = ?", where_params=(project,))
        self._event(actor, "cluster_created", project=project, detail={"cluster_id": cluster_id, "origin": origin})
        return self.db.one("SELECT * FROM clusters WHERE id = ?", (cluster_id,)) or {}

    # ---- brand kit ------------------------------------------------------------------------------

    def get_brand(self, actor: Actor, project: str) -> dict[str, Any]:
        self._scope(actor, project)
        row = self._project(project)
        return {"project": project, "name": row["name"], "brand": row["brand"], "draft": row["settings"].get("brand_draft")}

    def set_brand(self, actor: Actor, project: str, incoming: dict[str, Any], *, name: str | None = None) -> dict[str, Any]:
        from cmsbot.hub import brand as brand_kit

        self._manageable(actor, project)
        row = self._project(project)
        values: dict[str, Any] = {"brand": brand_kit.clean(incoming, row["brand"]),
                                  "settings": {k: v for k, v in row["settings"].items() if k != "brand_draft"}}
        if name and name.strip():
            values["name"] = name.strip()[:120]
        self.db.update("projects", "slug", project, values)
        self._event(actor, "brand_saved", project=project)
        return self.get_brand(actor, project)

    def propose_brand(self, actor: Actor, project: str, incoming: dict[str, Any]) -> dict[str, Any]:
        """An agent's draft of the brand kit. It changes nothing until a person loads and saves it."""
        from cmsbot.hub import brand as brand_kit

        self._require(actor, "admin", "generator", "analyst")
        self._scope(actor, project)
        row = self._project(project)
        draft = {**brand_kit.clean(incoming, {}), "proposed_by": actor.name, "proposed_at": now_iso()}
        self.db.update("projects", "slug", project, {"settings": {**row["settings"], "brand_draft": draft}})
        self._event(actor, "brand_proposed", project=project)
        return {"project": project, "status": "waiting for a reviewer to accept it on the Brand tab"}

    # ---- series: an anchor and the pieces made from it ----------------------------------------

    @staticmethod
    def _clean_plan(plan: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for row in plan or []:
            kind, channel = str(row.get("kind", "")), str(row.get("channel", ""))
            if channel not in DERIVATIVES.get(kind, ()):
                raise HubError(f"'{kind}' on '{channel}' is not something the planner offers.")
            try:
                count = int(row.get("count", 0))
            except (TypeError, ValueError) as exc:
                raise HubError("Counts must be whole numbers.") from exc
            if not 0 <= count <= MAX_PER_CELL:
                raise HubError(f"Counts go from 0 to {MAX_PER_CELL}.")
            out[(kind, channel)] = count
        return out

    def set_recipe(self, actor: Actor, project: str, recipe: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The project's default series: what each new anchor gets unless someone chooses otherwise."""
        self._manageable(actor, project)
        row = self._project(project)
        clean = [{"kind": k, "channel": c, "count": n} for (k, c), n in self._clean_plan(recipe).items() if n]
        self.db.update("projects", "slug", project, {"settings": {**row["settings"], "recipe": clean}})
        return clean

    def series(self, actor: Actor, anchor_id: int) -> dict[str, Any]:
        anchor = self._item(actor, anchor_id)
        if anchor["parent_id"]:
            raise HubError(f"Item {anchor_id} is itself made from #{anchor['parent_id']}; plan the series on that one.")
        children = self.db.all("SELECT id, kind, channel, title, status, scheduled_at FROM items WHERE parent_id = ? AND status != 'rejected' "
                               "ORDER BY kind, channel, id", (anchor_id,))
        counts: dict[tuple[str, str], int] = {}
        for child in children:
            counts[(child["kind"], child["channel"])] = counts.get((child["kind"], child["channel"]), 0) + 1
        recipe = self._project(anchor["project"])["settings"].get("recipe") or []
        return {
            "anchor": {"id": anchor["id"], "title": anchor["title"], "status": anchor["status"], "project": anchor["project"]},
            "options": [{"kind": k, "channels": list(c)} for k, c in DERIVATIVES.items()],
            "current": [{"kind": k, "channel": c, "count": n} for (k, c), n in sorted(counts.items())],
            "recipe": recipe, "uses_recipe": not children and bool(recipe), "children": children,
        }

    def plan_series(self, actor: Actor, anchor_id: int, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """Set how many of each piece this anchor should have. Adds planned items for a drafting agent to write;
        removes only planned items nobody has started. Anything drafted, in review or posted is left alone."""
        self._require(actor, "admin", "generator")
        anchor = self._item(actor, anchor_id)
        if anchor["parent_id"]:
            raise HubError("Plan the series on the anchor, not on one of its pieces.")
        wanted = self._clean_plan(plan)
        link = anchor["link"] or ((self.db.one("SELECT post_url FROM publications WHERE item_id = ? ORDER BY id DESC LIMIT 1",
                                               (anchor_id,)) or {}).get("post_url") or "")
        added, removed, kept = 0, 0, []
        for (kind, channel), count in wanted.items():
            existing = self.db.all("SELECT id, status, body FROM items WHERE parent_id = ? AND kind = ? AND channel = ? "
                                   "AND status != 'rejected' ORDER BY id", (anchor_id, kind, channel))
            for number in range(len(existing) + 1, count + 1):
                stamp = now_iso()
                label = kind.replace("_", " ").capitalize()
                self.db.insert("items", {
                    "project": anchor["project"], "cluster_id": anchor["cluster_id"], "parent_id": anchor_id, "kind": kind,
                    "channel": channel, "title": f"{label} {number} from: {anchor['title']}"[:300], "link": link,
                    "audit": {"brief": f"Write {label.lower()} {number} of {count} for {channel} from anchor #{anchor_id}. "
                                       "Take a different angle from the others in this series."},
                    "status": "planned", "created_by": actor.name, "created_at": stamp, "updated_at": stamp})
                added += 1
            surplus = len(existing) - count
            for child in reversed(existing):
                if surplus <= 0:
                    break
                if child["status"] == "planned" and not child["body"]:
                    self.db.execute("DELETE FROM events WHERE item_id = ?", (child["id"],))
                    self.db.execute("DELETE FROM items WHERE id = ?", (child["id"],))
                    removed += 1
                    surplus -= 1
            if surplus > 0:
                kept.append(f"{surplus} {kind.replace('_', ' ')} on {channel} already started and kept")
        self._event(actor, "series_planned", item=anchor, detail={"added": added, "removed": removed})
        return {**self.series(actor, anchor_id), "added": added, "removed": removed, "kept": kept}

    def import_published(self, actor: Actor, project: str, *, url: str, title: str, markdown: str,
                         published_at: str | None = None, cluster_id: int | None = None) -> dict[str, Any]:
        """Bring in a piece that is already live so a series can be made from it. It is recorded as published and
        never enters review or posting. Its claims still need a person to verify them where the project requires it."""
        self._require(actor, "admin", "generator")
        self._scope(actor, project)
        self._project(project)
        if not url.startswith(("http://", "https://")):
            raise HubError("Give the public address of the published piece.")
        if not title.strip() or not markdown.strip():
            raise HubError("The piece needs a title and its text.")
        existing = self.db.one("SELECT id FROM items WHERE project = ? AND external_ref = ?", (project, f"import:{url}"))
        if existing:
            raise HubError(f"That address was already imported as item {existing['id']}.", 409)
        stamp = now_iso()
        posted = parse_iso(published_at).strftime("%Y-%m-%dT%H:%M:%SZ") if published_at else stamp
        item_id = self.db.insert("items", {
            "project": project, "cluster_id": cluster_id, "kind": "blog", "channel": "blog", "title": title.strip()[:300],
            "body": {"markdown": markdown.strip()}, "link": url, "status": "published", "external_ref": f"import:{url}",
            "audit": {"imported": {"by": actor.name, "at": stamp, "from": url}}, "created_by": actor.name,
            "created_at": stamp, "updated_at": stamp})
        item = self._item(actor, item_id)
        self._revision(actor, item, "imported")
        self.db.insert("publications", {"item_id": item_id, "channel": "blog", "account": "", "post_url": url, "post_id": "",
                                        "posted_at": posted, "posted_by": "imported"})
        self._event(actor, "imported", item=item, detail={"url": url})
        recipe = self._project(project)["settings"].get("recipe") or []
        if recipe:
            self.plan_series(actor, item_id, recipe)
        return self.get_item(actor, item_id)

    def reread_import(self, actor: Actor, item_id: int, title: str, markdown: str) -> dict[str, Any]:
        """Replace an imported anchor's text with a fresh reading of its page. Its claims and series are kept."""
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if not item["audit"].get("imported"):
            raise HubError("Only imported pieces can be read again from their page.")
        if not markdown.strip():
            raise HubError("The page had no readable text.")
        self.db.update("items", "id", item_id, {"title": (title.strip() or item["title"])[:300], "body": {"markdown": markdown.strip()},
                                                "updated_at": now_iso()})
        updated = self._item(actor, item_id)
        self._revision(actor, updated, "read again from the page")
        self._event(actor, "reimported", item=updated, detail={"words": len(markdown.split())})
        return self.get_item(actor, item_id)

    # ---- brief: everything a drafting agent needs before it writes -------------------------

    def brief(self, actor: Actor, project: str) -> dict[str, Any]:
        self._scope(actor, project)
        row = self._project(project)
        counts = self.db.all("SELECT status, COUNT(*) AS n FROM items WHERE project = ? GROUP BY status", (project,))
        open_feedback = self.db.all(
            "SELECT id, kind, channel, title, review_notes FROM items WHERE project = ? AND status = 'needs_revision'",
            (project,))
        pillars = self.db.all("SELECT key, name, description, weight FROM pillars WHERE project = ? AND status = 'active'", (project,))
        return {
            "project": {"slug": row["slug"], "name": row["name"]},
            "brand": row["brand"],
            "settings": row["settings"],
            "pillars": pillars,
            "clusters": self.db.all(
                "SELECT c.id, c.slug, c.name, c.theme, c.keywords, c.starts_on, c.ends_on, c.origin, c.status, p.key AS pillar "
                "FROM clusters c LEFT JOIN pillars p ON p.id = c.pillar_id WHERE c.project = ? AND c.status != 'archived' "
                "ORDER BY c.id DESC LIMIT 20", (project,)),
            "learnings": self.db.all("SELECT id, statement, scope FROM learnings WHERE project = ? AND status = 'accepted' ORDER BY id DESC", (project,)),
            "signals": self.list_signals(actor, project, status="new"),
            "items_by_status": {r["status"]: r["n"] for r in counts},
            "needs_revision": open_feedback,
            "warnings": [w for w in (
                "" if (row["brand"].get("tone") or row["brand"].get("voice")) else
                "No brand kit yet: there is no guidance on tone or words to avoid. Write plainly, and tell the owner the Brand tab is empty.",
                "" if pillars else "No pillars are set for this project.",
            ) if w],
            "to_write": self.db.all("SELECT id, kind, channel, title, parent_id, audit FROM items WHERE project = ? AND status = 'planned' "
                                    "ORDER BY id LIMIT 100", (project,)),
            "spend": self.spend(actor, project),
        }

    # ---- items ---------------------------------------------------------------------------

    def create_item(self, actor: Actor, project: str, *, kind: str, channel: str, title: str,
                    cluster_id: int | None = None, parent_id: int | None = None, account: str = "",
                    body: dict[str, Any] | None = None, media: list[Any] | None = None,
                    attributes: dict[str, Any] | None = None, link: str = "", gaps: list[str] | None = None,
                    audit: dict[str, Any] | None = None, external_ref: str | None = None) -> dict[str, Any]:
        self._require(actor, "admin", "generator")
        self._scope(actor, project)
        self._project(project)
        if kind not in KINDS:
            raise HubError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}")
        if channel not in CHANNELS:
            raise HubError(f"Unknown channel '{channel}'. Use one of: {', '.join(CHANNELS)}")
        if external_ref:
            existing = self.db.one("SELECT * FROM items WHERE project = ? AND external_ref = ?", (project, external_ref))
            if existing:  # idempotent re-push from the same source
                if actor.drafts and existing["status"] not in AGENT_EDITABLE:
                    return self.get_item(actor, existing["id"])  # with the reviewer; leave it alone
                return self.update_item(actor, existing["id"], title=title, body=body, media=media,
                                        attributes=attributes, link=link, gaps=gaps, audit=audit)
        if parent_id:
            parent = self._item(actor, parent_id)
            if parent["project"] != project:
                raise HubError("Parent item belongs to another project.")
            cluster_id = cluster_id or parent["cluster_id"]
        stamp = now_iso()
        item_id = self.db.insert("items", {
            "project": project, "cluster_id": cluster_id, "parent_id": parent_id, "kind": kind, "channel": channel,
            "account": account, "title": title, "body": body or {}, "media": media or [],
            "attributes": attributes or {}, "link": link, "gaps": gaps or [], "audit": audit or {},
            "status": "drafting" if body else "planned", "created_by": actor.name, "external_ref": external_ref,
            "created_at": stamp, "updated_at": stamp,
        })
        item = self._item(actor, item_id)
        if body:
            self._revision(actor, item, "created")
        self._event(actor, "created", item=item)
        return self.get_item(actor, item_id)

    def _revision(self, actor: Actor, item: dict[str, Any], note: str) -> None:
        last = self.db.one("SELECT MAX(n) AS n FROM revisions WHERE item_id = ?", (item["id"],))
        self.db.insert("revisions", {
            "item_id": item["id"], "n": int((last or {}).get("n") or 0) + 1, "title": item["title"],
            "body": item["body"], "author": actor.name, "role": actor.role, "note": note, "created_at": now_iso(),
        })

    def update_item(self, actor: Actor, item_id: int, *, title: str | None = None, body: dict[str, Any] | None = None,
                    media: list[Any] | None = None, attributes: dict[str, Any] | None = None, link: str | None = None,
                    gaps: list[str] | None = None, audit: dict[str, Any] | None = None, account: str | None = None,
                    scheduled_at: str | None = None, note: str = "") -> dict[str, Any]:
        """Agents edit only while an item is theirs to edit. A person can edit until it is posted;
        editing approved copy withdraws the approval (use `approve` again, or `edit_and_approve`)."""
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if actor.drafts and item["status"] not in AGENT_EDITABLE:
            raise HubError(f"Item {item_id} is '{item['status']}' and is with the reviewer; agents can't change it now.", 409)
        if item["status"] in ("posting", "published"):
            raise HubError(f"Item {item_id} is {item['status']} and can no longer be edited.", 409)
        values: dict[str, Any] = {}
        for key, value in (("title", title), ("body", body), ("media", media), ("link", link), ("gaps", gaps),
                           ("account", account)):
            if value is not None and value != item[key]:
                values[key] = value
        if attributes is not None:
            values["attributes"] = {**item["attributes"], **attributes}
        if audit is not None:
            values["audit"] = {**item["audit"], **audit}
        if scheduled_at is not None and actor.role == "admin":
            values["scheduled_at"] = parse_iso(scheduled_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled_at else None
        if not values:
            return self.get_item(actor, item_id)
        touches_copy = bool(values.keys() & {"title", "body", "media", "link"})
        if touches_copy and item["status"] in READY:
            values.update(status="in_review", approved_hash=None, approved_by=None, approved_at=None)
        elif actor.drafts and item["status"] in ("planned", "needs_revision", "failed") and touches_copy:
            values["status"] = "drafting"
        values["updated_at"] = now_iso()
        self.db.update("items", "id", item_id, values)
        updated = self._item(actor, item_id)
        if touches_copy:
            self._revision(actor, updated, note or "edited")
        self._event(actor, "edited", item=updated, detail={"fields": sorted(k for k in values if k != "updated_at"), "note": note})
        return self.get_item(actor, item_id)

    def add_media(self, actor: Actor, item_id: int, url: str, *, name: str = "", role: str = "") -> dict[str, Any]:
        item = self._item(actor, item_id)
        entry = {"url": url, "name": name, **({"role": role} if role else {})}
        return self.update_item(actor, item_id, media=[*item["media"], entry], note=f"attached {name or url}")

    def set_render(self, actor: Actor, item_id: int, urls: list[str], *, style: str, render_hash: str) -> dict[str, Any]:
        """Attach freshly rendered slide images. Rendering changes no words, so a drafting agent may do it
        while the item is in review; it is refused if the slide text has moved on since the render."""
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if item["status"] in ("posting", "published"):
            raise HubError(f"Item {item_id} is {item['status']}.", 409)
        if actor.drafts and item["status"] in READY:
            raise HubError("Already approved; a reviewer must re-render.", 409)
        current = carousel.slides_hash(item["body"], carousel.resolve_style(item["body"], self.brand(item["project"]), style))
        if render_hash != current:
            raise HubError("The slide text changed after these images were rendered; render again.", 409)
        media = [m for m in item["media"] if not (isinstance(m, dict) and m.get("role") == "slide")]
        media += [{"url": url, "role": "slide", "name": f"slide-{n:02d}.png"} for n, url in enumerate(urls, start=1)]
        values: dict[str, Any] = {"media": media, "audit": {**item["audit"], "render": {"style": style, "hash": render_hash}},
                                  "updated_at": now_iso()}
        if item["status"] in READY:
            values.update(status="in_review", approved_hash=None, approved_by=None, approved_at=None)
        self.db.update("items", "id", item_id, values)
        self._event(actor, "rendered", item=item, detail={"slides": len(urls), "style": style})
        return self.get_item(actor, item_id)

    def set_video(self, actor: Actor, item_id: int, video_url: str, *, contact_sheet_url: str = "",
                  report: dict[str, Any]) -> dict[str, Any]:
        """Attach a rendered video. Refused if the script or footage changed since it was rendered."""
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if item["status"] in ("posting", "published"):
            raise HubError(f"Item {item_id} is {item['status']}.", 409)
        if actor.drafts and item["status"] in READY:
            raise HubError("Already approved; a reviewer must re-render.", 409)
        if report.get("hash") != video.storyboard_hash(item["body"], self.video_assets(item)):
            raise HubError("The script or footage changed after this video was rendered; render again.", 409)
        media = [m for m in item["media"] if not (isinstance(m, dict) and m.get("role") in ("video", "contact_sheet"))]
        media.append({"url": video_url, "role": "video", "name": "video.mp4"})
        if contact_sheet_url:
            media.append({"url": contact_sheet_url, "role": "contact_sheet", "name": "contact-sheet.jpg"})
        summary = {k: report.get(k) for k in ("hash", "seconds", "timing", "checks", "file")}
        values: dict[str, Any] = {"media": media, "audit": {**item["audit"], "video": summary}, "updated_at": now_iso()}
        if item["status"] in READY:
            values.update(status="in_review", approved_hash=None, approved_by=None, approved_at=None)
        self.db.update("items", "id", item_id, values)
        self._event(actor, "video_rendered", item=item, detail={"seconds": report.get("seconds")})
        return self.get_item(actor, item_id)

    def approve_script(self, actor: Actor, item_id: int, notes: str = "") -> dict[str, Any]:
        """First of two reviews for a video: the words are right, go and make it. The item returns to the
        drafting agent, which attaches footage and voiceover, renders, and submits the video for the second review."""
        self._require(actor, "admin")
        item = self._item(actor, item_id)
        if item["kind"] != "short_video":
            raise HubError("Only video items have a script stage.")
        if item["status"] not in ("in_review", "drafting", "needs_revision"):
            raise HubError(f"Item {item_id} is '{item['status']}'.", 409)
        blocking = [b for b in self.blockers(item) if not b.startswith(("Script stage", "Script approved"))]
        if blocking:
            raise HubError("Can't approve the script yet: " + "; ".join(blocking), 409)
        note = "Script approved. Produce the video (footage, voiceover, render) and submit it." + (f" {notes.strip()}" if notes.strip() else "")
        self.db.update("items", "id", item_id, {
            "status": "needs_revision", "review_notes": note, "updated_at": now_iso(),
            "audit": {**item["audit"], "script_approved": {"by": actor.name, "at": now_iso()}}})
        self._event(actor, "script_approved", item=item, detail={"notes": notes.strip()})
        return self.get_item(actor, item_id)

    def set_claims(self, actor: Actor, item_id: int, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace an anchor's claim ledger. Derivatives restate these instead of making new claims."""
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if item["parent_id"]:
            raise HubError("Claims live on the anchor item, not on a derivative.")
        imported = item["status"] == "published" and bool(item["audit"].get("imported"))
        if actor.drafts and item["status"] not in AGENT_EDITABLE and not imported:
            raise HubError("Claims can't be replaced once the item is with the reviewer.", 409)
        keep = {c["text"]: c for c in self.db.all("SELECT * FROM claims WHERE item_id = ?", (item_id,))}
        self.db.execute("DELETE FROM claims WHERE item_id = ?", (item_id,))
        for claim in claims:
            text = str(claim.get("text") or "").strip()
            if not text:
                continue
            previous = keep.get(text, {})  # an unchanged claim keeps its verification
            self.db.insert("claims", {
                "item_id": item_id, "text": text, "citation": str(claim.get("citation") or ""),
                "citation_url": str(claim.get("citation_url") or claim.get("citationUrl") or ""),
                "status": previous.get("status", "unverified"), "verified_by": previous.get("verified_by"),
                "verified_at": previous.get("verified_at"), "notes": previous.get("notes", ""),
            })
        return self.get_item(actor, item_id)

    def review_claim(self, actor: Actor, claim_id: int, *, status: str | None = None, text: str | None = None,
                     citation: str | None = None, citation_url: str | None = None, notes: str | None = None) -> dict[str, Any]:
        self._require(actor, "admin")
        claim = self.db.one("SELECT * FROM claims WHERE id = ?", (claim_id,))
        if not claim:
            raise HubError(f"No claim {claim_id}.", 404)
        self._item(actor, claim["item_id"])
        values: dict[str, Any] = {k: v for k, v in (("text", text), ("citation", citation),
                                                     ("citation_url", citation_url), ("notes", notes)) if v is not None}
        if status is not None:
            if status not in ("unverified", "verified", "rejected"):
                raise HubError("Claim status must be unverified, verified or rejected.")
            values.update(status=status, verified_by=actor.name if status != "unverified" else None,
                          verified_at=now_iso() if status != "unverified" else None)
        if values:
            self.db.update("claims", "id", claim_id, values)
        return self.db.one("SELECT * FROM claims WHERE id = ?", (claim_id,)) or {}

    def writing_checks(self, item: dict[str, Any]) -> list[dict[str, str]]:
        claims = self.db.all("SELECT * FROM claims WHERE item_id = ? ORDER BY id", (item["parent_id"] or item["id"],))
        return checks.run(item, claims, self.brand(item["project"]), POST_LIMITS.get(item["channel"]))

    def blockers(self, item: dict[str, Any]) -> list[str]:
        out: list[str] = []
        if content_hash(item) is None:
            out.append("Nothing to post yet.")
        out += [f"Open gap: {gap}" for gap in item["gaps"]]
        out += [c["detail"] for c in self.writing_checks(item) if c["check"] == "length"]
        if self.slides_stale(item):
            out.append("Slide images are missing or older than the slide text; render them again.")
        state = self.video_state(item)
        if state == "script":
            out.append("Script stage: approve the script to send it to production." if not item["audit"].get("script_approved")
                       else "Script approved; the video has not been rendered yet.")
        elif state == "stale":
            out.append("The video is older than the script or footage; render it again.")
        anchor_id = item["parent_id"] or item["id"]
        claims = self.db.all("SELECT * FROM claims WHERE item_id = ? ORDER BY id", (anchor_id,))
        refs = item["body"].get("claim_ids") or item["body"].get("claimIds") or []
        cited = {c["id"] for c in (checks.resolve_claim(r, claims) for r in refs) if c} if item["parent_id"] else None
        settings = self._project(item["project"])["settings"]
        for claim in claims:
            if cited is not None and claim["id"] not in cited:
                continue
            if claim["status"] == "rejected":
                out.append(f"Claim marked wrong: {claim['text'][:90]}")
            elif claim["status"] == "unverified" and settings.get("require_verified_claims"):
                out.append(f"Claim not verified: {claim['text'][:90]}")
        return out

    def get_item(self, actor: Actor, item_id: int) -> dict[str, Any]:
        item = self._item(actor, item_id)
        if actor.role == "distributor" and item["status"] not in READY | {"posting", "published"}:
            raise HubError(f"Item {item_id} is not approved.", 403)
        anchor_id = item["parent_id"] or item["id"]
        current = content_hash(item)
        item["content_hash"] = current
        item["changed_since_approval"] = bool(item["approved_hash"]) and item["approved_hash"] != current
        item["claims"] = self.db.all("SELECT * FROM claims WHERE item_id = ? ORDER BY id", (anchor_id,))
        refs = item["body"].get("claim_ids") or item["body"].get("claimIds") or []
        cited = {c["id"] for c in (checks.resolve_claim(r, item["claims"]) for r in refs) if c}
        for position, claim in enumerate(item["claims"], start=1):
            claim["label"] = f"L{position}"  # the reference a piece uses in claim_ids
            claim["cited"] = claim["id"] in cited
        item["blockers"] = self.blockers(item)
        item["slides_stale"] = self.slides_stale(item)
        item["active_jobs"] = self.db.all("SELECT id, kind, created_at FROM jobs WHERE item_id = ? AND status = 'running'", (item_id,))
        item["writing_checks"] = [] if actor.role == "distributor" else self.writing_checks(item)
        item["video_state"] = self.video_state(item)
        item["children"] = self.db.all("SELECT id, kind, channel, title, status FROM items WHERE parent_id = ?", (item_id,))
        item["publications"] = self.db.all("SELECT * FROM publications WHERE item_id = ? ORDER BY id", (item_id,))
        item["events"] = self.db.all("SELECT actor, role, action, detail, at FROM events WHERE item_id = ? ORDER BY id DESC LIMIT 30", (item_id,))
        item["revisions"] = self.db.all("SELECT n, author, role, note, created_at FROM revisions WHERE item_id = ? ORDER BY n DESC", (item_id,))
        item["cost_usd"] = round(sum(c["amount_usd"] for c in self.db.all("SELECT amount_usd FROM costs WHERE item_id = ?", (item_id,))), 4)
        item["tracked_link"] = self._tracked(item)
        item["char_limit"] = POST_LIMITS.get(item["channel"])
        return item

    def _tracked(self, item: dict[str, Any]) -> str:
        cluster = self.db.one("SELECT slug FROM clusters WHERE id = ?", (item["cluster_id"],)) if item["cluster_id"] else None
        return tracked_link(item["link"], channel=item["channel"], campaign=(cluster or {}).get("slug", ""), item_id=item["id"])

    def list_items(self, actor: Actor, *, project: str | None = None, status: str | None = None,
                   channel: str | None = None, kind: str | None = None, limit: int = 200,
                   anchors: bool = False) -> list[dict[str, Any]]:
        if actor.role == "distributor":
            raise HubError("Posting agents use ready_to_post, which only returns approved copy.", 403)
        project = actor.project or project
        clauses, params = ["1=1"], []
        if anchors:
            clauses.append("parent_id IS NULL AND (kind IN ('blog', 'newsletter') OR id IN (SELECT parent_id FROM items WHERE parent_id IS NOT NULL))")
        for column, value in (("project", project), ("channel", channel), ("kind", kind)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if status:
            wanted = [s.strip() for s in status.split(",") if s.strip()]
            clauses.append(f"status IN ({', '.join('?' for _ in wanted)})")
            params += wanted
        rows = self.db.all(
            "SELECT id, project, cluster_id, parent_id, kind, channel, account, title, status, scheduled_at, gaps, "
            "attributes, review_notes, error, updated_at FROM items WHERE " + " AND ".join(clauses) +
            " ORDER BY updated_at DESC LIMIT ?", (*params, max(1, min(limit, 500))))
        return rows

    def submit(self, actor: Actor, item_id: int) -> dict[str, Any]:
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if item["status"] not in ("drafting", "needs_revision", "planned", "failed"):
            raise HubError(f"Item {item_id} is '{item['status']}'; only drafts can be submitted.", 409)
        if content_hash(item) is None:
            raise HubError("Add the body before submitting.")
        self.db.update("items", "id", item_id, {"status": "in_review", "error": "", "review_notes": "", "updated_at": now_iso()})
        self._event(actor, "submitted", item=item)
        return self.get_item(actor, item_id)

    def fail(self, actor: Actor, item_id: int, error: str) -> dict[str, Any]:
        self._require(actor, "admin", "generator")
        item = self._item(actor, item_id)
        if item["status"] not in AGENT_EDITABLE:
            raise HubError(f"Item {item_id} is '{item['status']}'.", 409)
        self.db.update("items", "id", item_id, {"status": "failed", "error": error[:2000],
                                                "attempts": item["attempts"] + 1, "updated_at": now_iso()})
        self._event(actor, "failed", item=item, detail={"error": error[:500]})
        return self.get_item(actor, item_id)

    # ---- review (people only) --------------------------------------------------------------

    def approve(self, actor: Actor, item_id: int, *, scheduled_at: str | None = None) -> dict[str, Any]:
        self._require(actor, "admin")
        item = self._item(actor, item_id)
        if item["status"] not in ("in_review", "needs_revision", "drafting"):
            raise HubError(f"Item {item_id} is '{item['status']}', not waiting for review.", 409)
        blockers = self.blockers(item)
        if blockers:
            raise HubError("Can't approve yet: " + "; ".join(blockers), 409)
        when = parse_iso(scheduled_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled_at else item["scheduled_at"]
        self.db.update("items", "id", item_id, {
            "status": "scheduled" if when else "approved", "scheduled_at": when, "approved_hash": content_hash(item),
            "approved_by": actor.name, "approved_at": now_iso(), "review_notes": "", "updated_at": now_iso(),
        })
        self._event(actor, "approved", item=item, detail={"scheduled_at": when})
        return self.get_item(actor, item_id)

    def edit_and_approve(self, actor: Actor, item_id: int, *, scheduled_at: str | None = None, note: str = "",
                         **changes: Any) -> dict[str, Any]:
        """The common review move: fix a word or a figure, then approve, in one step."""
        self._require(actor, "admin")
        self.update_item(actor, item_id, note=note or "reviewer edit", **changes)
        return self.approve(actor, item_id, scheduled_at=scheduled_at)

    def request_changes(self, actor: Actor, item_id: int, notes: str) -> dict[str, Any]:
        self._require(actor, "admin")
        item = self._item(actor, item_id)
        if item["status"] in ("posting", "published"):
            raise HubError(f"Item {item_id} is already {item['status']}.", 409)
        if not notes.strip():
            raise HubError("Say what should change; the drafting agent reads this note.")
        self.db.update("items", "id", item_id, {"status": "needs_revision", "review_notes": notes.strip(),
                                                "approved_hash": None, "approved_by": None, "approved_at": None,
                                                "updated_at": now_iso()})
        self._event(actor, "changes_requested", item=item, detail={"notes": notes.strip()})
        return self.get_item(actor, item_id)

    def reject(self, actor: Actor, item_id: int, notes: str = "") -> dict[str, Any]:
        self._require(actor, "admin")
        item = self._item(actor, item_id)
        if item["status"] == "published":
            raise HubError("Already published.", 409)
        self.db.update("items", "id", item_id, {"status": "rejected", "review_notes": notes.strip(), "approved_hash": None,
                                                "scheduled_at": None, "updated_at": now_iso()})
        self._event(actor, "rejected", item=item, detail={"notes": notes.strip()})
        return self.get_item(actor, item_id)

    # ---- distribution hand-off --------------------------------------------------------------

    def _ready_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        brand = self._project(item["project"])["brand"]
        tracked = self._tracked(item)

        def with_tracking(value: Any) -> Any:  # links written into the copy get the same tags
            if isinstance(value, str):
                value = value.replace("{{link}}", tracked)
                return value.replace(item["link"], tracked) if item["link"] else value
            if isinstance(value, list):
                return [with_tracking(v) for v in value]
            if isinstance(value, dict):
                return {k: with_tracking(v) for k, v in value.items()}
            return value

        return {
            "id": item["id"], "project": item["project"], "kind": item["kind"], "channel": item["channel"],
            "account": item["account"], "title": item["title"], "body": with_tracking(item["body"]), "media": self._absolute_media(self._post_media(item)),
            "link": tracked, "scheduled_at": item["scheduled_at"], "approved_by": item["approved_by"],
            "approved_at": item["approved_at"], "disclosure": (brand.get("disclosure") or {}).get(item["channel"], ""),
        }

    @staticmethod
    def _post_media(item: dict[str, Any]) -> list[Any]:
        """What actually gets posted: the finished video or the slides, not the working files."""
        final = {"short_video": ("video",), "carousel": ("slide",)}.get(item["kind"])
        if not final:
            return item["media"]
        return [m for m in item["media"] if isinstance(m, dict) and m.get("role") in final]

    def _absolute_media(self, media: list[Any]) -> list[Any]:
        base = self.public_url
        def fix(entry: Any) -> Any:
            if isinstance(entry, dict) and str(entry.get("url", "")).startswith("/") and base:
                return {**entry, "url": base + entry["url"]}
            if isinstance(entry, str) and entry.startswith("/") and base:
                return base + entry
            return entry
        return [fix(m) for m in media]

    def ready_to_post(self, actor: Actor, *, project: str | None = None, channel: str | None = None,
                      due_within_hours: float = 24) -> list[dict[str, Any]]:
        """Only approved copy whose hash still matches the approval. This is all a posting agent can see."""
        self._require(actor, "admin", "distributor", "analyst")
        project = actor.project or project
        clauses, params = ["status IN ('approved','scheduled')"], []
        for column, value in (("project", project), ("channel", channel)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        horizon = datetime.now(timezone.utc) + timedelta(hours=due_within_hours)
        out = []
        for item in self.db.all("SELECT * FROM items WHERE " + " AND ".join(clauses) + " ORDER BY COALESCE(scheduled_at, approved_at)", params):
            if item["approved_hash"] != content_hash(item):
                continue
            if item["scheduled_at"] and parse_iso(item["scheduled_at"]) > horizon:
                continue
            out.append(self._ready_payload(item))
        return out

    def claim(self, actor: Actor, item_id: int) -> dict[str, Any]:
        """Take an approved item for posting so two agents never post the same thing."""
        self._require(actor, "admin", "distributor")
        item = self._item(actor, item_id)
        self._release_stale()
        item = self._item(actor, item_id)
        if item["status"] not in READY:
            raise HubError(f"Item {item_id} is '{item['status']}', not approved for posting.", 409)
        if item["approved_hash"] != content_hash(item):
            self.db.update("items", "id", item_id, {"status": "in_review", "approved_hash": None, "updated_at": now_iso()})
            raise HubError(f"Item {item_id} changed after approval and went back to review. Do not post it.", 409)
        changed = self.db.update("items", "id", item_id, {"status": "posting", "claimed_by": actor.name, "claimed_at": now_iso(),
                                                          "updated_at": now_iso()}, where="status = ?", where_params=(item["status"],))
        if not changed:
            raise HubError(f"Item {item_id} was just claimed by someone else.", 409)
        self._event(actor, "claimed", item=item)
        return self._ready_payload(item)

    def _release_stale(self) -> None:
        cutoff = (datetime.now(timezone.utc) - CLAIM_LEASE).strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in self.db.all("SELECT * FROM items WHERE status = 'posting' AND claimed_at < ?", (cutoff,)):
            self.db.update("items", "id", item["id"], {"status": "scheduled" if item["scheduled_at"] else "approved",
                                                       "claimed_by": None, "claimed_at": None,
                                                       "error": "A posting claim expired without a result; check the channel before retrying."})

    def release(self, actor: Actor, item_id: int, reason: str = "") -> dict[str, Any]:
        self._require(actor, "admin", "distributor")
        item = self._item(actor, item_id)
        if item["status"] != "posting":
            raise HubError(f"Item {item_id} is not claimed.", 409)
        self.db.update("items", "id", item_id, {"status": "scheduled" if item["scheduled_at"] else "approved",
                                                "claimed_by": None, "claimed_at": None, "error": reason[:500],
                                                "updated_at": now_iso()})
        self._event(actor, "released", item=item, detail={"reason": reason})
        return {"id": item_id, "status": "released"}

    def mark_posted(self, actor: Actor, item_id: int, *, post_url: str = "", post_id: str = "",
                    posted_at: str | None = None, account: str = "") -> dict[str, Any]:
        self._require(actor, "admin", "distributor")
        item = self._item(actor, item_id)
        if item["status"] not in READY | {"posting"}:
            raise HubError(f"Item {item_id} is '{item['status']}'; only approved items can be marked posted.", 409)
        if item["approved_hash"] != content_hash(item):
            raise HubError(f"Item {item_id} changed after approval.", 409)
        stamp = parse_iso(posted_at).strftime("%Y-%m-%dT%H:%M:%SZ") if posted_at else now_iso()
        publication_id = self.db.insert("publications", {
            "item_id": item_id, "channel": item["channel"], "account": account or item["account"],
            "post_url": post_url, "post_id": post_id, "posted_at": stamp, "posted_by": actor.name,
        })
        self.db.update("items", "id", item_id, {"status": "published", "claimed_by": None, "claimed_at": None,
                                                "error": "", "updated_at": now_iso()})
        self._event(actor, "posted", item=item, detail={"post_url": post_url})
        return {"id": item_id, "status": "published", "publication_id": publication_id}

    # ---- metrics, conversions, costs --------------------------------------------------------

    def report_metrics(self, actor: Actor, item_id: int, data: dict[str, Any], *, measured_at: str | None = None,
                       source: str = "") -> dict[str, Any]:
        """One snapshot of a post's numbers. Report at about 24h, 72h and 7d so posts are compared at the same age."""
        self._require(actor, "admin", "distributor", "analyst")
        item = self._item(actor, item_id)
        publication = self.db.one("SELECT * FROM publications WHERE item_id = ? ORDER BY id DESC LIMIT 1", (item_id,))
        if not publication:
            raise HubError(f"Item {item_id} has not been marked posted yet.", 409)
        clean = {k: float(v) for k, v in data.items() if k in stats.METRIC_FIELDS and isinstance(v, (int, float))}
        if not clean:
            raise HubError("No recognised metrics. Use: " + ", ".join(stats.METRIC_FIELDS))
        when = parse_iso(measured_at) if measured_at else datetime.now(timezone.utc)
        age = max(0.0, (when - parse_iso(publication["posted_at"])).total_seconds() / 3600)
        self.db.insert("metrics", {"publication_id": publication["id"], "measured_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "age_hours": round(age, 2), "data": clean, "source": source or actor.name})
        return {"item_id": item["id"], "age_hours": round(age, 1), "recorded": clean}

    def report_conversions(self, actor: Actor, project: str, rows: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
        """Site-side results per item, keyed by the utm_content tag (cmsbot-<id>) from GA4 or Mixpanel."""
        self._require(actor, "admin", "analyst")
        self._scope(actor, project)
        written, skipped = 0, []
        for row in rows:
            ref = str(row.get("utm_content") or row.get("item_id") or "")
            match = re.search(r"(\d+)$", ref)
            item = self.db.one("SELECT id FROM items WHERE id = ? AND project = ?", (int(match.group(1)), project)) if match else None
            if not item or not row.get("day"):
                skipped.append(ref)
                continue
            self.db.execute("DELETE FROM conversions WHERE item_id = ? AND source = ? AND day = ?", (item["id"], source, row["day"]))
            self.db.insert("conversions", {"project": project, "item_id": item["id"], "source": source, "day": str(row["day"]),
                                           "sessions": float(row.get("sessions") or 0), "signups": float(row.get("signups") or 0),
                                           "revenue_usd": float(row.get("revenue_usd") or 0)})
            written += 1
        return {"written": written, "skipped": skipped}

    def record_cost(self, actor: Actor, project: str, *, amount_usd: float, category: str, vendor: str = "",
                    item_id: int | None = None, note: str = "") -> dict[str, Any]:
        self._scope(actor, project)
        self._project(project)
        if category not in COST_CATEGORIES:
            raise HubError(f"Unknown cost category. Use one of: {', '.join(COST_CATEGORIES)}")
        if item_id:
            self._item(actor, item_id)
        self.db.insert("costs", {"project": project, "item_id": item_id, "category": category, "vendor": vendor,
                                 "amount_usd": float(amount_usd), "note": note, "actor": actor.name, "at": now_iso()})
        return self.spend(actor, project)

    def spend(self, actor: Actor, project: str) -> dict[str, Any]:
        self._scope(actor, project)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = self.db.all("SELECT category, SUM(amount_usd) AS total FROM costs WHERE project = ? AND at LIKE ? GROUP BY category",
                           (project, f"{month}%"))
        total = round(sum(r["total"] for r in rows), 2)
        budget = self._project(project)["settings"].get("monthly_budget_usd")
        return {"month": month, "total_usd": total, "by_category": {r["category"]: round(r["total"], 2) for r in rows},
                "monthly_budget_usd": budget, "remaining_usd": round(budget - total, 2) if budget else None,
                "over_budget": bool(budget) and total >= budget}

    # ---- signals and learnings --------------------------------------------------------------

    def submit_signal(self, actor: Actor, project: str, *, source: str, topic: str, summary: str = "", channel: str = "",
                      evidence: dict[str, Any] | None = None, score: float = 0, expires_in_days: float = 7) -> dict[str, Any]:
        """A trend or search observation. Planning reads these, so trend work and pillar work share one calendar."""
        self._scope(actor, project)
        self._project(project)
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        signal_id = self.db.insert("signals", {"project": project, "source": source, "channel": channel, "topic": topic,
                                               "summary": summary, "evidence": evidence or {}, "score": float(score),
                                               "observed_at": now_iso(), "expires_at": expires, "actor": actor.name})
        return self.db.one("SELECT * FROM signals WHERE id = ?", (signal_id,)) or {}

    def list_signals(self, actor: Actor, project: str, *, status: str = "new") -> list[dict[str, Any]]:
        self._scope(actor, project)
        self.db.execute("UPDATE signals SET status = 'expired' WHERE status = 'new' AND expires_at < ?", (now_iso(),))
        return self.db.all("SELECT id, source, channel, topic, summary, evidence, score, observed_at, expires_at, status "
                           "FROM signals WHERE project = ? AND status = ? ORDER BY score DESC, id DESC LIMIT 50", (project, status))

    def dismiss_signal(self, actor: Actor, signal_id: int) -> None:
        self._require(actor, "admin", "generator")
        signal = self.db.one("SELECT project FROM signals WHERE id = ?", (signal_id,))
        if not signal:
            raise HubError(f"No signal {signal_id}.", 404)
        self._scope(actor, signal["project"])
        self.db.update("signals", "id", signal_id, {"status": "dismissed"})

    def propose_learning(self, actor: Actor, project: str, statement: str, *, scope: dict[str, Any] | None = None,
                         evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """A change to how content is made. It only reaches drafting agents after a person accepts it."""
        self._scope(actor, project)
        self._project(project)
        learning_id = self.db.insert("learnings", {"project": project, "statement": statement, "scope": scope or {},
                                                   "evidence": evidence or {}, "proposed_by": actor.name, "created_at": now_iso()})
        return self.db.one("SELECT * FROM learnings WHERE id = ?", (learning_id,)) or {}

    def decide_learning(self, actor: Actor, learning_id: int, status: str) -> dict[str, Any]:
        self._require(actor, "admin")
        if status not in ("accepted", "rejected", "retired"):
            raise HubError("status must be accepted, rejected or retired.")
        learning = self.db.one("SELECT project FROM learnings WHERE id = ?", (learning_id,))
        if not learning:
            raise HubError(f"No learning {learning_id}.", 404)
        self._scope(actor, learning["project"])
        self.db.update("learnings", "id", learning_id, {"status": status, "decided_by": actor.name, "decided_at": now_iso()})
        return self.db.one("SELECT * FROM learnings WHERE id = ?", (learning_id,)) or {}

    def list_learnings(self, actor: Actor, project: str, status: str | None = None) -> list[dict[str, Any]]:
        self._scope(actor, project)
        if status:
            return self.db.all("SELECT * FROM learnings WHERE project = ? AND status = ? ORDER BY id DESC", (project, status))
        return self.db.all("SELECT * FROM learnings WHERE project = ? ORDER BY id DESC", (project,))

    # ---- performance ------------------------------------------------------------------------

    def performance(self, actor: Actor, project: str, *, age_hours: float = 168, dimensions: list[str] | None = None,
                    seed: int | None = None) -> dict[str, Any]:
        self._scope(actor, project)
        self._project(project)
        rows = self.db.all(
            "SELECT i.id, i.kind, i.channel, i.account, i.attributes, i.cluster_id, p.id AS publication_id, p.posted_at, "
            "pl.key AS pillar FROM items i JOIN publications p ON p.item_id = i.id "
            "LEFT JOIN clusters c ON c.id = i.cluster_id LEFT JOIN pillars pl ON pl.id = c.pillar_id WHERE i.project = ?", (project,))
        posts = []
        for row in rows:
            snapshots = self.db.all("SELECT age_hours, data FROM metrics WHERE publication_id = ?", (row["publication_id"],))
            conv = self.db.one("SELECT SUM(sessions) AS sessions, SUM(signups) AS signups, SUM(revenue_usd) AS revenue "
                               "FROM conversions WHERE item_id = ?", (row["id"],)) or {}
            cost = self.db.one("SELECT SUM(amount_usd) AS total FROM costs WHERE item_id = ?", (row["id"],)) or {}
            posts.append({
                "item_id": row["id"], "posted_at": row["posted_at"], "snapshots": snapshots,
                "dims": {"kind": row["kind"], "channel": row["channel"], "account": row["account"],
                         "pillar": row["pillar"] or "", **{k: str(v) for k, v in row["attributes"].items()}},
                "sessions": conv.get("sessions") or 0, "signups": conv.get("signups") or 0,
                "revenue_usd": conv.get("revenue") or 0, "cost_usd": cost.get("total") or 0,
            })
        report = stats.analyse(posts, age_hours=age_hours, dimensions=dimensions, seed=seed)
        report["spend"] = self.spend(actor, project)
        return report
