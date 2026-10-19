"""Variety ledger: the structural choices of recent pieces.

The multipass skill warns against adopting one alternative structure as a new
default. The plan stage reads the last few entries and is told to pick a
different opening, ending and unit mix.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RECENT = 6


def ledger_path(default_dir: Path) -> Path:
    """CMSBOT_VARIETY_LEDGER, else variety-ledger.jsonl next to the output tree."""
    if override := os.getenv("CMSBOT_VARIETY_LEDGER"):
        return Path(override)
    return default_dir / "variety-ledger.jsonl"


def read_recent(path: Path, limit: int = DEFAULT_RECENT) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def append_entry(path: Path, *, title: str, plan: dict[str, Any]) -> None:
    decisions = plan.get("decisions") if isinstance(plan.get("decisions"), dict) else {}
    units = plan.get("units") if isinstance(plan.get("units"), list) else []
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": title,
        "opening": decisions.get("opening"),
        "ending": decisions.get("ending"),
        "unit_kinds": [u.get("kind") for u in units if isinstance(u, dict)],
        "length_words": decisions.get("length_words"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def describe_recent(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No earlier pieces recorded."
    lines = []
    for e in entries:
        kinds = ", ".join(k for k in e.get("unit_kinds", []) if k) or "?"
        lines.append(f"- \"{e.get('title', '')}\": opening={e.get('opening')}; ending={e.get('ending')}; units=[{kinds}]")
    return "\n".join(lines)
