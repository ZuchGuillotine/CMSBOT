"""Render a carousel item's slides to images and attach them to the item."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx

from cmsbot import carousel, video
from cmsbot.hub.media import media_key
from cmsbot.hub.service import Actor, Hub, HubError


def preview(hub: Hub, actor: Actor, item_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Slide HTML for an item, optionally for an unsaved draft of its body."""
    item = hub.get_item(actor, item_id)
    if item["kind"] != "carousel":
        raise HubError("Only carousel items have slides.")
    brand = hub.brand(item["project"])
    try:
        built = carousel.build_slides(body if body is not None else item["body"], brand)
    except carousel.CarouselError as exc:
        raise HubError(str(exc)) from exc
    rendered = (item["audit"].get("render") or {}).get("hash")
    return {**built, "rendered_hash": rendered, "up_to_date": rendered == built["hash"], "can_render": carousel.can_render()}


def render_item(hub: Hub, storage, actor: Actor, item_id: int) -> dict[str, Any]:
    item = hub.get_item(actor, item_id)
    if item["kind"] != "carousel":
        raise HubError("Only carousel items can be rendered.")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = carousel.render_pngs(item["body"], hub.brand(item["project"]), Path(tmp),
                                          media_root=getattr(storage, "root", None))
        except carousel.CarouselError as exc:
            raise HubError(str(exc), 503) from exc
        urls = [storage.put(media_key(item["project"], item_id, path.name), path.read_bytes(), "image/png")
                for path in result["files"]]
    return hub.set_render(actor, item_id, urls, style=result["style"], render_hash=result["hash"])


def _fetch_assets(hub: Hub, storage, item: dict[str, Any], folder: Path) -> dict[str, Path]:
    """Bring an item's footage and sound onto local disk for ffmpeg."""
    assets: dict[str, Path] = {}
    for role, url in hub.video_assets(item).items():
        local = storage.path_for(url[len("/media/"):]) if url.startswith("/media/") else None
        if local is not None:
            assets[role] = local
            continue
        if not url.startswith(("http://", "https://")):
            raise HubError(f"Can't read the file attached as {role}.")
        target = folder / f"{role}{Path(url.split('?')[0]).suffix or '.bin'}"
        with httpx.stream("GET", url, timeout=300, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise HubError(f"Could not download the file attached as {role} ({response.status_code}).", 502)
            with target.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        assets[role] = target
    return assets


def render_video_item(hub: Hub, storage, actor: Actor, item_id: int) -> dict[str, Any]:
    item = hub.get_item(actor, item_id)
    if item["kind"] != "short_video":
        raise HubError("Only short_video items can be rendered as video.")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        try:
            report = video.render(item["body"], hub.brand(item["project"]), folder / "out",
                                  assets=_fetch_assets(hub, storage, item, folder), asset_refs=hub.video_assets(item))
        except video.VideoError as exc:
            raise HubError(str(exc), 503 if "needs ffmpeg" in str(exc) else 400) from exc
        video_url = storage.put(media_key(item["project"], item_id, "video.mp4"), report["video"].read_bytes(), "video/mp4")
        sheet_url = storage.put(media_key(item["project"], item_id, "contact-sheet.jpg"), report["contact_sheet"].read_bytes(), "image/jpeg")
    return hub.set_video(actor, item_id, video_url, contact_sheet_url=sheet_url, report=report)


def render_any(hub: Hub, storage, actor: Actor, item_id: int) -> dict[str, Any]:
    kind = hub.get_item(actor, item_id)["kind"]
    if kind == "short_video":
        return render_video_item(hub, storage, actor, item_id)
    return render_item(hub, storage, actor, item_id)


def render_if_stale(hub: Hub, storage, actor: Actor, item_id: int) -> None:
    """After an edit: refresh slide images or a rendered video when this machine can. Otherwise the blocker stays visible."""
    item = hub.get_item(actor, item_id)
    if item["kind"] == "carousel" and item["slides_stale"] and carousel.can_render() and item["body"].get("slides"):
        render_item(hub, storage, actor, item_id)
    elif item["video_state"] == "stale" and video.can_render():
        render_video_item(hub, storage, actor, item_id)
