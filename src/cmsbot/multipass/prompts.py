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
- Write full sentences of varied length. Do not build rhythm out of fragments ("In whom. At what dose.").
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
true means the AI-leaning pattern is present. Give a fix for every true feature.
"keep" is allowed only for mostly_mechanism_units, parallel_equal_units and
no_specific_dates, and only when the material makes it the right choice (explain
why in the instruction). Never "keep" thesis_before_first_section,
conclusion_restates_or_reframes or has_summary_stage.

Reader actions are wanted, not a problem: a specific next step, a protocol to try,
or a link placed where the reader needs it counts as reader participation. Never
ask to remove them, and never ask to remove citations or [bracketed] gaps. An ending
that is a concrete next step or an open question is not a summary stage.
Fixes are structural: cut, move or replace units. Rewording sentences does not count."""

RESTRUCTURE_SYSTEM = """\
Apply structural fixes to a draft. You get the brief, the material, the draft
and a list of fixes. Make each fix by cutting, moving or replacing units:
- Opening announces the thesis or sections: delete it; the piece usually opens fine at the next paragraph.
- Ending restates or summarizes: delete it, then make sure the new last unit is a real ending.
- Old-way-versus-new-way section: replace with what actually changed and when, or cut.
- Every section explains a mechanism: convert one into a case, a data point or an objection with its answer.
- No outside voice: add one from the material, or a [bracket] where one belongs.
- Parallel sections of equal length: merge, cut or expand by what the material warrants.
Keep citations, [brackets] and reader actions (next steps, protocols, links) intact.
Use only facts from the material. Write in full sentences with varied length;
do not answer a fix by chopping text into fragments.
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


# ---- Light run: derivatives of an anchor piece (`cmsbot derive`) -------------

_DERIVE_RULES = """\
Rules for every derivative:
- State only facts from the CLAIM LEDGER. Tag each item with the ledger ids it states ("claim_ids").
  Every number, dose, duration and study finding must come from a ledger claim, worded so it still
  says what the claim says. If a point needs a fact the ledger lacks, leave a visible [bracket]
  describing what is missing instead of supplying it.
- Never invent anecdotes, testimonials, quotes, people, or first-person experiences ("I tried",
  "my clients"). The account speaks as a team that reads the research, not as a user.
- Structure/function language only: no claims that anything treats, cures or prevents a disease.
- No roadmap openers ("in this thread", "here's what"), no summary or restated thesis at the end,
  no "it's not X, it's Y" frames, no "whether you're..." reader flattery. Follow the style rules in
  the brief (they may ban em dashes).
- Successive items in one batch must differ in opening move and in which claims carry them."""

NEWSLETTER_SYSTEM = f"""\
You write the weekly research newsletter from an anchor article and its claim ledger.
Return ONLY a JSON object:
{{"items": [{{
  "subject": "under 60 characters, specific, no clickbait or ALL CAPS",
  "preheader": "under 110 characters, adds to the subject instead of repeating it",
  "title": "headline at the top of the email",
  "body_markdown": "the email body in markdown",
  "claim_ids": ["L1", "L3"]
}}]}}

The reader opted in for research updates and already supplements. 350-600 words.
- Open on the most useful finding from the ledger, with its number and source, not on a greeting
  about the week.
- Cover the anchor's research in your own order; do not paraphrase the article top to bottom.
- If a FOCUS AREA is given, connect the finding to it in one short section, using only ledger facts.
- Give the reader one thing to do or track this week.
- Link to the full article exactly once with a markdown link whose URL is {{{{anchor_url}}}}
  (keep those braces literally). Cite studies inline as markdown links to their URL.
- No sign-off cliches ("Stay healthy!"). End on the action or the open question.

{_DERIVE_RULES}"""

THREAD_SYSTEM = f"""\
You write X (Twitter) threads from an anchor article and its claim ledger.
Return ONLY a JSON object:
{{"items": [{{
  "angle": "one line: what this thread is about, for the reviewer",
  "posts": ["post 1", "post 2"],
  "claim_ids": ["L2"]
}}]}}

Write the number of threads requested, each a standalone thread on a different angle:
a single study read closely, a reader objection with its answer, a common belief the ledger
contradicts, a how-to-track protocol, a limitation people skip. 3-7 posts per thread.
- Each post is under 270 characters, no hashtags, no emoji bullets, no "1/" numbering.
- The first post carries the specific finding or question; it is not a teaser.
- Put each study's URL once, in the first post that uses it (counts as ~23 characters); later posts
  about the same study don't repeat it.
- At most one thread in the batch may mention the product, plainly and once.

{_DERIVE_RULES}"""

VIDEO_SCRIPT_SYSTEM = f"""\
You write short-form video scripts (TikTok/Reels/Shorts) from an anchor article and its claim
ledger. A team member records the voiceover; visuals are b-roll, the paper itself on screen,
app screen recordings and template-rendered text. Return ONLY a JSON object:
{{"items": [{{
  "title": "working title for the reviewer",
  "format": "study receipt | form-vs-form | marker explainer | myth check",
  "hook": "the first spoken line, under 2 seconds, states the finding or the question",
  "on_screen_source": "the citation shown on screen (journal, year, PMID)",
  "beats": [{{"narration": "spoken words", "visual": "what is on screen", "overlay_text": "max 8 words"}}],
  "ending": "the last spoken line: a consequence, an open question or one next step, never a recap",
  "caption": "post caption, under 150 characters, searchable words that are actually spoken",
  "target_duration_seconds": 45,
  "claim_ids": ["L4"]
}}]}}

Write the number of scripts requested. Each video carries ONE primary ledger claim (first in
claim_ids) and at most two supporting ones; use a different primary claim per video.
- 3-5 beats, 30-75 seconds of narration at about 2.5 words per second. Vary length across the batch.
- The hook is spoken first and the ending last; beats carry the middle and never repeat either line.
- Narration is written to be spoken by a person: short clauses, no parentheses, no citations read aloud.
- Every number, dose and unit spoken also appears in overlay_text, so it can be template-rendered.
- No realistic AI people or AI presenters in "visual"; no weight-loss framing.

{_DERIVE_RULES}"""

DERIVE_FIX_SYSTEM = """\
Fix the listed problems in one derivative item. You get the claim ledger, the item as JSON and the
problems. Unsupported figures: remove the sentence or restate it using the ledger claim it was
meant to reflect; never keep a figure the ledger lacks. Invented anecdotes or testimonials: cut
them. Surface patterns: replace with a plain statement, not a synonym in the same frame.
Change only what the problems require. Return ONLY the corrected item as a JSON object with the
same keys."""
