"""HTTP surface of the Hub: the review page, a REST API, and an MCP endpoint over the same rules.

  /            review page (people)
  /api/...     REST (scripts, `cmsbot hub` commands)
  /mcp         MCP, streamable HTTP (Claude, Grok Bot, Codex and any other agent)

Every request carries a Hub token: `Authorization: Bearer <token>`, or `?token=<token>` for MCP clients
that can only store a URL.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import os
import sys
from importlib import resources
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from cmsbot.hub.service import (
    CHANNELS,
    DERIVATIVES,
    KINDS,
    POST_LIMITS,
    Actor,
    Hub,
    HubError,
)
from cmsbot import carousel
from cmsbot.hub import brand as brand_kit
from cmsbot.hub import fetch, palette
from cmsbot.hub import due
from cmsbot.hub import generate as generation
from cmsbot.hub import skills
from cmsbot.hub import jobs
from cmsbot.hub import render as rendering
from cmsbot import video
from cmsbot.hub.media import MAX_UPLOAD_BYTES, MediaError, content_type_for, media_key, storage_from_env
from cmsbot.hub.store import Store

_actor: contextvars.ContextVar[Actor | None] = contextvars.ContextVar("cmsbot_hub_actor", default=None)


def default_db_path() -> str:
    return os.environ.get("CMSBOT_HUB_DB", "hub-data/hub.sqlite3")


def _bearer(header: str | None) -> str:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _video_summary(item: dict[str, Any]) -> dict[str, Any]:
    report = item["audit"].get("video") or {}
    return {"item_id": item["id"], "seconds": report.get("seconds"), "checks": report.get("checks"),
            "video": next((m["url"] for m in item["media"] if isinstance(m, dict) and m.get("role") == "video"), "")}


def _generated_summary(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("item_id", "shot", "role", "url", "provider", "cost_usd", "cost_is_estimate", "words_timed", "disclosure",
            "item_remaining_usd")
    return {k: result[k] for k in keep if k in result}


def start_render(hub: Hub, storage, actor: Actor, item_id: int) -> dict[str, Any]:
    """Carousels render in a few seconds and return the item. Video runs as a job."""
    if hub.get_item(actor, item_id)["kind"] != "short_video":
        return rendering.render_item(hub, storage, actor, item_id)
    return {"job": jobs.start(hub, actor, item_id, "render_video",
                              lambda: rendering.render_video_item(hub, storage, actor, item_id), _video_summary)}


def start_generation(hub: Hub, storage, actor: Actor, item_id: int, what: str, **options: Any) -> dict[str, Any]:
    if what == "voiceover":
        generation.precheck_voiceover(hub, actor, item_id, **options)
        work = lambda: generation.generate_voiceover(hub, storage, actor, item_id, **options)  # noqa: E731
        return {"job": jobs.start(hub, actor, item_id, "generate_voiceover", work, _generated_summary)}
    generation.precheck_footage(hub, actor, item_id, **options)
    work = lambda: generation.generate_footage(hub, storage, actor, item_id, **options)  # noqa: E731
    return {"job": jobs.start(hub, actor, item_id, f"generate_shot_{int(options['shot']):02d}", work, _generated_summary)}


EVERYONE = {"whoami", "whats_due", "log_run", "list_skills", "get_skill", "list_projects"}
ROLE_TOOLS: dict[str, set[str]] = {
    "generator": EVERYONE | {
        "get_brief", "create_cluster", "list_items", "get_item", "save_draft", "check_draft", "set_claims", "submit_for_review",
        "report_failure", "render_carousel", "render_video", "job_status", "media_options", "generate_footage", "generate_voiceover",
        "import_published", "get_series", "plan_series", "propose_brand", "record_cost", "propose_learning", "submit_signal",
        "performance", "propose_skill_change"},
    "distributor": EVERYONE | {"ready_to_post", "claim_post", "release_post", "mark_posted", "report_metrics", "submit_signal", "record_cost"},
    "analyst": EVERYONE | {
        "get_brief", "list_items", "get_item", "get_series", "ready_to_post", "report_metrics", "report_conversions", "submit_signal",
        "record_cost", "performance", "propose_learning", "propose_skill_change", "propose_brand"},
}
ROLE_SUMMARY = {
    "generator": ("Drafting agent.", "Plan, draft, revise, render, generate media and submit for review.",
                  "Approve, post, or change a piece once it is with the reviewer."),
    "distributor": ("Posting agent.", "See approved copy that is due, claim it, post it exactly, record the post, report its numbers and trends.",
                    "See or edit drafts, or post anything the Hub did not hand you."),
    "analyst": ("Analysis agent.", "Read everything, bring in results, read the performance report, propose learnings and skill changes.",
                "Draft, post, or change a method directly."),
    "agent": ("All-round agent.", "Every agent job: drafting, posting approved copy, reporting numbers, analysis and proposals.",
              "Approve, verify claims, change settings or access. Post only what claim_post returns, never a draft from memory."),
    "admin": ("Reviewer.", "Everything, including approving.", "Nothing is withheld; this token belongs to a person."),
}


def tools_for(role: str, every_tool: set[str]) -> set[str]:
    if role == "admin":
        return every_tool
    if role == "agent":
        return set().union(*ROLE_TOOLS.values()) & every_tool
    return ROLE_TOOLS.get(role, EVERYONE) & every_tool


def build_mcp(hub: Hub, storage, do_import=None):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings

    server = MCPServer(
        "cmsbot-hub",
        log_level="WARNING",
        instructions=(
            "CMSBOT Hub holds the content state for every project: plans, drafts, review status, approved copy, "
            "posts, metrics, costs, learnings and the skills (written methods) agents follow. Your token has a role "
            "and a project scope that decide which tools you see: call whoami first to learn them. New here? Then "
            "get_skill('cmsbot-start'). Each session: whats_due(project), then get_skill for the task, then "
            "get_brief for the project. Drafting agents save drafts "
            "and submit them; only a person approves. Posting agents call ready_to_post, claim_post, post exactly "
            "the returned copy, then mark_posted and later report_metrics. Never post anything that did not come "
            "from ready_to_post or claim_post."
        ),
    )

    def actor() -> Actor:
        current = _actor.get()
        if current is None:
            raise HubError("Missing or invalid Hub token.", 401)
        return current

    registered: list[str] = []

    def tool(fn):
        registered.append(fn.__name__)

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            me = _actor.get()
            if me is not None and fn.__name__ not in tools_for(me.role, set(registered)):
                raise ToolError(f"The tool {fn.__name__} is not part of the {me.role} role. Call whoami to see what this token can do.")
            try:
                return fn(*args, **kwargs)
            except HubError as exc:  # show the agent the reason, not a generic crash
                raise ToolError(str(exc)) from exc

        return server.tool()(wrapped)

    @tool
    def whoami() -> dict[str, Any]:
        """Who this connection is: the token's name, its role, which projects it covers, what that role may and may
        not do, and the tools available to it. Call this first, so you know your scope before you plan any work."""
        me = actor()
        title, can, cannot = ROLE_SUMMARY[me.role]
        return {"name": me.name, "role": me.role, "role_means": title, "you_can": can, "you_cannot": cannot,
                "projects": [p["slug"] for p in hub.list_projects(me)],
                "limited_to_project": me.project or None,
                "your_tools": sorted(tools_for(me.role, set(registered))),
                "next": "Call get_skill('cmsbot-start') if you have not worked with this Hub before, then whats_due(project)."}

    @tool
    def whats_due(project: str) -> dict[str, Any]:
        """What needs doing in this project right now, split into what is for your role and what is waiting on
        others: revisions, pieces to write, posts due, numbers to report at 24h/72h/7d, planning or analysis that
        is overdue, decisions waiting on the reviewer. Start every session here and do only what is due."""
        return due.whats_due(hub, actor(), project)

    @tool
    def log_run(project: str, task: str, summary: str = "") -> dict[str, Any]:
        """Record that you finished a run (task: plan, write, distribute or analyze) with one line on what it did.
        Planning and analysis are scheduled from the last recorded run, so always log them."""
        return due.log_run(hub, actor(), project, task, summary)

    @tool
    def list_skills(project: str = "") -> list[dict[str, Any]]:
        """The skills (written methods) in effect, with a one-line description of when to use each. A project can
        have its own version of a skill. Read the one for your task with get_skill before starting it."""
        return skills.listing(hub, actor(), project or None)

    @tool
    def get_skill(name: str, project: str = "") -> dict[str, Any]:
        """The current text of a skill for this project. Follow it, and set the returned stamp as the `skill`
        attribute on drafts you make with it."""
        return skills.get(hub, actor(), name, project or None)

    @tool
    def propose_skill_change(name: str, body: str, rationale: str, project: str = "", evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """Propose a new version of a skill: the full new text, why, and the evidence (what you observed, on how
        many posts, compared at the same age). Prefer changing one project's copy over the hub-wide one. Only when
        the performance report shows 'above baseline' or 'below baseline' on at least five posts; a hunch or one
        good post is not a reason. A person accepts or rejects it; one open proposal per skill."""
        return skills.propose(hub, actor(), name=name, body=body, rationale=rationale, project=project or None, evidence=evidence)

    @tool
    def list_projects() -> list[dict[str, Any]]:
        """Projects this token can work on."""
        return [{"slug": p["slug"], "name": p["name"]} for p in hub.list_projects(actor())]

    @tool
    def get_brief(project: str) -> dict[str, Any]:
        """Read this before planning or writing: brand kit, pillars, recent clusters, accepted learnings,
        open trend signals, reviewer feedback waiting on you, and this month's spend against budget."""
        return hub.brief(actor(), project)

    @tool
    def create_cluster(project: str, name: str, pillar: str = "", theme: str = "", keywords: list[str] | None = None,
                       starts_on: str = "", ends_on: str = "", origin: str = "pillar", signal_id: int = 0) -> dict[str, Any]:
        """Plan one theme for one period under a pillar. Use origin='trend' and signal_id when it answers a trend signal."""
        return hub.create_cluster(actor(), project, name, pillar=pillar or None, theme=theme, keywords=keywords,
                                  starts_on=starts_on or None, ends_on=ends_on or None, origin=origin,
                                  signal_id=signal_id or None)

    @tool
    def propose_brand(project: str, brand: dict[str, Any]) -> dict[str, Any]:
        """Draft a project's brand kit for its reviewer to accept on the Brand tab; nothing changes until they save it.
        Fields: website, description, tone (how it sounds, in plain sentences with 'sounds like / never sounds like'
        examples quoted from the brand's own writing), lexicon {prefer: [], avoid: []}, disallowed_claims [], ctas [],
        visual {wordmark, footer, colors {base, glow, ink, muted, accent, accent2 as #rrggbb}},
        personas {channel: {account, type: brand|character, who, disclosure}}. Base it on the brand's real site and
        published writing, quoting its phrases; do not invent a personality."""
        return hub.propose_brand(actor(), project, brand)

    @tool
    def import_published(project: str, url: str, title: str = "", markdown: str = "") -> dict[str, Any]:
        """Bring in a piece that is already live (a published blog post or guide) as an anchor, so a series can be
        made from it. Give the text as markdown if you have it; otherwise the Hub reads the page. Afterwards call
        set_claims on it with every factual claim the series might repeat."""
        item = do_import(actor(), project, url, title, markdown)
        return {"id": item["id"], "title": item["title"], "status": item["status"], "series": hub.series(actor(), item["id"])["children"]}

    @tool
    def get_series(anchor_id: int) -> dict[str, Any]:
        """An anchor and the pieces planned or made from it, with their status, plus the project's default recipe."""
        return hub.series(actor(), anchor_id)

    @tool
    def plan_series(anchor_id: int, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """Set how many of each piece an anchor should have: [{kind, channel, count}], e.g. 8 threads on x and 3
        short videos on tiktok. Adds planned items to write and removes only untouched planned ones. Use when the
        owner asks for more or fewer pieces from an anchor; otherwise the project's recipe applies."""
        return hub.plan_series(actor(), anchor_id, plan)

    @tool
    def list_items(project: str = "", status: str = "", channel: str = "", kind: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """List items. status takes a comma list, e.g. 'needs_revision,failed'. Posting agents use ready_to_post instead."""
        return hub.list_items(actor(), project=project or None, status=status or None, channel=channel or None,
                              kind=kind or None, limit=limit)

    @tool
    def get_item(item_id: int) -> dict[str, Any]:
        """One item with its body, claims, reviewer notes, blockers and history."""
        return hub.get_item(actor(), item_id)

    @tool
    def save_draft(project: str, kind: str, channel: str, title: str, body: dict[str, Any], item_id: int = 0,
                   cluster_id: int = 0, parent_id: int = 0, account: str = "", media: list[Any] | None = None,
                   attributes: dict[str, Any] | None = None, link: str = "", gaps: list[str] | None = None,
                   audit: dict[str, Any] | None = None, external_ref: str = "") -> dict[str, Any]:
        """Create a draft, or update one you may still edit (pass item_id). kind: blog, newsletter, thread, post,
        carousel, short_video, image, infographic. channel: blog, email, x, threads, instagram, linkedin, tiktok,
        facebook, reddit, youtube. parent_id links a derivative to its anchor (it then restates the anchor's
        claims; list them in body.claim_ids). attributes are the things you want compared later
        (hook, format, style, persona, length...). gaps are facts you could not confirm; never invent them."""
        if item_id:
            saved = hub.update_item(actor(), item_id, title=title, body=body, media=media, attributes=attributes,
                                    link=link or None, gaps=gaps, audit=audit, account=account or None)
        else:
            saved = hub.create_item(actor(), project, kind=kind, channel=channel, title=title, body=body,
                                    cluster_id=cluster_id or None, parent_id=parent_id or None, account=account,
                                    media=media, attributes=attributes, link=link, gaps=gaps, audit=audit,
                                    external_ref=external_ref or None)
        # A short receipt, not the whole item: the draft's text is already in your context.
        return {"id": saved["id"], "status": saved["status"], "kind": saved["kind"], "channel": saved["channel"],
                "parent_id": saved["parent_id"], "blockers": saved["blockers"], "writing_checks": saved["writing_checks"]}

    @tool
    def render_carousel(item_id: int) -> dict[str, Any]:
        """Turn a carousel draft's slides into images in the project's brand style and attach them. Call after
        save_draft and before submit_for_review; a carousel can't be approved with missing or stale images."""
        item = rendering.render_item(hub, storage, actor(), item_id)
        return {"id": item["id"], "media": item["media"], "slides_stale": item["slides_stale"]}

    @tool
    def render_video(item_id: int) -> dict[str, Any]:
        """Start building the captioned 1080x1920 video for a short_video item from its script plus whatever
        footage and voiceover are attached (roles shot-01..NN, voiceover, timing, music). Shots without footage
        become branded cards, so this also previews a script alone. Takes a minute or more: this returns a job;
        call job_status(job_id) until it is done, then read the checks and fix what is not ok before submitting."""
        return start_render(hub, storage, actor(), item_id)

    @tool
    def job_status(job_id: int) -> dict[str, Any]:
        """Status of a render or generation job: running, done (with its result) or failed (with the reason)."""
        return jobs.get(hub, actor(), job_id)

    @tool
    def media_options(item_id: int) -> dict[str, Any]:
        """Which image, video and voice providers this Hub can use, and how much of the item's media cap and the
        project's monthly budget is left. Read this before generating anything."""
        return generation.options(hub, actor(), item_id)

    @tool
    def generate_footage(item_id: int, shot: int, prompt: str, kind: str = "video", seconds: int = 5,
                         provider: str = "") -> dict[str, Any]:
        """Generate the still (kind='image') or clip (kind='video') for one shot of an approved video script and
        attach it. Shots are numbered hook=1, then the beats, then the ending. Generate the still first: a video
        then starts from that still, which keeps the look consistent. This spends money: it is refused before the
        script is approved, above the item's media cap, or over the project's budget. Keep words, numbers and
        logos out of the prompt; text is added by the renderer. Returns a job; call job_status(job_id) until done."""
        return start_generation(hub, storage, actor(), item_id, "footage", shot=shot, prompt=prompt, kind=kind,
                                seconds=seconds, provider=provider)

    @tool
    def generate_voiceover(item_id: int, voice_id: str = "") -> dict[str, Any]:
        """Speak an approved video script with the project's voice and attach it with word timings, so captions
        sync exactly. Use only when the project has no recorded voiceover. Spends money, within the same caps.
        Returns a job; call job_status(job_id) until done."""
        return start_generation(hub, storage, actor(), item_id, "voiceover", voice_id=voice_id)

    @tool
    def set_claims(item_id: int, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace an anchor item's claim ledger: [{text, citation, citation_url}]. A person verifies each one.
        Returns each claim's label (L1, L2, ...): use exactly these labels in a derivative's body.claim_ids."""
        item = hub.set_claims(actor(), item_id, claims)
        return {"item_id": item["id"], "claims": [{"label": c["label"], "text": c["text"][:160], "status": c["status"]} for c in item["claims"]]}

    @tool
    def submit_for_review(item_id: int) -> dict[str, Any]:
        """Hand a finished draft to the reviewer. You can't edit it again unless they request changes."""
        item = hub.submit(actor(), item_id)
        fixes = [c for c in item["writing_checks"] if c["level"] == "fix"]
        return {"id": item["id"], "status": item["status"], "blockers": item["blockers"], "writing_checks": item["writing_checks"],
                "advice": "Submitted. " + (f"{len(fixes)} check(s) marked 'fix' will be shown to the reviewer; next time call "
                                           "check_draft and correct them before submitting." if fixes else "No objective problems found.")}

    @tool
    def check_draft(item_id: int) -> dict[str, Any]:
        """Run the writing checks on a saved draft before submitting: platform length limits, the brand's banned
        words, invented first-person experience, figures missing from the verified claims, and measurable
        machine-writing patterns. Correct everything marked 'fix'; treat 'note' items as a prompt to re-read."""
        item = hub.get_item(actor(), item_id)
        return {"id": item["id"], "writing_checks": item["writing_checks"], "blockers": item["blockers"]}

    @tool
    def report_failure(item_id: int, error: str) -> dict[str, Any]:
        """Record that drafting this item failed, so a person sees it instead of it going quiet."""
        item = hub.fail(actor(), item_id, error)
        return {"id": item["id"], "status": item["status"], "attempts": item["attempts"]}

    @tool
    def ready_to_post(project: str = "", channel: str = "", due_within_hours: float = 24) -> list[dict[str, Any]]:
        """Approved copy that is due. This is the only source of postable content."""
        return hub.ready_to_post(actor(), project=project or None, channel=channel or None,
                                 due_within_hours=due_within_hours)

    @tool
    def claim_post(item_id: int) -> dict[str, Any]:
        """Take an approved item for posting. Returns the exact copy, media and tracked link to post, unchanged."""
        return hub.claim(actor(), item_id)

    @tool
    def release_post(item_id: int, reason: str = "") -> dict[str, Any]:
        """Give a claimed item back without posting (API error, account issue)."""
        return hub.release(actor(), item_id, reason)

    @tool
    def mark_posted(item_id: int, post_url: str = "", post_id: str = "", posted_at: str = "", account: str = "") -> dict[str, Any]:
        """Record that the item went out, with its URL and platform id."""
        return hub.mark_posted(actor(), item_id, post_url=post_url, post_id=post_id, posted_at=posted_at or None,
                               account=account)

    @tool
    def report_metrics(item_id: int, metrics: dict[str, float], measured_at: str = "") -> dict[str, Any]:
        """One snapshot of a post's numbers (impressions, reach, views, likes, comments, saves, shares, clicks,
        follows, watch_seconds, avg_watch_pct). Report at about 24h, 72h and 7d after posting."""
        return hub.report_metrics(actor(), item_id, metrics, measured_at=measured_at or None)

    @tool
    def report_conversions(project: str, source: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Site results per item from GA4 or Mixpanel: [{utm_content: 'cmsbot-<id>', day, sessions, signups, revenue_usd}]."""
        return hub.report_conversions(actor(), project, rows, source=source)

    @tool
    def submit_signal(project: str, source: str, topic: str, summary: str = "", channel: str = "",
                      evidence: dict[str, Any] | None = None, score: float = 0, expires_in_days: float = 7) -> dict[str, Any]:
        """Report a trend or search opportunity (what is rising, where, and the evidence). Planning reads these."""
        return hub.submit_signal(actor(), project, source=source, topic=topic, summary=summary, channel=channel,
                                 evidence=evidence, score=score, expires_in_days=expires_in_days)

    @tool
    def record_cost(project: str, amount_usd: float, category: str, vendor: str = "", item_id: int = 0, note: str = "") -> dict[str, Any]:
        """Log money spent making or distributing content. category: llm, image, video, audio, tool, subscription,
        human, other. Returns month-to-date spend."""
        return hub.record_cost(actor(), project, amount_usd=amount_usd, category=category, vendor=vendor,
                               item_id=item_id or None, note=note)

    @tool
    def performance(project: str, age_hours: float = 168) -> dict[str, Any]:
        """How content is doing: posts compared at the same age against their channel's recent baseline, by attribute,
        with cost per signup and a suggested mix. Read the caveats before drawing conclusions."""
        return hub.performance(actor(), project, age_hours=age_hours)

    @tool
    def propose_learning(project: str, statement: str, scope: dict[str, Any] | None = None,
                         evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """Propose a change to how content is made, with the evidence. It takes effect only after a person accepts it."""
        return hub.propose_learning(actor(), project, statement, scope=scope, evidence=evidence)

    list_all = server.list_tools

    async def list_for_role():
        """Each token is shown only the tools its role can use, so an agent's tool list is its scope."""
        me = _actor.get()
        tools = await list_all()
        if me is None:
            return tools
        allowed = tools_for(me.role, set(registered))
        return [t for t in tools if t.name in allowed]

    server.list_tools = list_for_role  # type: ignore[method-assign]

    inner = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def authed(scope, receive, send):
        if scope["type"] != "http":
            return await inner(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = _bearer(headers.get("authorization"))
        if not token:
            from urllib.parse import parse_qs
            token = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [""])[0]
        found = hub.authenticate(token)
        if found is None:
            response = JSONResponse({"error": "Missing or invalid Hub token."}, status_code=401)
            return await response(scope, receive, send)
        reset = _actor.set(found)
        try:
            await inner(scope, receive, send)
        finally:
            _actor.reset(reset)

    return server, authed


def create_app(db_path: str | None = None, *, hub: Hub | None = None, storage=None, with_mcp: bool = True) -> FastAPI:
    db_path = db_path or default_db_path()
    hub = hub or Hub(Store(db_path), os.environ.get("CMSBOT_HUB_PUBLIC_URL", ""))
    storage = storage or storage_from_env(db_path if hub.db.path != ":memory:" else ":memory:")
    mcp_holder: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        if not mcp_holder:
            yield
            return
        async with mcp_holder["server"].session_manager.run():
            yield

    app = FastAPI(title="CMSBOT Hub", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.hub = hub
    jobs.recover(hub)
    skills.seed(hub)
    setup_attempts = {"left": 10}
    code = hub.setup_code()
    if code:  # a fresh Hub: the first reviewer is created in the browser with this one-time code
        print(f"\nCMSBOT Hub first-time setup code: {code}\nOpen the Hub in a browser and enter it to create the first reviewer.\n",
              file=sys.stderr, flush=True)

    @app.exception_handler(HubError)
    async def hub_error(_: Request, exc: HubError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(MediaError)
    async def media_error(_: Request, exc: MediaError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    def refresh_media(actor: Actor, item_id: int) -> bool:
        """After a reviewer's edit. Returns True when a video re-render was started and is still running."""
        item = hub.get_item(actor, item_id)
        if item["kind"] == "short_video":
            if item["video_state"] == "stale" and video.can_render():
                start_render(hub, storage, actor, item_id)
                return True
            return bool(item["active_jobs"])
        rendering.render_if_stale(hub, storage, actor, item_id)
        return False

    def do_import(actor: Actor, project: str, url: str, title: str, markdown: str, published_at: str | None = None):
        url = url.strip()
        if not markdown.strip():  # no text supplied: read the page
            try:
                page = fetch.read_page(url)
            except fetch.FetchError as exc:
                raise HubError(f"{exc} You can paste the text instead.") from exc
            title, markdown = title.strip() or page["title"], page["markdown"]
            if len(markdown) < 200:
                raise HubError("Could not find the article text on that page. Paste the text instead.")
        return hub.import_published(actor, project, url=url, title=title, markdown=markdown, published_at=published_at)

    def who(authorization: str | None = Header(default=None)) -> Actor:
        found = hub.authenticate(_bearer(authorization))
        if found is None:
            raise HubError("Missing or invalid Hub token.", 401)
        return found

    @app.get("/", response_class=HTMLResponse)
    def review_page() -> str:
        return resources.files("cmsbot.hub").joinpath("review.html").read_text(encoding="utf-8")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/setup")
    def setup_state() -> dict[str, bool]:
        return {"needed": hub.needs_setup()}

    @app.post("/api/setup")
    def setup(body: dict[str, Any] = Body(...)) -> dict[str, str]:
        if setup_attempts["left"] <= 0:
            raise HubError("Too many wrong codes. Restart the Hub to get a new setup code.", 429)
        try:
            return {"token": hub.complete_setup(str(body.get("code", "")), str(body.get("name", "")).strip())}
        except HubError as exc:
            if exc.status == 403:
                setup_attempts["left"] -= 1
            raise

    @app.get("/api/tokens")
    def tokens(actor: Actor = Depends(who)):
        return hub.list_tokens(actor)

    @app.post("/api/tokens")
    def post_token(body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        days = body.get("expires_in_days")
        return hub.issue_token(actor, str(body.get("name", "")), str(body.get("role", "")), body.get("project") or None,
                               float(days) if days else None)

    @app.post("/api/tokens/{token_id}/{action}")
    def token_action(token_id: int, action: str, actor: Actor = Depends(who)):
        if action == "rotate":
            return hub.rotate_token(actor, token_id)
        if action == "revoke":
            hub.revoke_token(token_id, actor)
            return {"id": token_id, "revoked": True}
        raise HubError(f"Unknown action '{action}'.", 404)

    @app.get("/api/settings")
    def get_settings(project: str | None = None, actor: Actor = Depends(who)):
        return hub.list_settings(actor, project or actor.project)

    @app.put("/api/settings/{name}")
    def put_setting(name: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.set_setting(actor, name, str(body.get("value", "")), body.get("project") or actor.project)

    @app.delete("/api/settings/{name}")
    def delete_setting(name: str, project: str | None = None, actor: Actor = Depends(who)):
        hub.clear_setting(actor, name, project or actor.project)
        return {"name": name, "removed": True}

    @app.post("/api/settings/{name}/test")
    def test_setting(name: str, project: str | None = None, actor: Actor = Depends(who)):
        return generation.test_key(hub, actor, name, project or actor.project)

    @app.get("/api/me")
    def me(actor: Actor = Depends(who)) -> dict[str, Any]:
        return {"name": actor.name, "role": actor.role, "project": actor.project, "kinds": KINDS,
                "derivatives": [{"kind": k, "channels": list(c)} for k, c in DERIVATIVES.items()],
                "channels": CHANNELS, "post_limits": POST_LIMITS}

    @app.get("/api/projects")
    def projects(actor: Actor = Depends(who)):
        return hub.list_projects(actor)

    @app.put("/api/projects/{slug}")
    def put_project(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.upsert_project(actor, slug, body.get("name", ""), brand=body.get("brand"), settings=body.get("settings"))

    @app.put("/api/projects/{slug}/pillars/{key}")
    def put_pillar(slug: str, key: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.upsert_pillar(actor, slug, key, body.get("name", key), description=body.get("description", ""),
                                 weight=float(body.get("weight", 1)), status=body.get("status", "active"))

    @app.get("/api/projects/{slug}/brief")
    def brief(slug: str, actor: Actor = Depends(who)):
        return hub.brief(actor, slug)

    @app.post("/api/projects/{slug}/clusters")
    def post_cluster(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.create_cluster(actor, slug, body["name"], pillar=body.get("pillar"), theme=body.get("theme", ""),
                                  keywords=body.get("keywords"), starts_on=body.get("starts_on"),
                                  ends_on=body.get("ends_on"), origin=body.get("origin", "pillar"),
                                  signal_id=body.get("signal_id"))

    @app.get("/api/items")
    def items(project: str | None = None, status: str | None = None, channel: str | None = None,
              kind: str | None = None, limit: int = Query(default=200), anchors: bool = False, actor: Actor = Depends(who)):
        return hub.list_items(actor, project=project, status=status, channel=channel, kind=kind, limit=limit, anchors=anchors)

    @app.get("/api/items/{item_id}/series")
    def get_series(item_id: int, actor: Actor = Depends(who)):
        return hub.series(actor, item_id)

    @app.put("/api/items/{item_id}/series")
    def put_series(item_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.plan_series(actor, item_id, body.get("plan", []))

    @app.get("/api/projects/{slug}/due")
    def get_due(slug: str, actor: Actor = Depends(who)):
        return due.whats_due(hub, actor, slug)

    @app.put("/api/projects/{slug}/rhythm")
    def put_rhythm(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return {"rhythm": due.set_rhythm(hub, actor, slug, body)}

    @app.post("/api/projects/{slug}/runs")
    def post_run(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return due.log_run(hub, actor, slug, str(body.get("task", "")), str(body.get("summary", "")))

    @app.get("/api/skills")
    def list_skills(project: str | None = None, actor: Actor = Depends(who)):
        return skills.listing(hub, actor, project)

    @app.get("/api/skills/{name}")
    def get_skill(name: str, project: str | None = None, manage: bool = False, actor: Actor = Depends(who)):
        return skills.detail(hub, actor, name, project) if manage else skills.get(hub, actor, name, project)

    @app.put("/api/skills/{name}")
    def put_skill(name: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return skills.save(hub, actor, name=name, description=str(body.get("description", "")), body=str(body.get("body", "")),
                           project=body.get("project") or None, note=str(body.get("note", "")))

    @app.post("/api/skills")
    def upload_skill(body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        """Add a skill from the text of a SKILL.md file (front matter with name and description, then the method)."""
        parsed = skills.parse(str(body.get("text", "")))
        name = str(body.get("name") or parsed["name"]).strip().lower()
        return skills.save(hub, actor, name=name, description=str(body.get("description") or parsed["description"]),
                           body=parsed["body"], project=body.get("project") or None, note="Uploaded")

    @app.delete("/api/skills/{name}")
    def delete_skill(name: str, project: str | None = None, actor: Actor = Depends(who)):
        skills.retire(hub, actor, name, project)
        return {"name": name, "retired": True}

    @app.post("/api/skills/versions/{skill_id}/{action}")
    def skill_version_action(skill_id: int, action: str, actor: Actor = Depends(who)):
        if action in ("accept", "reject"):
            return skills.decide(hub, actor, skill_id, action == "accept")
        if action == "restore":
            return skills.restore(hub, actor, skill_id)
        raise HubError(f"Unknown action '{action}'.", 404)

    @app.get("/api/skills/versions/{skill_id}")
    def skill_version(skill_id: int, actor: Actor = Depends(who)):
        row = hub.db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))
        if not row:
            raise HubError("No such version.", 404)
        hub._manageable(actor, row["project"] or None)
        return row

    @app.get("/api/projects/{slug}/brand")
    def get_brand(slug: str, actor: Actor = Depends(who)):
        return hub.get_brand(actor, slug)

    @app.put("/api/projects/{slug}/brand")
    def put_brand(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.set_brand(actor, slug, body.get("brand", {}), name=body.get("name"))

    @app.post("/api/projects/{slug}/brand/scan")
    def scan_brand(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        hub._manageable(actor, slug)
        return brand_kit.scan_site(str(body.get("url", "")).strip())

    @app.post("/api/projects/{slug}/brand/images")
    async def brand_image(slug: str, request: Request, name: str = Query(...), actor: Actor = Depends(who)):
        """Upload a palette picture, mood image or logo. Returns where it is stored and the colours found in it."""
        hub._manageable(actor, slug)
        hub._project(slug)
        content_type = content_type_for(name, request.headers.get("content-type", ""))
        if not content_type.startswith("image/"):
            raise HubError("Upload a picture (PNG, JPG or WebP).")
        data = await request.body()
        if not data or len(data) > 15 * 1024 * 1024:
            raise HubError("The picture is empty or larger than 15 MB.", 413)
        try:
            colours = palette.from_image(data)
        except Exception as exc:  # noqa: BLE001 - any unreadable file is the user's to replace
            raise HubError("That file could not be read as a picture.") from exc
        url = storage.put(media_key(slug, "brand", name), data, content_type)
        return {"url": url, "colors_found": colours, "suggested_colors": palette.suggest_theme(colours)}

    @app.post("/api/projects/{slug}/brand/preview")
    def preview_brand(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        """How slides would look with the brand as currently filled in (before saving)."""
        hub._scope(actor, slug)
        draft = brand_kit.clean(body.get("brand", {}), hub._project(slug)["brand"])
        name = str(body.get("name") or hub._project(slug)["name"])
        cta = (draft.get("ctas") or ["Find out more"])[0]
        sample = {"topic_tag": "PREVIEW", "slides": [
            {"type": "cover", "eyebrow": "How your slides will look", "headline": (draft.get("description") or name)[:70],
             "dek": "Headline, supporting line and labels in your colours and fonts."},
            {"type": "evidence", "label": "ACCENTS AND TEXT", "rows": [
                {"claim": "Accent colour marks what matters", "strength": 3, "note": "Muted text carries the detail"},
                {"claim": "Second accent for contrast", "strength": 1, "note": "Used sparingly"}]},
            {"type": "cta", "headline": cta, "button": cta}]}
        return carousel.build_slides(sample, {"name": name, **draft})

    @app.post("/api/items/{item_id}/reread")
    def reread(item_id: int, actor: Actor = Depends(who)):
        item = hub.get_item(actor, item_id)
        try:
            page = fetch.read_page(item["link"])
        except fetch.FetchError as exc:
            raise HubError(str(exc)) from exc
        return hub.reread_import(actor, item_id, page["title"], page["markdown"])

    @app.put("/api/projects/{slug}/recipe")
    def put_recipe(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return {"recipe": hub.set_recipe(actor, slug, body.get("recipe", []))}

    @app.post("/api/projects/{slug}/import")
    def import_piece(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return do_import(actor, slug, body.get("url", ""), body.get("title", ""), body.get("markdown", ""), body.get("published_at"))

    item_fields = ("title", "body", "media", "attributes", "link", "gaps", "audit", "account")

    @app.post("/api/projects/{slug}/items")
    def post_item(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        extra = {k: body[k] for k in item_fields if k in body and k != "title"}
        return hub.create_item(actor, slug, kind=body["kind"], channel=body["channel"], title=body["title"],
                               cluster_id=body.get("cluster_id"), parent_id=body.get("parent_id"),
                               external_ref=body.get("external_ref"), **extra)

    @app.get("/api/items/{item_id}")
    def get_item(item_id: int, actor: Actor = Depends(who)):
        return hub.get_item(actor, item_id)

    @app.patch("/api/items/{item_id}")
    def patch_item(item_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        changes = {k: body[k] for k in (*item_fields, "scheduled_at", "note") if k in body}
        hub.update_item(actor, item_id, **changes)
        if actor.role == "admin":
            refresh_media(actor, item_id)
        return hub.get_item(actor, item_id)

    @app.post("/api/items/{item_id}/media")
    async def upload_media(item_id: int, request: Request, name: str = Query(...), role: str = "",
                           actor: Actor = Depends(who)):
        """Upload one image, video or audio file as the raw request body and attach it to the item."""
        item = hub.get_item(actor, item_id)
        content_type = content_type_for(name, request.headers.get("content-type", ""))
        data = await request.body()
        if not data:
            raise HubError("The upload is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HubError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", 413)
        url = storage.put(media_key(item["project"], item_id, name), data, content_type)
        return hub.add_media(actor, item_id, url, name=name, role=role)

    @app.post("/api/items/{item_id}/media/raw")
    async def store_media(item_id: int, request: Request, name: str = Query(...), actor: Actor = Depends(who)):
        """Store a file for an item without attaching it (used for slide images, attached by PUT /render)."""
        if not actor.can("admin", "generator"):
            raise HubError("This token may not upload.", 403)
        item = hub.get_item(actor, item_id)
        data = await request.body()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HubError("The upload is empty or too large.", 413)
        return {"url": storage.put(media_key(item["project"], item_id, name), data, content_type_for(name))}

    @app.get("/media/{key:path}")
    def get_media(key: str):
        path = storage.path_for(key)
        if path is None:
            raise HubError("Not found.", 404)
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.post("/api/items/{item_id}/preview")
    def preview_slides(item_id: int, body: dict[str, Any] = Body(default={}), actor: Actor = Depends(who)):
        return rendering.preview(hub, actor, item_id, body.get("body"))

    @app.post("/api/items/{item_id}/render")
    def render_slides(item_id: int, actor: Actor = Depends(who)):
        return start_render(hub, storage, actor, item_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, actor: Actor = Depends(who)):
        return jobs.get(hub, actor, job_id)

    @app.get("/api/items/{item_id}/media-options")
    def media_options(item_id: int, actor: Actor = Depends(who)):
        return generation.options(hub, actor, item_id)

    @app.post("/api/items/{item_id}/generate")
    def generate(item_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        if body.get("what") == "voiceover":
            return start_generation(hub, storage, actor, item_id, "voiceover", voice_id=body.get("voice_id", ""),
                                    provider=body.get("provider", ""))
        return start_generation(hub, storage, actor, item_id, "footage", shot=int(body.get("shot", 0)),
                                prompt=body.get("prompt", ""), kind=body.get("kind", "video"),
                                seconds=int(body.get("seconds", 5)), provider=body.get("provider", ""))

    @app.put("/api/items/{item_id}/render")
    def put_render(item_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        """Attach slide images rendered elsewhere (`cmsbot hub render`), already uploaded with /media."""
        return hub.set_render(actor, item_id, body["urls"], style=body["style"], render_hash=body["hash"])

    @app.put("/api/items/{item_id}/claims")
    def put_claims(item_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.set_claims(actor, item_id, body.get("claims", []))

    @app.patch("/api/claims/{claim_id}")
    def patch_claim(claim_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.review_claim(actor, claim_id, **{k: body[k] for k in ("status", "text", "citation", "citation_url", "notes") if k in body})

    @app.post("/api/items/{item_id}/{action}")
    def item_action(item_id: int, action: str, body: dict[str, Any] = Body(default={}), actor: Actor = Depends(who)):
        if action == "submit":
            return hub.submit(actor, item_id)
        if action == "fail":
            return hub.fail(actor, item_id, body.get("error", ""))
        if action == "approve":
            changes = {k: body[k] for k in item_fields if k in body}
            if changes:
                hub.update_item(actor, item_id, note=body.get("note", "") or "reviewer edit", **changes)
            if actor.role == "admin" and refresh_media(actor, item_id):
                raise HubError("Saved. The video is being rendered again with your edits; approve it when that finishes "
                               "(about a minute).", 409)
            return hub.approve(actor, item_id, scheduled_at=body.get("scheduled_at"))
        if action == "approve-script":
            return hub.approve_script(actor, item_id, body.get("notes", ""))
        if action == "request-changes":
            return hub.request_changes(actor, item_id, body.get("notes", ""))
        if action == "reject":
            return hub.reject(actor, item_id, body.get("notes", ""))
        if action == "claim":
            return hub.claim(actor, item_id)
        if action == "release":
            return hub.release(actor, item_id, body.get("reason", ""))
        if action == "posted":
            return hub.mark_posted(actor, item_id, post_url=body.get("post_url", ""), post_id=body.get("post_id", ""),
                                   posted_at=body.get("posted_at"), account=body.get("account", ""))
        if action == "metrics":
            return hub.report_metrics(actor, item_id, body.get("metrics", {}), measured_at=body.get("measured_at"),
                                      source=body.get("source", ""))
        raise HubError(f"Unknown action '{action}'.", 404)

    @app.get("/api/ready")
    def ready(project: str | None = None, channel: str | None = None, due_within_hours: float = 24,
              actor: Actor = Depends(who)):
        return hub.ready_to_post(actor, project=project, channel=channel, due_within_hours=due_within_hours)

    @app.post("/api/projects/{slug}/costs")
    def post_cost(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.record_cost(actor, slug, amount_usd=body["amount_usd"], category=body["category"],
                               vendor=body.get("vendor", ""), item_id=body.get("item_id"), note=body.get("note", ""))

    @app.post("/api/projects/{slug}/conversions")
    def post_conversions(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.report_conversions(actor, slug, body.get("rows", []), source=body.get("source", "manual"))

    @app.get("/api/projects/{slug}/signals")
    def get_signals(slug: str, status: str = "new", actor: Actor = Depends(who)):
        return hub.list_signals(actor, slug, status=status)

    @app.post("/api/projects/{slug}/signals")
    def post_signal(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.submit_signal(actor, slug, source=body.get("source", actor.name), topic=body["topic"],
                                 summary=body.get("summary", ""), channel=body.get("channel", ""),
                                 evidence=body.get("evidence"), score=body.get("score", 0),
                                 expires_in_days=body.get("expires_in_days", 7))

    @app.post("/api/signals/{signal_id}/dismiss")
    def dismiss_signal(signal_id: int, actor: Actor = Depends(who)):
        hub.dismiss_signal(actor, signal_id)
        return {"id": signal_id, "status": "dismissed"}

    @app.get("/api/projects/{slug}/learnings")
    def get_learnings(slug: str, status: str | None = None, actor: Actor = Depends(who)):
        return hub.list_learnings(actor, slug, status)

    @app.post("/api/projects/{slug}/learnings")
    def post_learning(slug: str, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.propose_learning(actor, slug, body["statement"], scope=body.get("scope"), evidence=body.get("evidence"))

    @app.patch("/api/learnings/{learning_id}")
    def patch_learning(learning_id: int, body: dict[str, Any] = Body(...), actor: Actor = Depends(who)):
        return hub.decide_learning(actor, learning_id, body["status"])

    @app.get("/api/projects/{slug}/performance")
    def get_performance(slug: str, age_hours: float = 168, actor: Actor = Depends(who)):
        return hub.performance(actor, slug, age_hours=age_hours)

    if with_mcp:
        built_server, mcp_app = build_mcp(hub, storage, do_import)
        mcp_holder["server"] = built_server
        app.mount("/", mcp_app)
    return app
