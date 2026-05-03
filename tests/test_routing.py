from __future__ import annotations

import json

from cmsbot.routing import decide_model_route, load_model_routing


def test_model_routing_decision_escalates_on_matching_trigger(tmp_path):
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "model-routing.json").write_text(
        json.dumps(
            {
                "routing": {
                    "final_polish": {
                        "tier": "premium",
                        "escalate_on": ["de_ai_failed", "human_review"],
                    },
                    "source_draft": {
                        "tier": "mid",
                        "escalate_on": ["regulated_claims"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    routing = load_model_routing(tmp_path)
    decision = decide_model_route(
        routing,
        work_type="source_draft",
        triggers=["regulated_claims", "other"],
    )

    assert decision.escalate is True
    assert decision.tier == "mid->premium"
    assert decision.matched_triggers == ["regulated_claims"]
