from __future__ import annotations

import json
from datetime import datetime, timezone

from cmsbot.goals import initialize_ops_scaffold, load_goal_statuses, prepare_goal_handoff, summarize_goal_run
from cmsbot.workflow import finish_run, load_latest_runs, make_run_id, start_run


def test_initialize_ops_scaffold_writes_goals_and_strategy(tmp_path):
    written = initialize_ops_scaffold(tmp_path)

    assert tmp_path / "goals" / "weekly-content-calendar.goal.md" in written
    assert tmp_path / "strategy" / "strategy-memory.md" in written
    assert (tmp_path / "strategy" / "model-routing.json").exists()

    statuses = load_goal_statuses(tmp_path)
    assert {entry.title for entry in statuses} >= {"Weekly Content Calendar", "Daily Asset Generation"}
    assert all(entry.status == "active" for entry in statuses)


def test_workflow_run_records_are_resumable(tmp_path):
    run_id = make_run_id("De AI Review", now=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc))
    assert run_id == "20260502T120000Z-de-ai-review"

    record, path = start_run(
        tmp_path,
        kind="de-ai-review",
        parameters={"platform": "instagram"},
        files=["asset-set"],
        run_id=run_id,
    )
    assert record["status"] == "running"
    assert path == tmp_path / "runs" / run_id / "run.json"

    finished = finish_run(
        path,
        files_changed=["distribution/instagram/de_ai_report.json"],
        summary="Reviewed one artifact.",
        next_safe_action="Human approval.",
    )
    assert finished["status"] == "completed"
    assert finished["files_changed"] == ["distribution/instagram/de_ai_report.json"]

    latest = load_latest_runs(tmp_path)
    assert latest[0]["run_id"] == run_id
    assert json.loads(path.read_text(encoding="utf-8"))["next_safe_action"] == "Human approval."


def test_start_run_avoids_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr("cmsbot.workflow.make_run_id", lambda kind: "20260502T120000Z-de-ai-review")

    first, _ = start_run(tmp_path, kind="de-ai-review")
    second, second_path = start_run(tmp_path, kind="de-ai-review")

    assert first["run_id"] == "20260502T120000Z-de-ai-review"
    assert second["run_id"] == "20260502T120000Z-de-ai-review-2"
    assert second_path == tmp_path / "runs" / "20260502T120000Z-de-ai-review-2" / "run.json"


def test_prepare_goal_handoff_and_summary(tmp_path):
    initialize_ops_scaffold(tmp_path)

    handoff, run_path, handoff_path = prepare_goal_handoff(
        tmp_path,
        tmp_path / "output",
        goal="weekly-content-calendar",
    )

    assert handoff["goal"] == "Weekly Content Calendar"
    assert handoff_path.exists()
    assert "PR Creation Cue" in handoff_path.read_text(encoding="utf-8")
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "completed"

    record, summary_path = summarize_goal_run(tmp_path / "output", run_id=handoff["run_id"])
    assert record["run_id"] == handoff["run_id"]
    assert summary_path.exists()
