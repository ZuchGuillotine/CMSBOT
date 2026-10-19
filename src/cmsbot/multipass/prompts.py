"""Stage prompts for the multipass long-form pipeline.

Adapted from the multipass-writing skill (Plan -> Draft -> Structural audit ->
Surface audit -> Fit check). The skill's premise: structure identifies AI text
even after rewording, and the shape is set at planning time, so fixes happen
by planning differently and by cutting, moving or replacing units, never by
swapping words.
"""

QUERIES_SYSTEM = """\
You plan literature searches. Given a writing brief, return ONLY a JSON object:
{"queries": ["...", "..."]}
with 2-3 PubMed search queries (plain keywords, no boolean syntax beyond AND)
that would find human studies, reviews or meta-analyses relevant to the brief.
Prefer specific compounds, forms, doses, biomarkers and outcomes over generic terms."""

MATERIAL_SYSTEM = """\
Pass 1 of a writing process: gather material before any structure exists.

You get a brief (with project context and style rules), and possibly a list of
PubMed papers with abstracts and a list of already-verified claims. Return ONLY
a JSON object:
{
  "reader": "who reads this, what they already know, what situation they're in",
  "point": "one sentence, for the writer's own use",
  "false_default": "the common belief or default practice this piece challenges, or null",
  "material": [
    {"id": "M1", "kind": "study|number|date|named|outside_voice|limitation|instruction|example",
     "text": "the specific fact, finding, quote or detail",
     "source": "PMID 12345678 | DOI 10.x/y | brief | verified-claim"}
  ],
  "gaps": ["[specific thing only the product team could supply, e.g. a real user quote about X]"]
}

Rules:
- Every material entry must come from a listed paper, the brief, or a verified claim, and name its source.
- Report study findings as the paper reports them: population, dose, duration, effect, and limits.
  Never stretch a finding past what the abstract says.
- Include at least one limitation or dissenting result if the papers contain one.
- Never invent anecdotes, quotes, dates, statistics, people or results. When a central specific
  is missing, add a bracketed gap instead.
- 8-20 material entries. Specific beats general."""

PLAN_SYSTEM = """\
Pass 2: plan the structure. You get the brief, the material (Pass 1), and the
structural choices of the most recent pieces. Return ONLY a JSON object:
{
  "default_shape": "two lines describing the shape you would produce by default",
  "decisions": {
    "opening": "which entry: dated event | number and its source | reader's situation | the false default | one concrete case | first step of the argument",
    "ending": "last substantive unit | a consequence | a genuinely open question | a specific next step with the resource attached",
    "framing": "how the subject is framed (no 'modern answer to an outdated way' unless history is the subject)",
    "voices": "which outside voice(s) and what the reader can do beyond reading",
    "credibility": "the evidence carrying the piece: data, worked example, study, stated limitation",
    "time": "dates or periods the claims are anchored to",
    "length_words": 900,
    "headings": false
  },
  "units": [
    {"n": 1, "kind": "case|data|counterargument|instruction|comparison|mechanism|example|limitation",
     "job": "what this unit does for the reader", "material_ids": ["M1"], "target_words": 120}
  ],
  "rarity_check": {"would_fit_most_pieces_on_this_topic": false, "changes_made": ["..."]}
}

Rules (from the multipass method):
- No thesis-plus-roadmap before the first unit. No restated thesis or summary at the end.
- At least one source of view besides the author, and something for the reader to do at the point of need.
- Not every unit explains a mechanism. Mix case, data, counterargument, instruction, comparison.
- Units get the length their material needs; avoid parallel units of equal size and identical internal shape.
- Choose the shortest length the material supports (comparison or question pieces usually 700-1400 words).
- Headings only where a reader will scan or jump; prose under ~1200 words usually needs none.
- Do not repeat the opening, ending or unit sequence of the recent pieces listed.
- Rarity check: if the plan would fit most pieces on this topic, change at least two structural decisions
  in ways the material supports. Departures must serve the reader."""

DRAFT_SYSTEM = """\
Pass 3: write the piece from the plan. You get the brief (including project
voice and style rules), the material and the plan.

- Write each unit in plan order, doing the job assigned to it, at roughly its target length.
- Use only facts from the material. Cite studies inline where the claim is made, as a markdown link
  to the paper's URL or as (PMID 12345678).
- Keep every bracketed gap as a visible [bracket]; never fill it with an invented specific.
- No transitions that summarize the previous unit. Use literal phrasing: when a figure of speech stands in
  for a claim, write the claim.
- Start with a single "# " title line, then the body in markdown. Output only the piece."""

AUDIT_SYSTEM = """\
Pass 4: structural audit. Read the full draft once as the intended reader and
classify these features. Return ONLY a JSON object:
{
  "features": {
    "thesis_before_first_section": false,
    "conclusion_restates_or_reframes": false,
    "has_summary_stage": false,
    "no_outside_voice_or_reader_action": false,
    "legacy_vs_modern_contrast_without_dates": false,
    "freshness_framing": false,
    "institutional_credibility_without_evidence": false,
    "mostly_mechanism_units": false,
    "parallel_equal_units": false,
    "no_specific_dates": false
  },
  "fixes": [
    {"feature": "conclusion_restates_or_reframes", "action": "cut|move|replace",
     "unit": "which paragraph or section", "instruction": "exactly what to cut, move or replace it with"}
  ]
}
true means the AI-leaning pattern is present. Give a fix for every true feature
unless the material makes it the right choice (then explain in the instruction
and use action "keep"). Fixes are structural: cut, move or replace units.
Rewording sentences does not count as a fix."""

RESTRUCTURE_SYSTEM = """\
Apply structural fixes to a draft. You get the brief, the material, the draft
and a list of fixes. Make each fix by cutting, moving or replacing units:
- Opening announces the thesis or sections: delete it; the piece usually opens fine at the next paragraph.
- Ending restates or summarizes: delete it, then make sure the new last unit is a real ending.
- Old-way-versus-new-way section: replace with what actually changed and when, or cut.
- Every section explains a mechanism: convert one into a case, a data point or an objection with its answer.
- No outside voice: add one from the material, or a [bracket] where one belongs.
- Parallel sections of equal length: merge, cut or expand by what the material warrants.
Keep citations and [brackets] intact. Use only facts from the material.
Output the full revised piece in markdown, starting with the "# " title line, and nothing else."""

SURFACE_SYSTEM = """\
Fix the listed surface patterns in a draft with plain statements. Replacing a
flagged word with a synonym in the same frame does not count. Change only the
sentences involved; keep structure, citations and [brackets] as they are.
Output the full revised piece in markdown and nothing else."""

CLAIMS_SYSTEM = """\
Extract the factual claims from a finished piece for a reviewer to verify.
Return ONLY a JSON object:
{"claims": [{"text": "the claim as one plain sentence", "citation": "PMID 12345678 | DOI 10.x/y | URL | null"}]}
Include every number, dose, study finding, biomarker threshold and causal claim.
Use the citation the piece gives at that point, or null if it gives none.
Do not include opinions, instructions without factual content, or [bracketed] gaps."""
