"""Writing checks that need no model: run on every draft, shown to the agent on submit and to the reviewer.

Drafting is done by whichever agent the project uses, following the writing skill. These checks are
the part that should not depend on that agent's judgement: platform limits, the brand's banned
words, invented experience, figures that are not in the verified claims, and the measurable
patterns that make text read as machine-written.

  fix   objective problems; the agent should correct them before submitting
  note  patterns worth a look; the reviewer decides
"""

from __future__ import annotations

import re
from typing import Any

SKIP_KEYS = {"claim_ids", "claimIds", "theme", "style", "type", "background", "format", "visual", "brief"}
URL = re.compile(r"https?://\S+")
TESTIMONIAL = re.compile(
    r"\b(I|we) (tried|took|started|switched|noticed|felt|swear)\b"
    r"|\bmy (client|patient|friend|wife|husband|partner|doctor)s?\b"
    r"|\b(a|one) (client|patient|reader|user|customer) (told|said|wrote)\b", re.IGNORECASE)
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*%?")
PLACEHOLDER = re.compile(r"\{\{(?!link\}\})[^}]*\}\}|\[[^\]\n]{6,}\]")


def texts(value: Any, key: str = "") -> list[str]:
    if key in SKIP_KEYS:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [t for v in value for t in texts(v, key)]
    if isinstance(value, dict):
        return [t for k, v in value.items() for t in texts(v, k)]
    return []


def post_length(text: str, channel: str) -> int:
    """X counts any link as 23 characters."""
    return len(URL.sub("x" * 23, text)) if channel == "x" else len(text)


def _figures(text: str) -> set[str]:
    out = set()
    for match in NUMBER.findall(URL.sub(" ", text)):
        digits = match.rstrip("%").replace(",", "")
        if match.endswith("%") or "." in digits or len(digits) >= 2:  # bare single digits are usually list counts
            out.add(match)
    return out


def run(item: dict[str, Any], claims: list[dict[str, Any]], brand: dict[str, Any], char_limit: int | None) -> list[dict[str, str]]:
    body = item["body"]
    parts = texts(body)
    if not parts:
        return []
    full = "\n\n".join(parts)
    lower = full.lower()
    out: list[dict[str, str]] = []

    if char_limit and isinstance(body.get("posts"), list):
        for number, post in enumerate(body["posts"], start=1):
            length = post_length(str(post), item["channel"])
            if length > char_limit:
                out.append({"level": "fix", "check": "length", "detail": f"Post {number} is {length} characters; {item['channel']} allows {char_limit}."})
    elif char_limit and isinstance(body.get("text"), str) and post_length(body["text"], item["channel"]) > char_limit:
        out.append({"level": "fix", "check": "length",
                    "detail": f"The post is {post_length(body['text'], item['channel'])} characters; {item['channel']} allows {char_limit}."})

    lexicon = brand.get("lexicon") if isinstance(brand.get("lexicon"), dict) else {}
    banned = [str(w) for w in (lexicon.get("avoid") or []) + (brand.get("banned_words") or []) if str(w).strip()]
    found = sorted({w for w in banned if re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", lower)})
    if found:
        out.append({"level": "fix", "check": "brand words", "detail": "Uses words the brand avoids: " + ", ".join(found[:8]) + "."})

    if TESTIMONIAL.search(full):
        out.append({"level": "fix", "check": "invented experience",
                    "detail": "Reads as a first-person result or a testimonial. Keep it only if a real person stands behind it."})

    if item.get("parent_id"):
        allowed = _figures(" ".join(f"{c['text']} {c['citation']}" for c in claims if c["status"] != "rejected"))
        extra = sorted(_figures(full) - allowed)
        if extra and claims:
            out.append({"level": "fix", "check": "figures", "detail": "Figures not found in the anchor's claims: " + ", ".join(extra[:8]) + "."})
        if claims and not (body.get("claim_ids") or body.get("claimIds")):
            out.append({"level": "note", "check": "claims", "detail": "Lists no claim_ids, so the reviewer can't see which verified claims it rests on."})

    if PLACEHOLDER.search(full):
        out.append({"level": "note", "check": "placeholders", "detail": "Contains a bracketed note or an unfilled placeholder."})

    if len(full.split()) >= 120:  # pattern measures are meaningless on a few lines
        try:
            from cmsbot.multipass.shape_check import hard_flags, measure

            metrics = measure(full)
            for flag in hard_flags(metrics):
                out.append({"level": "note", "check": "structure", "detail": flag})
        except Exception:  # noqa: BLE001 - a measuring bug must never block a draft
            pass
    try:
        from cmsbot.de_ai import heuristic_review

        review = heuristic_review(full, asset_set_id="", platform=item["channel"], content_format=item["kind"], source_path="")
        advice = review.get("rewrite_recommendations") or []
        for index, flag in enumerate(review.get("flags", [])):
            out.append({"level": "note", "check": flag, "detail": str(advice[index]) if index < len(advice) else ""})
    except Exception:  # noqa: BLE001
        pass
    return out
