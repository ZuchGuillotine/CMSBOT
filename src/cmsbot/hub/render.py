"""Render a carousel item's slides to images and attach them to the item."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from cmsbot import carousel
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


def render_if_stale(hub: Hub, storage, actor: Actor, item_id: int) -> None:
    """After an edit: refresh slide images when this machine can. Otherwise the blocker stays visible."""
    item = hub.get_item(actor, item_id)
    if item["kind"] == "carousel" and item["slides_stale"] and carousel.can_render() and item["body"].get("slides"):
        render_item(hub, storage, actor, item_id)
