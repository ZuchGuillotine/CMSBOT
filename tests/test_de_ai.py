from __future__ import annotations

import json

from cmsbot.de_ai import review_de_ai
from cmsbot.gates import evaluate_asset_gates, record_human_approval


def test_review_de_ai_writes_report_review_and_manifest(tmp_path):
    asset_set_dir = tmp_path / "output" / "asset-sets" / "2026" / "2026-05" / "2026-05-02-sample"
    distribution_dir = asset_set_dir / "distribution" / "instagram"
    distribution_dir.mkdir(parents=True)
    (asset_set_dir / "manifest.json").write_text(
        json.dumps({"asset_set_id": "2026-05-02-sample"}),
        encoding="utf-8",
    )
    (distribution_dir / "carousel.md").write_text(
        """# Carousel

In today's fast-paced world, this revolutionary guide will unlock better results.
This is a very long slide line that does not fit an Instagram carousel because it reads like a compressed blog paragraph instead of visual copy.
Click here to learn more.
""",
        encoding="utf-8",
    )

    report, report_path, review_path = review_de_ai(
        asset_set_dir,
        platform="instagram",
        content_format="carousel",
        use_llm=False,
        save=True,
    )

    assert report["asset_set_id"] == "2026-05-02-sample"
    assert report["score"] < 80
    assert report["requires_human_review"] is True
    assert "generic phrasing" in report["flags"]
    assert report_path == distribution_dir / "de_ai_report.json"
    assert review_path == distribution_dir / "review.md"

    manifest = json.loads((asset_set_dir / "manifest.json").read_text(encoding="utf-8"))
    instagram = manifest["distribution_artifacts"]["instagram"]
    assert instagram["de_ai_report_json"] == "distribution/instagram/de_ai_report.json"
    assert instagram["de_ai_review_markdown"] == "distribution/instagram/review.md"


def test_review_gates_require_de_ai_and_human_approval(tmp_path):
    asset_set_dir = tmp_path / "output" / "asset-sets" / "2026" / "2026-05" / "2026-05-02-sample"
    source_dir = asset_set_dir / "source"
    distribution_dir = asset_set_dir / "distribution" / "instagram"
    source_dir.mkdir(parents=True)
    distribution_dir.mkdir(parents=True)
    (asset_set_dir / "manifest.json").write_text(
        json.dumps({"asset_set_id": "2026-05-02-sample"}),
        encoding="utf-8",
    )
    (source_dir / "content.md").write_text("# Draft\n\nUseful copy.", encoding="utf-8")
    (distribution_dir / "de_ai_report.json").write_text(
        json.dumps(
            {
                "asset_set_id": "2026-05-02-sample",
                "platform": "instagram",
                "format": "carousel",
                "score": 92,
                "requires_human_review": False,
            }
        ),
        encoding="utf-8",
    )

    before = evaluate_asset_gates(asset_set_dir)
    assert before["gates"]["draft_ready"]["passed"] is True
    assert before["gates"]["de_ai_passed"]["passed"] is True
    assert before["gates"]["human_approved"]["passed"] is False
    assert before["gates"]["publish_ready"]["passed"] is False

    approval, approval_path = record_human_approval(asset_set_dir, approved_by="Ben", notes="Looks good.")
    assert approval["human_approved"] is True
    assert approval_path.exists()

    after = evaluate_asset_gates(asset_set_dir)
    assert after["gates"]["publish_ready"]["passed"] is True
