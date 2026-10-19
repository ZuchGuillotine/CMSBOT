"""Multipass long-form pipeline (`cmsbot generate --method multipass`).

Stages, after the multipass-writing skill:
  1 material   - PubMed papers + brief -> sourced material and [bracket] gaps
  2 plan       - explicit structural decisions + rarity check, steered away from recent pieces
  3 draft      - written unit by unit from the plan
  4 structure  - audit the AI-leaning structural features; fix by cutting/moving/replacing units
  5 surface    - shape_check + de-AI heuristics; fix hard patterns with plain statements
  6 fit        - extract claims, verify every PMID/DOI, list unresolved gaps for the reviewer
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cmsbot.budget import BudgetTracker
from cmsbot.config import Config
from cmsbot.de_ai import heuristic_review
from cmsbot.models import ContentType, Draft, LLMResponse, PipelineResult
from cmsbot.multipass import prompts
from cmsbot.multipass.shape_check import hard_flags, measure
from cmsbot.multipass.sources import Paper, search_pubmed, verify_citation
from cmsbot.multipass.variety import append_entry, describe_recent, ledger_path, read_recent
from cmsbot.providers import build_provider
from cmsbot.providers.base import LLMProvider
from cmsbot.renderer import Renderer
from cmsbot.repurpose import parse_json_object

MAX_PAPERS = 12
MAX_STRUCTURE_ROUNDS = 2
# Heuristic flags from de_ai that warrant a surface pass (the rest go to the reviewer)
SURFACE_FIX_FLAGS = {"generic phrasing", "contrast frames", "disease claim", "CTA awkwardness"}
_GAP = re.compile(r"\[([^\]\n]{3,200})\](?!\()")
_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class MultipassArtifacts:
    material: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    structural_audits: list[dict[str, Any]] = field(default_factory=list)
    surface: dict[str, Any] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    papers: list[dict[str, Any]] = field(default_factory=list)


def find_gaps(text: str) -> list[str]:
    """[Bracketed] placeholders left for a person to fill (markdown links and citations excluded)."""
    gaps = []
    for match in _GAP.finditer(text):
        inner = match.group(1).strip()
        if re.match(r"^(PMID|DOI|\d)", inner, flags=re.IGNORECASE):
            continue
        if inner not in gaps:
            gaps.append(inner)
    return gaps


def remaining_structural_issues(audit: dict[str, Any]) -> list[str]:
    features = audit.get("features") if isinstance(audit.get("features"), dict) else {}
    kept = {f.get("feature") for f in audit.get("fixes", []) if isinstance(f, dict) and f.get("action") == "keep"}
    return [name for name, present in features.items() if present is True and name not in kept]


def _papers_block(papers: list[Paper]) -> str:
    if not papers:
        return "No papers retrieved. Use only the brief; bracket any study-level specifics."
    return "\n\n".join(
        f"P{i} PMID {p.pmid} ({p.year}, {p.journal}) {p.url}\nTitle: {p.title}\nAbstract: {p.abstract or '(none)'}"
        for i, p in enumerate(papers, 1)
    )


def _claims_block(claims: list[dict[str, Any]] | None) -> str:
    if not claims:
        return "None."
    return "\n".join(f"- {c.get('text')} ({c.get('citation') or 'no citation'})" for c in claims)


class MultipassPipeline:
    def __init__(self, config: Config | None = None, *, use_pubmed: bool = True, images: bool = True):
        self.config = config or Config.from_env()
        self.budget = BudgetTracker(cap=self.config.budget_cap)
        self.use_pubmed = use_pubmed
        self.images = images
        self._log: Callable[[str], None] = lambda msg: None

    def set_logger(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    def _provider(self, role: str) -> LLMProvider:
        return build_provider(self.config.provider_for(role), self.config)

    async def _call(self, stage: str, provider: LLMProvider, system: str, user: str, max_tokens: int = 8000) -> LLMResponse:
        response = await provider.generate_simple(user_prompt=user, system_prompt=system, max_tokens=max_tokens)
        self.budget.record(stage, response.provider, response.model, response.input_tokens, response.output_tokens)
        self._log(f"  [{stage}] {response.model} | {response.input_tokens}+{response.output_tokens} tokens | ${response.cost:.4f}")
        return response

    async def _call_json(self, stage: str, provider: LLMProvider, system: str, user: str, *, required: list[str]) -> dict[str, Any]:
        response = await self._call(stage, provider, system, user)
        try:
            return parse_json_object(response.content, required_keys=required)
        except ValueError:
            retry = await self._call(stage, provider, system, f"{user}\n\nReturn ONLY the JSON object, nothing else.")
            return parse_json_object(retry.content, required_keys=required)

    async def _gather_papers(self, planner: LLMProvider, task_prompt: str) -> list[Paper]:
        if not self.use_pubmed:
            return []
        plan = await self._call_json("queries", planner, prompts.QUERIES_SYSTEM, task_prompt, required=["queries"])
        queries = [q for q in plan.get("queries", []) if isinstance(q, str) and q.strip()][:3]
        results = await asyncio.gather(*(search_pubmed(q, max_results=5) for q in queries))
        papers: dict[str, Paper] = {}
        for batch in results:
            for paper in batch:
                papers.setdefault(paper.pmid, paper)
        self._log(f"  [sources] {len(papers)} PubMed papers from {len(queries)} queries")
        return list(papers.values())[:MAX_PAPERS]

    async def _structure_pass(self, task_prompt: str, material_json: str, draft: str, artifacts: MultipassArtifacts) -> str:
        auditor, restructurer = self._provider("auditor"), self._provider("restructurer")
        for round_num in range(1, MAX_STRUCTURE_ROUNDS + 1):
            audit = await self._call_json(
                "structure_audit", auditor, prompts.AUDIT_SYSTEM, f"DRAFT:\n\n{draft}", required=["features"]
            )
            audit["round"] = round_num
            artifacts.structural_audits.append(audit)
            issues = remaining_structural_issues(audit)
            if not issues:
                break
            fixes = [f for f in audit.get("fixes", []) if isinstance(f, dict) and f.get("action") != "keep"]
            self._log(f"  [structure] round {round_num}: {', '.join(issues)}")
            response = await self._call(
                "restructure", restructurer, prompts.RESTRUCTURE_SYSTEM,
                f"BRIEF:\n{task_prompt}\n\nMATERIAL:\n{material_json}\n\nDRAFT:\n{draft}\n\nFIXES:\n{json.dumps(fixes, indent=2)}",
            )
            draft = response.content.strip() or draft
        return draft

    async def _surface_pass(self, draft: str, artifacts: MultipassArtifacts) -> str:
        def assess(text: str) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
            metrics = measure(text)
            review = heuristic_review(text, asset_set_id="multipass", platform="blog", content_format="blog", source_path="content.md")
            blocking = hard_flags(metrics) + [
                rec for flag, rec in zip(review["flags"], review["rewrite_recommendations"]) if flag in SURFACE_FIX_FLAGS
            ]
            return metrics, blocking, review

        metrics, blocking, review = assess(draft)
        if blocking:
            self._log(f"  [surface] fixing: {'; '.join(blocking)}")
            response = await self._call(
                "surface_fix", self._provider("restructurer"), prompts.SURFACE_SYSTEM,
                f"DRAFT:\n{draft}\n\nPATTERNS TO FIX:\n" + "\n".join(f"- {b}" for b in blocking),
            )
            draft = response.content.strip() or draft
            metrics, remaining, review = assess(draft)
        else:
            remaining = []
        artifacts.surface = {"metrics": metrics, "fixed": blocking, "remaining": remaining, "de_ai": review}
        return draft

    async def _fit_pass(self, draft: str, artifacts: MultipassArtifacts) -> None:
        extracted = await self._call_json(
            "claims", self._provider("auditor"), prompts.CLAIMS_SYSTEM, f"PIECE:\n\n{draft}", required=["claims"]
        )
        claims = [c for c in extracted.get("claims", []) if isinstance(c, dict) and c.get("text")]
        checks = await asyncio.gather(*(verify_citation(c.get("citation")) for c in claims))
        artifacts.claims = [{**c, "verification": v} for c, v in zip(claims, checks)]
        artifacts.gaps = find_gaps(draft)
        unverified = sum(1 for c in artifacts.claims if c["verification"]["status"] != "verified")
        self._log(f"  [fit] {len(claims)} claims ({unverified} without a verified citation), {len(artifacts.gaps)} gaps")

    async def run(
        self,
        task_prompt: str,
        output_dir: Path | None = None,
        *,
        verified_claims: list[dict[str, Any]] | None = None,
        ledger_dir: Path | None = None,
    ) -> tuple[PipelineResult, MultipassArtifacts]:
        """Run all stages. `ledger_dir` holds the variety ledger shared across runs (default: output_dir's parent)."""
        output_dir = output_dir or self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = MultipassArtifacts()
        planner, author = self._provider("planner"), self._provider("author")

        self._log("\n[1/6 Material] Gathering sources and specifics...")
        papers = await self._gather_papers(planner, task_prompt)
        artifacts.papers = [p.as_dict() for p in papers]
        artifacts.material = await self._call_json(
            "material", planner, prompts.MATERIAL_SYSTEM,
            f"BRIEF:\n{task_prompt}\n\nPAPERS:\n{_papers_block(papers)}\n\nVERIFIED CLAIMS:\n{_claims_block(verified_claims)}",
            required=["material"],
        )
        material_json = json.dumps(artifacts.material, indent=2)

        self._log("\n[2/6 Plan] Making structural decisions...")
        ledger = ledger_path(ledger_dir or output_dir.parent)
        artifacts.plan = await self._call_json(
            "plan", planner, prompts.PLAN_SYSTEM,
            f"BRIEF:\n{task_prompt}\n\nMATERIAL:\n{material_json}\n\nRECENT PIECES:\n{describe_recent(read_recent(ledger))}",
            required=["decisions", "units"],
        )

        self._log("\n[3/6 Draft] Writing from the plan...")
        draft_resp = await self._call(
            "draft", author, prompts.DRAFT_SYSTEM,
            f"BRIEF:\n{task_prompt}\n\nMATERIAL:\n{material_json}\n\nPLAN:\n{json.dumps(artifacts.plan, indent=2)}",
            max_tokens=12000,
        )
        draft = draft_resp.content.strip()

        self._log("\n[4/6 Structure] Auditing shape...")
        draft = await self._structure_pass(task_prompt, material_json, draft, artifacts)

        self._log("\n[5/6 Surface] Measuring...")
        draft = await self._surface_pass(draft, artifacts)

        self._log("\n[6/6 Fit] Extracting and verifying claims...")
        await self._fit_pass(draft, artifacts)

        images = []
        if self.images:
            from cmsbot.pipeline import build_image_director

            self._log("\n[Images] Planning and generating...")
            images, img_responses = await build_image_director(self.config, self.budget).generate_images(
                task_prompt, draft, ContentType.BLOG, output_dir, log_fn=self._log
            )
            for ir in img_responses:
                self.budget.record("image_director", ir.provider, ir.model, ir.input_tokens, ir.output_tokens)

        html = Renderer().render(content=draft, images=images, content_type=ContentType.BLOG, task_prompt=task_prompt)
        self._write(output_dir, draft, html, artifacts)
        title_match = _TITLE.search(draft)
        append_entry(ledger, title=title_match.group(1).strip() if title_match else "", plan=artifacts.plan)

        last_audit = artifacts.structural_audits[-1] if artifacts.structural_audits else {}
        result = PipelineResult(
            task_prompt=task_prompt,
            content_type=ContentType.BLOG,
            final_draft=Draft(content=draft, round_num=len(artifacts.structural_audits), origin="multipass",
                              provider=draft_resp.provider, model=draft_resp.model),
            rounds=[],
            images=images,
            html=html,
            total_cost=self.budget.spent,
            total_rounds=len(artifacts.structural_audits),
            converged=not remaining_structural_issues(last_audit) and not artifacts.surface.get("remaining"),
        )
        self._log(f"\nMULTIPASS COMPLETE | cost ${self.budget.spent:.4f} | gaps {len(artifacts.gaps)}")
        return result, artifacts

    @staticmethod
    def _write(output_dir: Path, draft: str, html: str, artifacts: MultipassArtifacts) -> None:
        def dump(name: str, payload: Any) -> None:
            (output_dir / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        (output_dir / "content.md").write_text(draft + "\n", encoding="utf-8")
        (output_dir / "index.html").write_text(html, encoding="utf-8")
        dump("material.json", {**artifacts.material, "papers": artifacts.papers})
        dump("plan.json", artifacts.plan)
        dump("claims.json", {"claims": artifacts.claims})
        dump("gaps.json", {"gaps": artifacts.gaps})
        dump("audit.json", {"structural": artifacts.structural_audits, "surface": artifacts.surface})
