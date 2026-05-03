"""Durable workflow run records for resumable CMSBOT operations."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def make_run_id(kind: str, *, now: datetime | None = None) -> str:
    """Create a sortable run id with a human-readable kind prefix."""
    current = now or datetime.now(timezone.utc)
    safe_kind = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-") or "run"
    return f"{current.strftime('%Y%m%dT%H%M%SZ')}-{safe_kind}"


def start_run(
    output_root: str | Path,
    *,
    kind: str,
    parameters: dict[str, Any] | None = None,
    files: list[str | Path] | None = None,
    run_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Write a `running` run record and return its payload and path."""
    root = Path(output_root)
    base_id = run_id or make_run_id(kind)
    chosen_id = base_id
    run_dir = root / "runs" / chosen_id
    if run_id is None:
        suffix = 2
        while run_dir.exists():
            chosen_id = f"{base_id}-{suffix}"
            run_dir = root / "runs" / chosen_id
            suffix += 1
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"

    payload = {
        "run_id": chosen_id,
        "kind": kind,
        "status": "running",
        "started_at": _now_iso(),
        "finished_at": None,
        "parameters": parameters or {},
        "files": [str(path) for path in files or []],
        "files_changed": [],
        "cost_summary": {},
        "summary": "",
        "failure_reason": "",
        "resume_instruction": "",
        "next_safe_action": "",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload, path


def finish_run(
    run_path: str | Path,
    *,
    status: str = "completed",
    files_changed: list[str | Path] | None = None,
    cost_summary: dict[str, Any] | None = None,
    summary: str = "",
    failure_reason: str = "",
    resume_instruction: str = "",
    next_safe_action: str = "",
) -> dict[str, Any]:
    """Finalize an existing run record."""
    path = Path(run_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(
        {
            "status": status,
            "finished_at": _now_iso(),
            "files_changed": [str(path) for path in files_changed or []],
            "cost_summary": cost_summary or {},
            "summary": summary,
            "failure_reason": failure_reason,
            "resume_instruction": resume_instruction,
            "next_safe_action": next_safe_action,
        }
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_latest_runs(output_root: str | Path, *, limit: int = 10) -> list[dict[str, Any]]:
    """Load recent run records newest first."""
    runs_dir = Path(output_root) / "runs"
    if not runs_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*/run.json"), reverse=True):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            records.append({"run_id": path.parent.name, "status": "invalid", "path": str(path)})
        if len(records) >= limit:
            break
    return records


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
