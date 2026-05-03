"""Review-gate evaluation for asset-set approval and publishing readiness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cmsbot.content_ops import resolve_asset_set_root, resolve_source_dir


def evaluate_asset_gates(asset_set_dir: str | Path) -> dict[str, Any]:
    """Evaluate deterministic readiness gates for one asset set."""
    asset_root = resolve_asset_set_root(asset_set_dir)
    manifest = _load_json(asset_root / "manifest.json")
    source_dir = resolve_source_dir(asset_root)
    source_files = [
        source_dir / "content.md",
        source_dir / "index.html",
        source_dir / "blog_draft.json",
    ]
    distribution_dir = asset_root / "distribution"
    de_ai_reports = sorted(distribution_dir.glob("*/de_ai_report.json")) if distribution_dir.exists() else []
    approvals = _load_approvals(asset_root)

    draft_ready = any(path.exists() for path in source_files)
    de_ai_passed = bool(de_ai_reports)
    de_ai_findings: list[dict[str, Any]] = []
    for report_path in de_ai_reports:
        report = _load_json(report_path)
        passed = int(report.get("score", 0) or 0) >= 80 and not bool(report.get("requires_human_review"))
        de_ai_passed = de_ai_passed and passed
        de_ai_findings.append(
            {
                "platform": report.get("platform") or report_path.parent.name,
                "format": report.get("format") or "",
                "score": report.get("score"),
                "requires_human_review": bool(report.get("requires_human_review")),
                "passed": passed,
                "path": str(report_path),
            }
        )

    human_approved = bool(approvals.get("human_approved"))
    publish_ready = draft_ready and de_ai_passed and human_approved
    gates = {
        "draft_ready": {
            "passed": draft_ready,
            "reason": "Source content exists." if draft_ready else "No source content, HTML, or blog draft found.",
        },
        "de_ai_passed": {
            "passed": de_ai_passed,
            "reason": "All de-AI reports passed." if de_ai_passed else "Missing or failing de-AI review reports.",
        },
        "human_approved": {
            "passed": human_approved,
            "reason": "Human approval recorded." if human_approved else "Human approval has not been recorded.",
        },
        "publish_ready": {
            "passed": publish_ready,
            "reason": (
                "Artifact is ready for product-repo publishing adapter."
                if publish_ready
                else "Publishing remains blocked by one or more gates."
            ),
        },
    }
    return {
        "asset_set_id": manifest.get("asset_set_id") or asset_root.name,
        "asset_set_dir": str(asset_root),
        "gates": gates,
        "de_ai_reports": de_ai_findings,
        "approvals": approvals,
    }


def record_human_approval(
    asset_set_dir: str | Path,
    *,
    approved_by: str,
    notes: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Record explicit human approval for an asset set."""
    asset_root = resolve_asset_set_root(asset_set_dir)
    approval_dir = asset_root / "approval"
    approval_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "human_approved": True,
        "approved_by": approved_by,
        "notes": notes or "",
    }
    approval_path = approval_dir / "approval.json"
    approval_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _update_manifest_approval(asset_root, approval_path)
    return payload, approval_path


def _load_approvals(asset_root: Path) -> dict[str, Any]:
    approval_path = asset_root / "approval" / "approval.json"
    if not approval_path.exists():
        return {"human_approved": False}
    payload = _load_json(approval_path)
    payload.setdefault("human_approved", False)
    return payload


def _update_manifest_approval(asset_root: Path, approval_path: Path) -> None:
    manifest_path = asset_root / "manifest.json"
    if not manifest_path.exists():
        return
    payload = _load_json(manifest_path)
    payload["approval"] = {
        "human_approved": True,
        "approval_file": str(approval_path.relative_to(asset_root)),
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
