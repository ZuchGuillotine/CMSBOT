"""Structural and surface measurements for a draft (multipass Pass 5).

Vendored from the multipass-writing skill's shape_check.py and made importable.
It reports numbers for judgment, not a grade: the pipeline shows them to the
reviewer and only acts on a few hard patterns (see `hard_flags`).

CLI: python -m cmsbot.multipass.shape_check draft.md
"""

from __future__ import annotations

import re
import statistics as st
import sys
from typing import Any

PATTERNS = {
    "roadmap/announce": r"\b(in this (post|article|guide|piece|essay)|we'?ll (cover|explore|look at|walk through|break down)|let'?s (dive|explore|take a look|break)|here'?s (what|how|why) |below,? (we|you'?ll))",
    "restate/close": r"\b(in (short|summary|conclusion|sum)|to (sum up|summarize|wrap up)|ultimately,|the bottom line|at the end of the day|the takeaway|key takeaways?)\b",
    "legacy-vs-modern": r"\b(traditional(ly)?|legacy|outdated|old way|modern|next[- ]gen(eration)?|today'?s (fast|rapid)|in the (digital|modern) (age|era)|era of|slower era|evolv(ing|ed) landscape|game[- ]chang)",
    "not-X-but-Y": r"(\bnot (just|only|merely|simply)\b|\bisn'?t (just|about)\b|\bmore than just\b|\bit'?s not\b[^.]{0,60}\bit'?s\b)",
    "signpost phrases": r"\b(it'?s worth noting|importantly,|notably,|crucially,|that said,|the reality is|here'?s the thing|the truth is|make no mistake|the result\?|the answer\?|enter\b)",
    "llm-favored words": r"\b(delve|tapestry|landscape|navigat\w+|leverag\w+|robust|seamless(ly)?|crucial|pivotal|underscor\w+|intricate|realm|foster\w*|unlock\w*|elevat\w+|streamlin\w+|empower\w*|holistic|myriad|bustling|testament|showcas\w+|paramount|nuanced|journey|resonat\w+|harness\w*|transformative|ever[- ]evolving|cutting[- ]edge)\b",
    "reader-flattery/imagine": r"\b(whether you'?re|imagine (a|you|if)|if you'?ve ever|we'?ve all been there)\b",
}

_STOP = set("the a an and or of to in on for with is are was were be it this that as at by from you your we our they their i".split())

# Patterns that block the draft until fixed (the rest are shown to the reviewer)
HARD_PATTERNS = ("roadmap/announce", "restate/close", "reader-flattery/imagine")


def _sentences(text: str) -> list[str]:
    text = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", text, flags=re.M)
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.split()) > 1]


def _cv(values: list[int]) -> float:
    return round(st.pstdev(values) / st.mean(values), 2) if len(values) > 1 and st.mean(values) else 0.0


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)} - _STOP


def measure(text: str) -> dict[str, Any]:
    """Return the shape metrics for a markdown draft."""
    lines = text.splitlines()
    headings = sum(1 for line in lines if re.match(r"^\s*#{1,6}\s", line))
    bullets = sum(1 for line in lines if re.match(r"^\s*([-*+]|\d+[.)])\s", line))
    body = "\n".join(line for line in lines if not re.match(r"^\s*#{1,6}\s", line))
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    words = re.findall(r"[A-Za-z0-9'’]+", body)
    word_count = max(len(words), 1)

    sentence_lengths = [len(s.split()) for s in _sentences(body)]
    para_lengths = [len(p.split()) for p in paras]

    def last_sentence_len(paragraph: str) -> int:
        ss = _sentences(paragraph)
        return len(ss[-1].split()) if ss else 0

    long_paras = [p for p in paras if len(p.split()) > 25]
    punch = sum(1 for p in long_paras if last_sentence_len(p) <= 7)

    hits: dict[str, list[str]] = {}
    for name, pattern in PATTERNS.items():
        found = re.findall(pattern, body, flags=re.I)
        if found:
            hits[name] = sorted({(m if isinstance(m, str) else m[0]).lower().strip() for m in found})

    triads = re.findall(r"[\w'-]+(?: [\w'-]+){0,3}, [\w'-]+(?: [\w'-]+){0,3},? (?:and|or) [\w'-]+", body)
    em_dashes = body.count("—") + body.count(" -- ")
    years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", body)
    months = re.findall(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b", body
    )
    quotes = re.findall(r"[\"“][^\"”]{12,}[\"”]", body)
    attributions = re.findall(r"\b(said|says|told|according to|wrote|reported)\b", body, flags=re.I)

    overlap = 0.0
    if len(paras) > 2:
        first, last = _content_words(paras[0]), _content_words(paras[-1])
        overlap = round(len(first & last) / max(len(first | last), 1), 2)

    per_k = lambda n: round(n * 1000 / word_count, 1)  # noqa: E731
    return {
        "words": len(words),
        "paragraphs": len(paras),
        "sentences": len(sentence_lengths),
        "headings": headings,
        "list_items": bullets,
        "sentence_length": {
            "mean": round(st.mean(sentence_lengths), 1) if sentence_lengths else 0,
            "cv": _cv(sentence_lengths),
            "share_12_25": round(sum(12 <= x <= 25 for x in sentence_lengths) / len(sentence_lengths), 2)
            if sentence_lengths else 0,
        },
        "paragraph_length": {"mean": round(st.mean(para_lengths)) if para_lengths else 0, "cv": _cv(para_lengths)},
        "punchline_closers": {"count": punch, "of": len(long_paras)},
        "em_dashes_per_k": per_k(em_dashes),
        "triads_per_k": per_k(len(triads)),
        "dates": {"years": len(years), "months": len(months)},
        "quotations": len(quotes),
        "attributions": len(attributions),
        "first_last_overlap": overlap,
        "pattern_hits": hits,
    }


def hard_flags(metrics: dict[str, Any]) -> list[str]:
    """Patterns that must be fixed before review, as human-readable reasons."""
    flags = [f"{name}: {', '.join(metrics['pattern_hits'][name][:5])}" for name in HARD_PATTERNS if name in metrics["pattern_hits"]]
    if metrics["first_last_overlap"] >= 0.35:
        flags.append(f"closing paragraph restates the opening (overlap {metrics['first_last_overlap']})")
    return flags


def format_report(metrics: dict[str, Any]) -> str:
    sl, pl = metrics["sentence_length"], metrics["paragraph_length"]
    lines = [
        f"words {metrics['words']} | paragraphs {metrics['paragraphs']} | sentences {metrics['sentences']} "
        f"| headings {metrics['headings']} | list items {metrics['list_items']}",
        f"sentence length: mean {sl['mean']}, CV {sl['cv']}, share 12-25 words {sl['share_12_25']:.0%}",
        f"paragraph length: mean {pl['mean']}, CV {pl['cv']}",
        f"paragraphs (>25w) ending on a <=7-word sentence: {metrics['punchline_closers']['count']}/{metrics['punchline_closers']['of']}",
        f"em dashes {metrics['em_dashes_per_k']}/1k | 'X, Y, and Z' lists {metrics['triads_per_k']}/1k",
        f"dates: years {metrics['dates']['years']}, months {metrics['dates']['months']} | quotations {metrics['quotations']} "
        f"| attributions {metrics['attributions']}",
        f"first/last paragraph content-word overlap (Jaccard): {metrics['first_last_overlap']}",
    ]
    lines += [f"{name}: {', '.join(values[:12])}" for name, values in metrics["pattern_hits"].items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Usage: python -m cmsbot.multipass.shape_check draft.md", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        print(format_report(measure(handle.read())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
