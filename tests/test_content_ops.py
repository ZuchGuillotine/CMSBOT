from __future__ import annotations

from datetime import date

from cmsbot.blog_draft import export_blog_draft
from cmsbot.content_ops import (
    build_content_calendar,
    prepare_generate_layout,
    resolve_asset_set_root,
    resolve_source_dir,
)
from cmsbot.project import make_project_profile


def test_make_project_profile_includes_content_ops_defaults():
    profile = make_project_profile("Acme Ops")

    assert "content_ops" in profile
    assert profile["content_ops"]["pillars"]
    assert profile["content_ops"]["cadence"]["planning_horizon_days"] == 30


def test_prepare_generate_layout_uses_asset_set_structure_for_default_runs(tmp_path):
    layout = prepare_generate_layout(tmp_path, "How to debug flaky webhooks")

    assert layout.layout == "asset_set"
    assert layout.asset_set_dir.parent.name == date.today().strftime("%Y-%m")
    assert layout.source_dir == layout.asset_set_dir / "source"
    assert layout.asset_set_id.startswith(date.today().isoformat())


def test_prepare_generate_layout_accepts_calendar_asset_set_id(tmp_path):
    layout = prepare_generate_layout(
        tmp_path,
        "How to track recovery",
        requested_asset_set_id="2026-05-04-recovery-how-to-track-recovery",
    )

    assert layout.layout == "asset_set"
    assert layout.asset_set_id == "2026-05-04-recovery-how-to-track-recovery"
    assert layout.asset_set_dir == (
        tmp_path
        / "asset-sets"
        / "2026"
        / "2026-05"
        / "2026-05-04-recovery-how-to-track-recovery"
    )
    assert layout.source_dir == layout.asset_set_dir / "source"


def test_build_content_calendar_generates_entries_and_slots():
    content_ops = {
        "pillars": [
            {
                "key": "education",
                "label": "Education",
                "goals": ["traffic"],
                "default_formats": ["blog", "thread", "carousel"],
            }
        ],
        "cadence": {
            "timezone": "America/Los_Angeles",
            "planning_horizon_days": 7,
            "default_schedule": [
                {"day_offset": 0, "formats": ["carousel", "thread"]},
                {"day_offset": 2, "formats": ["blog"]},
            ],
        },
    }
    seo = {"long_tail_topics": ["lead routing audit"]}

    payload = build_content_calendar(
        "Acme Ops",
        "acme-ops",
        content_ops=content_ops,
        seo=seo,
        start_date=date(2026, 4, 18),
        days=3,
    )

    assert payload["project"]["slug"] == "acme-ops"
    assert len(payload["entries"]) == 3
    assert payload["entries"][0]["topic"] == "lead routing audit"
    assert payload["entries"][0]["distribution_slots"][0]["platform"] == "instagram"
    assert payload["entries"][0]["distribution_slots"][-1]["format"] == "blog"


def test_export_blog_draft_accepts_asset_set_root(tmp_path):
    asset_set_dir = tmp_path / "asset-sets" / "2026" / "2026-04" / "2026-04-18-sample"
    source_dir = asset_set_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "index.html").write_text(
        "<html><head><title>Sample Draft</title></head><body><article><p>Useful body copy for the draft.</p></article></body></html>",
        encoding="utf-8",
    )

    payload, destination = export_blog_draft(asset_set_dir)

    assert payload["title"] == "Sample Draft"
    assert destination == source_dir / "blog_draft.json"


def test_resolve_source_dir_and_asset_root_support_asset_set_layout(tmp_path):
    asset_set_dir = tmp_path / "asset-sets" / "2026" / "2026-04" / "2026-04-18-sample"
    source_dir = asset_set_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text("# Title\n", encoding="utf-8")

    assert resolve_source_dir(asset_set_dir) == source_dir
    assert resolve_asset_set_root(asset_set_dir) == asset_set_dir
    assert resolve_asset_set_root(source_dir) == asset_set_dir
