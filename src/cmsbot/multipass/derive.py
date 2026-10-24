"""Light multipass run for derivatives (`cmsbot derive`): newsletter, threads, video scripts.

Derivatives restate claims from the anchor's claim ledger instead of making new ones, so a
reviewer checks facts once on the anchor and only tone and format here. Stages:
  1 ledger   - verified claims from the anchor, numbered L1..Ln, plus the anchor text
  2 draft    - one call per kind; every item lists the ledger ids it states
  3 gates    - ledger (no figure outside the ledger), testimonials, surface patterns; one fix pass
  4 fit      - unresolved [gaps] and remaining gate results recorded for the reviewer
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cmsbot.budget import BudgetTracker
from cmsbot.config import Config
from cmsbot.de_ai import heuristic_review
from cmsbot.multipass import prompts
from cmsbot.multipass.pipeline import find_gaps
from cmsbot.multipass.shape_check import hard_flags, measure
from cmsbot.providers import build_provider
from cmsbot.renderer import _markdown_to_html
from cmsbot.repurpose import parse_json_object

ANCHOR_URL_TOKEN = "{{anchor_url}}"
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?")
_MD_URL = re.compile(r"\]\([^)]*\)|https?://\S+")
_TESTIMONIAL = re.compile(
    r"\b(I|we) (tried|took|started|switched|noticed|felt|swear)\b"
    r"|\bmy (client|patient|friend|wife|husband|partner|doctor)s?\b"
    r"|\b(a|one) (client|patient|reader|user) (told|said|wrote)\b",
    re.IGNORECASE,
)
# Heuristic flags worth one automatic fix (the rest go to the reviewer)
SURFACE_FIX_FLAGS = {"generic phrasing", "contrast frames", "disease claim", "CTA awkwardness", "inflated claims"}


@dataclass(frozen=True)
class DerivativeKind:
    name: str
    system: str
    platform: str  # de_ai platform for length and tone checks
    default_count: int
    distribution_dir: str


KINDS: dict[str, DerivativeKind] = {
    "newsletter": DerivativeKind("newsletter", prompts.NEWSLETTER_SYSTEM, "email", 1, "email"),
    "thread": DerivativeKind("thread", prompts.THREAD_SYSTEM, "x", 5, "x"),
    "video_script": DerivativeKind("video_script", prompts.VIDEO_SCRIPT_SYSTEM, "tiktok", 3, "tiktok"),
}


class DeriveError(RuntimeError):
    """The anchor can't support derivatives (missing content or no verified claims)."""


def _is_verified(claim: dict[str, Any]) -> bool:
    verification = claim.get("verification")
    if isinstance(verification, dict) and verification.get("status") == "verified":
        return True
    # Claim rows exported from a content queue carry an admin-set status instead
    return claim.get("status") == "verified"


def load_ledger(claims: list[dict[str, Any]], *, include_unverified: bool = False) -> list[dict[str, Any]]:
    """Number the usable claims L1..Ln. Unverified claims are kept only on request and only with a citation."""
    ledger = []
    for claim in claims:
        if not isinstance(claim, dict) or not claim.get("text"):
            continue
        if not (_is_verified(claim) or (include_unverified and claim.get("citation"))):
            continue
        verification = claim.get("verification") if isinstance(claim.get("verification"), dict) else {}
        ledger.append({
            "id": f"L{len(ledger) + 1}",
            "text": str(claim["text"]).strip(),
            "citation": claim.get("citation"),
            "url": verification.get("url") or claim.get("citationUrl") or claim.get("url"),
        })
    return ledger


def ledger_block(ledger: list[dict[str, Any]]) -> str:
    return "\n".join(f"{c['id']}: {c['text']} ({c['citation'] or 'no citation'}{', ' + c['url'] if c['url'] else ''})" for c in ledger)


def item_text(kind: str, item: dict[str, Any]) -> str:
    """The publishable copy of an item, for gates and measurements."""
    if kind == "newsletter":
        return "\n\n".join(str(item.get(k) or "") for k in ("subject", "preheader", "title", "body_markdown"))
    if kind == "thread":
        return "\n\n".join(str(p) for p in item.get("posts") or [])
    beats = item.get("beats") or []
    spoken = [str(item.get("hook") or "")]
    spoken += [f"{b.get('narration', '')}\n{b.get('overlay_text', '')}" for b in beats if isinstance(b, dict)]
    spoken += [str(item.get("ending") or ""), str(item.get("caption") or "")]
    return "\n\n".join(spoken)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(_MD_URL.sub(" ", text))}


def ledger_violations(text: str, ledger: list[dict[str, Any]], claim_ids: list[Any], *, allowed_text: str = "") -> list[str]:
    """Figures not found in any ledger claim, and claim ids that don't exist."""
    known_ids = {c["id"] for c in ledger}
    allowed = _numbers(" ".join(f"{c['text']} {c['citation'] or ''}" for c in ledger) + " " + allowed_text)
    problems = [f"unknown claim id {cid}" for cid in claim_ids if cid not in known_ids]
    if not claim_ids:
        problems.append("item lists no claim_ids")
    for number in sorted(_numbers(text) - allowed, key=lambda n: (len(n), n)):
        problems.append(f"figure '{number}' is not in the claim ledger")
    return problems


def assess(kind: DerivativeKind, item: dict[str, Any], ledger: list[dict[str, Any]], *, allowed_text: str = "") -> dict[str, Any]:
    text = item_text(kind.name, item)
    metrics = measure(text)
    review = heuristic_review(text, asset_set_id="derive", platform=kind.platform, content_format=kind.name, source_path=kind.name)
    surface = hard_flags(metrics) + [
        rec for flag, rec in zip(review["flags"], review["rewrite_recommendations"]) if flag in SURFACE_FIX_FLAGS
    ]
    testimonials = sorted({m.group(0) for m in _TESTIMONIAL.finditer(text)})
    ids = item.get("claim_ids") if isinstance(item.get("claim_ids"), list) else []
    gate = {
        "ledger": ledger_violations(text, ledger, ids, allowed_text=allowed_text),
        "testimonials": [f"possible invented anecdote: '{t}'" for t in testimonials],
        "surface": surface,
    }
    return {"gates": gate, "blocking": gate["ledger"] + gate["testimonials"] + gate["surface"],
            "de_ai": {"score": review["score"], "flags": review["flags"]}, "metrics": metrics}


class DerivePipeline:
    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self.budget = BudgetTracker(cap=self.config.budget_cap)
        self._log: Callable[[str], None] = lambda msg: None

    def set_logger(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    async def _json(self, stage: str, role: str, system: str, user: str, required: list[str]) -> dict[str, Any]:
        provider = build_provider(self.config.provider_for(role), self.config)
        for attempt in range(2):
            prompt = user if attempt == 0 else f"{user}\n\nReturn ONLY the JSON object, nothing else."
            response = await provider.generate_simple(user_prompt=prompt, system_prompt=system, max_tokens=10000)
            self.budget.record(stage, response.provider, response.model, response.input_tokens, response.output_tokens)
            self._log(f"  [{stage}] {response.model} | {response.input_tokens}+{response.output_tokens} tokens | ${response.cost:.4f}")
            try:
                return parse_json_object(response.content, required_keys=required)
            except ValueError:
                if attempt:
                    raise
        raise AssertionError("unreachable")

    async def run(
        self,
        kind_name: str,
        *,
        brief: str,
        anchor_title: str,
        anchor_text: str,
        ledger: list[dict[str, Any]],
        count: int | None = None,
        focus: str | None = None,
        anchor_url: str = ANCHOR_URL_TOKEN,
    ) -> dict[str, Any]:
        kind = KINDS[kind_name]
        if not ledger:
            raise DeriveError("The anchor has no verified claims; derivatives can only restate verified claims.")
        count = count or kind.default_count
        context = (
            f"BRIEF:\n{brief}\n\nCLAIM LEDGER:\n{ledger_block(ledger)}\n\n"
            f"ANCHOR ARTICLE ({anchor_title}):\n{anchor_text}"
        )
        request = f"{context}\n\nWrite {count} item(s)."
        if focus:
            request += f"\n\nFOCUS AREA: {focus}"

        self._log(f"\n[Draft] {count} {kind.name} item(s) from {len(ledger)} ledger claims...")
        drafted = await self._json(kind.name, "drafter", kind.system, request, required=["items"])
        items = [i for i in drafted.get("items", []) if isinstance(i, dict)][:count]

        # Figures may come from the ledger or the focus brief the editor wrote; the anchor's
        # uncited figures don't count, since derivatives restate verified claims only.
        allowed_text = focus or ""
        results = []
        for n, item in enumerate(items, 1):
            check = assess(kind, item, ledger, allowed_text=allowed_text)
            fixed = []
            if check["blocking"]:
                self._log(f"  [gates] item {n}: {'; '.join(check['blocking'][:6])}")
                try:
                    revised = await self._json(
                        f"{kind.name}_fix", "restructurer", prompts.DERIVE_FIX_SYSTEM,
                        f"CLAIM LEDGER:\n{ledger_block(ledger)}\n\nITEM:\n{json.dumps(item, indent=2)}\n\n"
                        "PROBLEMS:\n" + "\n".join(f"- {p}" for p in check["blocking"]),
                        required=[],
                    )
                    if revised:
                        fixed, item = check["blocking"], {**item, **revised}
                        check = assess(kind, item, ledger, allowed_text=allowed_text)
                except ValueError as exc:
                    self._log(f"  [gates] item {n}: fix pass returned bad JSON ({exc}); keeping the draft")
            if kind.name == "newsletter":
                item["body_markdown"] = str(item.get("body_markdown") or "").replace(ANCHOR_URL_TOKEN, anchor_url)
                item["body_html"] = _markdown_to_html(item["body_markdown"], [])
            item["gaps"] = find_gaps(item_text(kind.name, item))
            item["audit"] = {"fixed": fixed, "remaining": check["blocking"], "gates": check["gates"],
                             "de_ai": check["de_ai"], "metrics": check["metrics"]}
            results.append(item)

        blocked = sum(1 for i in results if i["audit"]["remaining"])
        self._log(f"\nDERIVE COMPLETE | {len(results)} {kind.name} | {blocked} with open gate issues | ${self.budget.spent:.4f}")
        return {
            "kind": kind.name,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "anchor": {"title": anchor_title},
            "focus": focus,
            "ledger": ledger,
            "items": results,
            "cost_usd": round(self.budget.spent, 4),
        }


def write_derivatives(asset_root: Path, payload: dict[str, Any]) -> Path:
    """Write <asset>/distribution/derivatives/<kind>.json (overwrites the previous batch of that kind)."""
    out_dir = asset_root / "distribution" / "derivatives"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{payload['kind']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
