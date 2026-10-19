from __future__ import annotations

import asyncio
import json

import httpx

from cmsbot.config import Config, ProviderConfig
from cmsbot.models import LLMResponse
from cmsbot.multipass import pipeline as mp
from cmsbot.multipass.pipeline import MultipassPipeline, find_gaps, remaining_structural_issues
from cmsbot.multipass.shape_check import hard_flags, measure
from cmsbot.multipass.sources import Paper, extract_identifier, parse_pubmed_xml, verify_citation

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle>
  <MedlineCitation><PMID>31234567</PMID><Article>
    <Journal><Title>Nutrients</Title><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
    <ArticleTitle>Magnesium <i>glycinate</i> and sleep quality</ArticleTitle>
    <Abstract><AbstractText Label="METHODS">RCT in 46 adults.</AbstractText>
              <AbstractText Label="RESULTS">Sleep efficiency improved.</AbstractText></Abstract>
  </Article></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.3390/nu13010001</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""


# ---- shape check / gaps -----------------------------------------------------


def test_shape_check_flags_roadmap_and_restated_close():
    text = (
        "# Magnesium\n\nIn this article we'll cover magnesium forms and sleep.\n\n"
        "Glycinate is gentler on the stomach than oxide in most reports.\n\n"
        "In summary, magnesium forms and sleep matter."
    )
    flags = hard_flags(measure(text))
    assert any(f.startswith("roadmap/announce") for f in flags)
    assert any(f.startswith("restate/close") for f in flags)


def test_shape_check_counts_dates_and_attributions():
    metrics = measure("In March 2024 the lab reported ferritin of 18. A coach said it changed the plan.")
    assert metrics["dates"] == {"years": 1, "months": 1}
    assert metrics["attributions"] == 2


def test_find_gaps_ignores_links_and_citations():
    text = "A user said [real quote about timing]. See [the study](https://x.y) and [PMID 123]. [1] [real quote about timing]"
    assert find_gaps(text) == ["real quote about timing"]


def test_remaining_issues_respects_keep():
    audit = {
        "features": {"has_summary_stage": True, "no_specific_dates": True, "freshness_framing": False},
        "fixes": [{"feature": "no_specific_dates", "action": "keep"}],
    }
    assert remaining_structural_issues(audit) == ["has_summary_stage"]


# ---- sources -----------------------------------------------------------------


def test_parse_pubmed_xml():
    [paper] = parse_pubmed_xml(PUBMED_XML)
    assert paper.pmid == "31234567"
    assert paper.title == "Magnesium glycinate and sleep quality"
    assert paper.year == "2021"
    assert paper.doi == "10.3390/nu13010001"
    assert "METHODS: RCT in 46 adults." in paper.abstract
    assert paper.url == "https://pubmed.ncbi.nlm.nih.gov/31234567/"


def test_extract_identifier():
    assert extract_identifier("PMID 31234567") == ("pmid", "31234567")
    assert extract_identifier("https://pubmed.ncbi.nlm.nih.gov/31234567/") == ("pmid", "31234567")
    assert extract_identifier("doi: 10.3390/nu13010001.") == ("doi", "10.3390/nu13010001")
    assert extract_identifier("Smith et al. 2020") is None
    assert extract_identifier(None) is None


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_verify_citation_pmid_and_doi():
    def handler(request: httpx.Request) -> httpx.Response:
        if "esummary" in request.url.path:
            pmid = request.url.params["id"]
            result = {"31234567": {"title": "Magnesium and sleep"}} if pmid == "31234567" else {pmid: {"error": "cannot get document summary"}}
            return httpx.Response(200, json={"result": result})
        if request.url.path.endswith("/10.3390/nu13010001"):
            return httpx.Response(200, json={"message": {"title": ["Real paper"]}})
        return httpx.Response(404)

    async def run():
        async with _client(handler) as client:
            return [
                await verify_citation("PMID 31234567", client=client),
                await verify_citation("PMID 99999999", client=client),
                await verify_citation("10.3390/nu13010001", client=client),
                await verify_citation("10.9999/made-up", client=client),
                await verify_citation(None, client=client),
            ]

    ok_pmid, bad_pmid, ok_doi, bad_doi, missing = asyncio.run(run())
    assert ok_pmid["status"] == "verified"
    assert bad_pmid["status"] == "unresolved"
    assert ok_doi["status"] == "verified" and ok_doi["title"] == "Real paper"
    assert bad_doi["status"] == "unresolved"
    assert missing["status"] == "missing"


# ---- pipeline ----------------------------------------------------------------

DRAFT_WITH_SUMMARY = """# Magnesium glycinate vs oxide

In this article we'll cover two magnesium forms.

A 2021 trial of 46 adults (PMID 31234567) found better sleep efficiency with glycinate.

A user told us [real quote about switching forms].

In summary, the form matters."""

RESTRUCTURED = """# Magnesium glycinate vs oxide

A 2021 trial of 46 adults (PMID 31234567) found better sleep efficiency with glycinate.

A user told us [real quote about switching forms].

If you switch forms, log the dose and the date you switched."""


class ScriptedProvider:
    """Returns canned output per stage, keyed by a marker in the system prompt."""

    name = "stub"

    def __init__(self):
        self.calls: list[str] = []
        self.audits = 0

    async def generate_simple(self, user_prompt, system_prompt="", max_tokens=4096, temperature=0.7):
        stage, content = self._respond(system_prompt)
        self.calls.append(stage)
        return LLMResponse(content=content, input_tokens=10, output_tokens=10, model="stub", provider="stub", cost=0.0)

    def _respond(self, system: str) -> tuple[str, str]:
        if system == mp.prompts.QUERIES_SYSTEM:
            return "queries", json.dumps({"queries": ["magnesium glycinate sleep"]})
        if system == mp.prompts.MATERIAL_SYSTEM:
            return "material", json.dumps({
                "reader": "adults who supplement", "point": "form matters",
                "material": [{"id": "M1", "kind": "study", "text": "RCT in 46 adults", "source": "PMID 31234567"}],
                "gaps": ["[real quote about switching forms]"],
            })
        if system == mp.prompts.PLAN_SYSTEM:
            return "plan", json.dumps({
                "decisions": {"opening": "number and its source", "ending": "specific next step"},
                "units": [{"n": 1, "kind": "data"}, {"n": 2, "kind": "instruction"}],
            })
        if system == mp.prompts.DRAFT_SYSTEM:
            return "draft", DRAFT_WITH_SUMMARY
        if system == mp.prompts.AUDIT_SYSTEM:
            self.audits += 1
            present = self.audits == 1
            return "audit", json.dumps({
                "features": {"has_summary_stage": present, "thesis_before_first_section": present},
                "fixes": [{"feature": "has_summary_stage", "action": "cut", "instruction": "cut the last paragraph"}] if present else [],
            })
        if system == mp.prompts.RESTRUCTURE_SYSTEM:
            return "restructure", RESTRUCTURED
        if system == mp.prompts.SURFACE_SYSTEM:
            return "surface", RESTRUCTURED
        if system == mp.prompts.CLAIMS_SYSTEM:
            return "claims", json.dumps({"claims": [
                {"text": "Glycinate improved sleep efficiency in 46 adults.", "citation": "PMID 31234567"},
                {"text": "Most people are deficient.", "citation": None},
            ]})
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


def test_multipass_pipeline_runs_all_stages(tmp_path, monkeypatch):
    stub = ScriptedProvider()
    monkeypatch.setattr(mp, "build_provider", lambda name, config: stub)

    async def fake_search(query, max_results=5):
        return [Paper(pmid="31234567", title="Magnesium and sleep", journal="Nutrients", year="2021", abstract="RCT")]

    async def fake_verify(citation):
        return {"status": "verified"} if citation == "PMID 31234567" else {"status": "missing"}

    monkeypatch.setattr(mp, "search_pubmed", fake_search)
    monkeypatch.setattr(mp, "verify_citation", fake_verify)

    config = Config(anthropic=ProviderConfig(api_key="k", model="claude-sonnet-5-5"))
    source = tmp_path / "asset" / "source"
    result, artifacts = asyncio.run(
        MultipassPipeline(config, images=False).run("Write about magnesium forms", source, ledger_dir=tmp_path)
    )

    # Structure fixed in one restructure round, then the re-audit came back clean
    assert stub.calls[:4] == ["queries", "material", "plan", "draft"]
    assert stub.calls.count("restructure") == 1
    assert "In summary" not in result.final_draft.content
    assert result.converged is True

    assert artifacts.gaps == ["real quote about switching forms"]
    assert [c["verification"]["status"] for c in artifacts.claims] == ["verified", "missing"]
    assert artifacts.papers[0]["pmid"] == "31234567"

    for name in ("content.md", "index.html", "material.json", "plan.json", "claims.json", "gaps.json", "audit.json"):
        assert (source / name).exists(), name
    audit = json.loads((source / "audit.json").read_text())
    assert len(audit["structural"]) == 2

    ledger = (tmp_path / "variety-ledger.jsonl").read_text().strip().splitlines()
    assert json.loads(ledger[-1])["opening"] == "number and its source"


def test_multipass_plan_sees_recent_pieces(tmp_path, monkeypatch):
    (tmp_path / "variety-ledger.jsonl").write_text(
        json.dumps({"title": "Earlier post", "opening": "the false default", "ending": "a consequence", "unit_kinds": ["case"]}) + "\n"
    )
    seen: dict[str, str] = {}
    stub = ScriptedProvider()
    original = stub.generate_simple

    async def spy(user_prompt, system_prompt="", **kwargs):
        if system_prompt == mp.prompts.PLAN_SYSTEM:
            seen["plan_input"] = user_prompt
        return await original(user_prompt, system_prompt, **kwargs)

    stub.generate_simple = spy
    monkeypatch.setattr(mp, "build_provider", lambda name, config: stub)

    async def fake_verify(citation):
        return {"status": "missing"}

    monkeypatch.setattr(mp, "verify_citation", fake_verify)
    config = Config(anthropic=ProviderConfig(api_key="k", model="claude-sonnet-5-5"))
    asyncio.run(MultipassPipeline(config, use_pubmed=False, images=False).run("brief", tmp_path / "s", ledger_dir=tmp_path))

    assert 'opening=the false default' in seen["plan_input"]
