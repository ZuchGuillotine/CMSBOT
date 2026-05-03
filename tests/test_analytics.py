from __future__ import annotations

import json
from datetime import date

from cmsbot.analytics import import_social_csv, load_performance_index, record_social_metrics
from cmsbot.content_ops import build_content_calendar


def test_record_social_metrics_updates_global_and_asset_set_analytics(tmp_path):
    output_root = tmp_path / "output"
    asset_set_dir = output_root / "asset-sets" / "2026" / "2026-04" / "2026-04-18-sample"
    (asset_set_dir / "analytics").mkdir(parents=True)

    record, index_path = record_social_metrics(
        output_root,
        asset_set_id="2026-04-18-sample",
        slot_id="2026-04-18-sample:carousel:1",
        platform="instagram",
        format="carousel",
        pillar="education",
        topic="lead routing audit",
        hook="Most teams miss this audit step",
        views_7d=25000,
        impressions=60000,
        saves=1200,
        shares=200,
        comments=80,
        link_clicks=140,
        conversions=12,
    )

    assert record["asset_set_id"] == "2026-04-18-sample"
    assert index_path.exists()

    records = load_performance_index(output_root)
    assert len(records) == 1
    assert records[0]["slot_id"] == "2026-04-18-sample:carousel:1"

    asset_perf = json.loads((asset_set_dir / "analytics" / "performance.json").read_text(encoding="utf-8"))
    assert asset_perf["asset_set_id"] == "2026-04-18-sample"
    assert asset_perf["records"][0]["platform"] == "instagram"


def test_build_content_calendar_uses_recent_analytics_signals():
    today = date.today()
    analytics_records = [
        {
            "asset_set_id": "set-1",
            "slot_id": "set-1:carousel:1",
            "platform": "instagram",
            "format": "carousel",
            "pillar": "education",
            "hook": "Most teams miss this step",
            "posted_date": today.isoformat(),
            "performance_score": 88,
        },
        {
            "asset_set_id": "set-2",
            "slot_id": "set-2:thread:1",
            "platform": "x",
            "format": "thread",
            "pillar": "product-proof",
            "hook": "Here is what changes when you fix the workflow",
            "posted_date": today.isoformat(),
            "performance_score": 40,
        },
    ]

    payload = build_content_calendar(
        "Acme Cloud",
        "acme-cloud",
        content_ops={
            "pillars": [
                {"key": "product-proof", "label": "Product Proof", "goals": ["conversion"], "default_formats": ["thread", "blog"]},
                {"key": "education", "label": "Education", "goals": ["traffic"], "default_formats": ["thread", "carousel"]},
            ],
            "cadence": {"planning_horizon_days": 3, "default_schedule": [{"day_offset": 0, "formats": ["thread", "carousel"]}]},
        },
        seo={"long_tail_topics": ["lead routing audit"]},
        start_date=today,
        days=2,
        analytics_records=analytics_records,
    )

    first_entry = payload["entries"][0]
    assert first_entry["pillar"] == "education"
    assert first_entry["asset_set"][0] == "carousel"
    assert first_entry["distribution_slots"][0]["slot_id"].startswith(first_entry["id"])
    assert any("winning hook style" in angle for angle in first_entry["hook_angles"])


def test_import_social_csv_maps_common_platform_columns(tmp_path):
    csv_path = tmp_path / "social.csv"
    csv_path.write_text(
        "asset_set_id,platform,format,views,saves,shares,comments,link clicks,score,date\n"
        "asset-1,instagram,carousel,10000,320,40,12,80,91.5,2026-05-01\n",
        encoding="utf-8",
    )

    imported, destination = import_social_csv(
        tmp_path / "output",
        csv_path,
        platform="instagram",
        format="carousel",
    )

    assert imported == 1
    assert destination.exists()
    records = load_performance_index(tmp_path / "output")
    assert records[0]["asset_set_id"] == "asset-1"
    assert records[0]["views_7d"] == 10000
    assert records[0]["link_clicks"] == 80
    assert records[0]["performance_score"] == 91.5
