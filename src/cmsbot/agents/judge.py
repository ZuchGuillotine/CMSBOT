"""Blind judge panel — 3 fresh agents, no labels, ranked choice + Borda count."""

from __future__ import annotations

import asyncio
import json
import re
import random

from cmsbot.models import Draft, Judgment, LLMResponse
from cmsbot.providers.base import LLMProvider

SYSTEM = """\
You are a content quality judge. You will evaluate three candidate versions of the same content.

Evaluate each candidate on these criteria:
1. **Clarity & Readability** — Is it easy to follow? Good flow?
2. **Depth & Substance** — Does it provide real insight and value?
3. **Engagement** — Does it hook and hold the reader?
4. **Structure** — Is it well-organized with smooth transitions?
5. **Originality** — Does it avoid clichés? Does it offer fresh perspective?
6. **Polish** — Is the writing tight, vivid, and error-free?

RULES:
- You see Candidate 1, Candidate 2, and Candidate 3. You do NOT know who wrote them.
- Rank ALL three from best to worst.
- Output ONLY a JSON array with your ranking, best first.
- Example: [2, 1, 3] means Candidate 2 is best, Candidate 1 is second, Candidate 3 is worst.
- Output ONLY the JSON array. No commentary, no explanation."""


class JudgePanel:
    """Three independent judges evaluate candidates blindly using Borda count."""

    def __init__(self, providers: list[LLMProvider]):
        if len(providers) != 3:
            raise ValueError("Judge panel requires exactly 3 providers")
        self.providers = providers

    async def evaluate(
        self,
        candidates: list[Draft],
        task_prompt: str,
    ) -> tuple[int, str, list[Judgment], list[LLMResponse]]:
        """
        Evaluate candidates blindly.

        Returns:
            (winner_index, winner_label, judgments, responses)
            winner_index: index into the ORIGINAL [A, B, AB] order
            winner_label: "A", "B", or "AB"
        """
        labels = ["A", "B", "AB"]

        # Shuffle candidates — judges see them in random order with no labels
        indices = list(range(3))
        random.shuffle(indices)
        shuffled = [candidates[i] for i in indices]

        # Build the evaluation prompt
        candidate_text = "\n\n---\n\n".join(
            f"## CANDIDATE {i + 1}\n\n{c.content}" for i, c in enumerate(shuffled)
        )
        user_prompt = f"""\
TASK: {task_prompt}

{candidate_text}

Rank these three candidates from best to worst. Output ONLY a JSON array like [1, 2, 3]."""

        # Run all 3 judges in parallel
        async def run_judge(judge_id: int, provider: LLMProvider) -> tuple[Judgment, LLMResponse]:
            response = await provider.generate_simple(
                user_prompt=user_prompt,
                system_prompt=SYSTEM,
                max_tokens=200,
                temperature=0.3,
            )
            ranking = _parse_ranking(response.content)
            judgment = Judgment(
                ranking=ranking,
                judge_id=judge_id,
                provider=response.provider,
                model=response.model,
            )
            return judgment, response

        results = await asyncio.gather(
            run_judge(0, self.providers[0]),
            run_judge(1, self.providers[1]),
            run_judge(2, self.providers[2]),
        )

        judgments = [r[0] for r in results]
        responses = [r[1] for r in results]

        # Borda count: 1st place = 2 pts, 2nd = 1 pt, 3rd = 0 pts
        scores = [0, 0, 0]  # scores for shuffled candidates 1, 2, 3
        for j in judgments:
            for rank_pos, candidate_num in enumerate(j.ranking):
                idx = candidate_num - 1  # Convert 1-indexed to 0-indexed
                if 0 <= idx < 3:
                    scores[idx] += 2 - rank_pos

        # Winner in shuffled order
        shuffled_winner = scores.index(max(scores))
        # Map back to original order
        original_winner = indices[shuffled_winner]
        winner_label = labels[original_winner]

        return original_winner, winner_label, judgments, responses


def _parse_ranking(text: str) -> list[int]:
    """Parse a JSON ranking array from potentially messy LLM output."""
    text = text.strip()

    # Try direct JSON parse
    try:
        result = json.loads(text)
        if isinstance(result, list) and len(result) == 3:
            return [int(x) for x in result]
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find a JSON array in the text
    match = re.search(r'\[[\s]*(\d)[\s]*,[\s]*(\d)[\s]*,[\s]*(\d)[\s]*\]', text)
    if match:
        return [int(match.group(1)), int(match.group(2)), int(match.group(3))]

    # Fallback: look for any 3 digits
    digits = re.findall(r'[1-3]', text)
    if len(digits) >= 3:
        return [int(digits[0]), int(digits[1]), int(digits[2])]

    # Last resort: default ranking
    return [1, 2, 3]
