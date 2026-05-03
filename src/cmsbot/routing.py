"""Model routing policy loading and deterministic escalation decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ROUTING_PATH = Path("strategy") / "model-routing.json"


@dataclass(frozen=True)
class RoutingDecision:
    work_type: str
    tier: str
    escalate: bool
    matched_triggers: list[str]
    policy: dict[str, Any]


def load_model_routing(root: str | Path = ".") -> dict[str, Any]:
    """Load model routing policy from `strategy/model-routing.json`."""
    path = Path(root) / DEFAULT_ROUTING_PATH
    if not path.exists():
        return {
            "version": 1,
            "default_policy": "cost-aware",
            "routing": {},
            "limits": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Model routing file must be a JSON object: {path}")
    payload.setdefault("routing", {})
    payload.setdefault("limits", {})
    return payload


def decide_model_route(
    routing: dict[str, Any],
    *,
    work_type: str,
    triggers: list[str] | None = None,
) -> RoutingDecision:
    """Return the configured tier and whether escalation triggers matched."""
    routes = routing.get("routing", {}) if isinstance(routing.get("routing"), dict) else {}
    policy = routes.get(work_type, {})
    if not isinstance(policy, dict):
        policy = {}

    active_triggers = {trigger.strip() for trigger in triggers or [] if trigger.strip()}
    escalate_on = {
        str(trigger).strip()
        for trigger in policy.get("escalate_on", [])
        if str(trigger).strip()
    }
    matched = sorted(active_triggers & escalate_on)
    tier = str(policy.get("tier") or "mid")
    if matched and tier != "premium":
        tier = f"{tier}->premium"

    return RoutingDecision(
        work_type=work_type,
        tier=tier,
        escalate=bool(matched),
        matched_triggers=matched,
        policy=policy,
    )
