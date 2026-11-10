from __future__ import annotations

import asyncio
import json

import pytest

from cmsbot.config import Config, ProviderConfig
from cmsbot.models import LLMResponse
from cmsbot.multipass import refresh as rf
from cmsbot.multipass.refresh import RefreshError, RefreshPipeline, existing_images, new_figures, structure_counts

ORIGINAL = """# Magnesium forms for sleep

In a four-week trial of 155 adults, ISI scores fell by 3.9 points with 250 mg bisglycinate (PMID 40918053).
Placebo ISI scores fell by 2.3 points. Take 250 mg an hour before bed.

Oxide absorbed less in a 2003 comparison [citation needed for absorption figures]."""

REVISED = """# Magnesium forms for sleep

In a four-week trial of 155 adults, ISI scores fell with bisglycinate (PMID 40918053).

## How much of the drop was placebo?

| Arm | ISI change |
|---|---|
| 250 mg bisglycinate | -3.9 |
| Placebo | -2.3 |

Most of the change also happened on placebo. Log your dose and sleep scores to compare.

## Does the form matter?

Oxide absorbed less in a 2003 comparison [citation needed for absorption figures]."""


class Stub:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def generate_simple(self, user_prompt, system_prompt="", max_tokens=4096, temperature=0.7):
        self.calls.append(system_prompt)
        return LLMResponse(content=self.responses.pop(0), input_tokens=1, output_tokens=1, model="stub", provider="stub", cost=0.0)


def _config():
    key = ProviderConfig(api_key="x", model="m")
    return Config(anthropic=key, anthropic_opus=key)


def _asset(tmp_path, with_hero=True):
    src = tmp_path / "set" / "source"
    (src / "images").mkdir(parents=True)
    (src / "content.md").write_text(ORIGINAL)
    (src / "index.html").write_text("<html>old</html>")
    if with_hero:
        (src / "images" / "hero.jpg").write_bytes(b"old-hero")
    return tmp_path / "set"


def test_new_figures_flags_only_numbers_missing_from_the_original():
    assert new_figures(ORIGINAL, REVISED) == []
    assert new_figures(ORIGINAL, REVISED + "\n\nAbout 40% of people respond.") == ["40"]


def test_structure_counts_headings_and_tables():
    assert structure_counts(REVISED) == {"headings": 2, "tables": 1}
    assert structure_counts(ORIGINAL) == {"headings": 0, "tables": 0}


def test_refresh_rewrites_content_keeps_backup_and_existing_images(tmp_path, monkeypatch):
    stub = Stub([REVISED])
    monkeypatch.setattr(rf, "build_provider", lambda name, config: stub)
    root = _asset(tmp_path)
    report = asyncio.run(RefreshPipeline(_config(), images=False).run(root, "BRIEF", "magnesium"))

    src = root / "source"
    assert (src / "content.md").read_text().strip() == REVISED
    assert "<table>" in (src / "index.html").read_text()
    assert 'src="images/hero.jpg"' in (src / "index.html").read_text()
    assert (src / report["backup"] / "content.md").read_text() == ORIGINAL
    assert report["headings"] == 2 and report["tables"] == 1
    assert json.loads((src / "gaps.json").read_text())["gaps"] == ["citation needed for absorption figures"]


def test_refresh_retries_then_fails_on_invented_figures(tmp_path, monkeypatch):
    bad = REVISED + "\n\nAbout 40% respond."
    stub = Stub([bad, bad])
    monkeypatch.setattr(rf, "build_provider", lambda name, config: stub)
    root = _asset(tmp_path)
    with pytest.raises(RefreshError, match="40"):
        asyncio.run(RefreshPipeline(_config(), images=False).run(root, "BRIEF", "magnesium"))
    assert len(stub.calls) == 2 and stub.calls[1] == rf.prompts.REFRESH_FIX_SYSTEM
    assert (root / "source" / "content.md").read_text() == ORIGINAL  # untouched on failure


def test_existing_images_maps_hero_and_sections(tmp_path):
    src = tmp_path / "source"
    (src / "images").mkdir(parents=True)
    for name in ("hero.jpg", "section-2.svg", "section-1.jpg", "notes.txt"):
        (src / "images" / name).write_bytes(b"x")
    specs = existing_images(src, "Title")
    assert [(s.placement, s.file_path) for s in specs] == [
        ("hero", "images/hero.jpg"), ("section-1", "images/section-1.jpg"), ("section-2", "images/section-2.svg")
    ]
