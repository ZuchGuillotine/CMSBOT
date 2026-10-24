from __future__ import annotations

import asyncio
import json

from click.testing import CliRunner

from cmsbot.cli import main
from cmsbot.config import Config, ProviderConfig
from cmsbot.models import LLMResponse
from cmsbot.multipass import derive as dv
from cmsbot.multipass.derive import DerivePipeline, ledger_violations, load_ledger

CLAIMS = [
    {"text": "In a four-week trial of 155 adults, ISI scores fell by 3.9 points with 250 mg magnesium bisglycinate.",
     "citation": "PMID 40918053",
     "verification": {"status": "verified", "url": "https://pubmed.ncbi.nlm.nih.gov/40918053/"}},
    {"text": "Placebo ISI scores fell by 2.3 points.", "citation": "PMID 40918053",
     "verification": {"status": "verified", "url": "https://pubmed.ncbi.nlm.nih.gov/40918053/"}},
    {"text": "Most people are deficient.", "citation": None, "verification": {"status": "missing"}},
]


class StubProvider:
    name = "stub"

    def __init__(self, items, fixed=None):
        self.items, self.fixed, self.calls = items, fixed, []

    async def generate_simple(self, user_prompt, system_prompt="", max_tokens=4096, temperature=0.7):
        if system_prompt == dv.prompts.DERIVE_FIX_SYSTEM:
            self.calls.append("fix")
            content = json.dumps(self.fixed)
        else:
            self.calls.append("draft")
            content = json.dumps({"items": self.items})
        return LLMResponse(content=content, input_tokens=10, output_tokens=10, model="stub", provider="stub", cost=0.0)


def _config(tmp_path):
    key = ProviderConfig(api_key="x", model="m")
    return Config(anthropic=key, anthropic_opus=key, output_dir=tmp_path)


def test_load_ledger_keeps_verified_and_admin_verified_claims():
    ledger = load_ledger(CLAIMS + [{"text": "Admin checked.", "status": "verified", "citationUrl": "https://doi.org/x"}])
    assert [c["id"] for c in ledger] == ["L1", "L2", "L3"]
    assert ledger[2]["url"] == "https://doi.org/x"
    assert len(load_ledger(CLAIMS, include_unverified=True)) == 2  # uncited claims never qualify


def test_ledger_gate_flags_figures_outside_the_ledger():
    ledger = load_ledger(CLAIMS)
    ok = "ISI fell 3.9 points on 250 mg ([PMID 40918053](https://pubmed.ncbi.nlm.nih.gov/40918053/))."
    assert ledger_violations(ok, ledger, ["L1"]) == []
    bad = ledger_violations("Take 400 mg at night.", ledger, ["L9"])
    assert "unknown claim id L9" in bad and "figure '400' is not in the claim ledger" in bad


def test_derive_fixes_gate_failures_once(tmp_path, monkeypatch):
    draft = {"angle": "a", "posts": ["I tried 400 mg and slept great.", "ISI fell 3.9 points."], "claim_ids": ["L1"]}
    fixed = {"posts": ["ISI scores fell 3.9 points on 250 mg in a four-week trial.", "Placebo fell 2.3."]}
    stub = StubProvider([draft], fixed)
    monkeypatch.setattr(dv, "build_provider", lambda name, config: stub)
    payload = asyncio.run(DerivePipeline(_config(tmp_path)).run(
        "thread", brief="b", anchor_title="T", anchor_text="# T", ledger=load_ledger(CLAIMS), count=1,
    ))
    item = payload["items"][0]
    assert stub.calls == ["draft", "fix"]
    assert any("400" in p for p in item["audit"]["fixed"])
    assert item["audit"]["remaining"] == []
    assert item["posts"] == fixed["posts"]


def test_newsletter_gets_anchor_url_and_html(tmp_path, monkeypatch):
    item = {"subject": "S", "preheader": "P", "title": "T", "claim_ids": ["L2"],
            "body_markdown": "Placebo fell 2.3 points. [Read it]({{anchor_url}}). [quote from a reader about timing]"}
    monkeypatch.setattr(dv, "build_provider", lambda name, config: StubProvider([item]))
    payload = asyncio.run(DerivePipeline(_config(tmp_path)).run(
        "newsletter", brief="b", anchor_title="T", anchor_text="# T", ledger=load_ledger(CLAIMS),
        anchor_url="https://example.com/blog/t",
    ))
    out = payload["items"][0]
    assert "https://example.com/blog/t" in out["body_markdown"] and "<p>" in out["body_html"]
    assert out["gaps"] == ["quote from a reader about timing"]


def test_derive_cli_writes_distribution_file(tmp_path, monkeypatch):
    asset = tmp_path / "set"
    (asset / "source").mkdir(parents=True)
    (asset / "source" / "content.md").write_text("# Magnesium\n\nBody.\n")
    (asset / "source" / "claims.json").write_text(json.dumps({"claims": CLAIMS}))
    video = {"title": "v", "hook": "ISI fell 3.9 points.", "beats": [{"narration": "On 250 mg.", "visual": "paper", "overlay_text": "250 mg"}],
             "ending": "Track it for four weeks.", "caption": "magnesium sleep", "target_duration_seconds": 40, "claim_ids": ["L1"]}
    monkeypatch.setattr(dv, "build_provider", lambda name, config: StubProvider([video]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    result = CliRunner().invoke(main, ["derive", str(asset), "--kind", "video_script", "--count", "1"])
    assert result.exit_code == 0, result.output
    payload = json.loads((asset / "distribution" / "derivatives" / "video_script.json").read_text())
    assert payload["items"][0]["audit"]["remaining"] == []
    assert [c["id"] for c in payload["ledger"]] == ["L1", "L2"]
