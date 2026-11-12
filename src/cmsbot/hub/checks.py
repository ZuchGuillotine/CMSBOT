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
STOCK_PHRASES = (
    "in today's fast-paced world", "in today's world", "game-changer", "game changer", "unlock the power", "unlock your", "take it to the next level",
    "at the end of the day", "let's dive in", "dive into", "it's important to note", "in conclusion", "look no further", "the world of",
    "seamlessly", "revolutionize", "harness the power", "navigating the", "ever-evolving", "delve into", "when it comes to",
)
CONTRAST_FRAME = re.compile(r"[^.!?\n]*\b(?:it'?s|this is|that'?s|they'?re|you'?re) not (?:just |only |about )?[^.!?\n]{3,60}?[,;—–-] ?(?:it'?s|but|they'?re|you'?re)\b[^.!?\n]*[.!?]?", re.IGNORECASE)
ABSOLUTE = re.compile(r"\b(guaranteed?|always works|never fails|100% (?:safe|effective|accurate)|the best in the world|risk[- ]free|instantly|overnight success|proven to)\b", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{\{(?!link\}\})[^}]*\}\}|\[[^\]\n]{6,}\]")


def resolve_claim(ref: Any, claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A claim reference is its label ("L3") or its position (3) in the anchor's ledger, in the order the Hub lists them."""
    match = re.fullmatch(r"[Ll]?(\d+)", str(ref).strip())
    if not match:
        return None
    position = int(match.group(1))
    return claims[position - 1] if 1 <= position <= len(claims) else None


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
        listed = body.get("claim_ids") or body.get("claimIds") or []
        if claims and not listed:
            out.append({"level": "note", "check": "claims", "detail": "Lists no claim_ids, so the reviewer can't see which verified claims it rests on."})
        unknown = [str(ref) for ref in listed if resolve_claim(ref, claims) is None]
        if unknown:
            out.append({"level": "fix", "check": "claims", "detail": f"claim_ids that match no claim on the anchor: {', '.join(unknown[:8])}. "
                                                                     f"Use the labels from get_item on the anchor (L1 to L{len(claims)})."})

    if PLACEHOLDER.search(full):
        out.append({"level": "note", "check": "placeholders", "detail": "Contains a bracketed note or an unfilled placeholder."})

    if len(full.split()) >= 120:  # pattern measures are meaningless on a few lines
        try:
            from cmsbot.multipass.shape_check import hard_flags, measure

            for flag in hard_flags(measure(full)):
                out.append({"level": "note", "check": "structure", "detail": flag})
        except Exception:  # noqa: BLE001 - a measuring bug must never block a draft
            pass
    # Every note below quotes what it found, so the writer knows exactly what to change.
    stock = sorted({phrase for phrase in STOCK_PHRASES if phrase in lower})
    if stock:
        out.append({"level": "note", "check": "stock phrases", "detail": "Reads as filler: " + "; ".join(f'"{p}"' for p in stock[:6]) + "."})
    frames = [m.group(0).strip() for m in CONTRAST_FRAME.finditer(full)]
    if frames:
        out.append({"level": "note", "check": "contrast frames", "detail": "The \"not X, but Y\" shape: " + " / ".join(f'"{f[:90]}"' for f in frames[:3]) + "."})
    absolutes = sorted({m.group(0).lower() for m in ABSOLUTE.finditer(full)})
    if absolutes:
        out.append({"level": "note", "check": "absolute claims", "detail": "Check these can be backed up: " + ", ".join(absolutes[:8]) + "."})
    return out
