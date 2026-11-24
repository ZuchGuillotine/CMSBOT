"""Map a CMSBOT asset set on disk (multipass anchor + `derive` output) to Hub items."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# derive kind -> (hub kind, default channel)
DERIVED = {
    "thread": ("thread", "x"),
    "carousel": ("carousel", "instagram"),
    "video_script": ("short_video", "tiktok"),
    "newsletter": ("newsletter", "email"),
}
ANCHOR_URL_TOKEN = "{{anchor_url}}"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _fill(value: Any, link: str) -> Any:
    if isinstance(value, str):
        return value.replace(ANCHOR_URL_TOKEN, link) if link else value
    if isinstance(value, list):
        return [_fill(v, link) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, link) for k, v in value.items()}
    return value


def _derived_body(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    keep = {k: v for k, v in item.items() if k not in ("gaps", "audit", "body_html", "angle", "title")}
    if kind == "newsletter":
        keep = {"subject": item.get("subject", ""), "preheader": item.get("preheader", ""),
                "markdown": item.get("body_markdown", ""), "claim_ids": item.get("claim_ids", [])}
    return keep


def plan_asset_set(asset_set_dir: Path, *, link: str = "") -> list[dict[str, Any]]:
    """Returns item payloads, anchor first. `external_ref` makes a second push update instead of duplicate."""
    root = Path(asset_set_dir)
    source = root / "source" if (root / "source").is_dir() else root
    ref = root.name
    plan: list[dict[str, Any]] = []

    content = source / "content.md"
    if content.exists():
        markdown = content.read_text(encoding="utf-8").strip()
        heading = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
        manifest = _load(root / "manifest.json")
        claims = [
            {"text": c.get("text", ""), "citation": c.get("citation", ""),
             "citation_url": (c.get("verification") or {}).get("url") or c.get("citation_url", "")}
            for c in _load(source / "claims.json").get("claims", []) if isinstance(c, dict)
        ]
        plan.append({
            "anchor": True, "kind": "blog", "channel": "blog",
            "title": heading.group(1).strip() if heading else ref, "body": {"markdown": markdown},
            "gaps": [str(g)[:500] for g in _load(source / "gaps.json").get("gaps", [])][:50],
            "audit": _load(source / "audit.json"), "link": link, "claims": claims,
            "external_ref": f"{ref}:blog", "cost_usd": float((manifest.get("pipeline") or {}).get("total_cost") or 0),
        })

    derivatives = root / "distribution" / "derivatives"
    for file_kind, (kind, channel) in DERIVED.items():
        payload = _load(derivatives / f"{file_kind}.json")
        for index, item in enumerate(payload.get("items", []), start=1):
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("subject") or item.get("angle") or item.get("hook") or f"{kind} {index}"
            plan.append({
                "kind": kind, "channel": channel, "title": f"{kind.replace('_', ' ').title()}: {str(title)[:120]}",
                "body": _fill(_derived_body(file_kind, item), link), "gaps": [str(g)[:500] for g in item.get("gaps", [])],
                "audit": item.get("audit") or {}, "link": link,
                "attributes": {k: str(item[k]) for k in ("format", "style", "angle") if item.get(k)},
                "external_ref": f"{ref}:{file_kind}:{index}",
                "cost_usd": round(float(payload.get("cost_usd") or 0) / max(1, len(payload["items"])), 4),
            })
    if not plan:
        raise ValueError(f"No source/content.md or distribution/derivatives/*.json under {root}.")
    return plan
