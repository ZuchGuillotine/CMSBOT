"""Strawman — adversarial critic that attacks the draft hard. Problems only, no fixes."""

from __future__ import annotations

from cmsbot.models import ContentType, Critique, LLMResponse
from cmsbot.providers.base import LLMProvider

SYSTEM = """\
You are a ruthless editorial critic. Your ONLY job is to find every weakness in this draft.

Be specific, be harsh, be thorough. Identify:
- Logical gaps, unsupported claims, or factual concerns
- Weak or generic openings and conclusions
- Clichés, filler phrases, and lazy writing
- Missing perspectives, counterarguments, or nuance
- Structural problems (pacing, flow, transitions)
- Unclear or jargon-heavy passages
- Missed opportunities to engage the reader
- Redundancy or repetition

RULES:
- Do NOT suggest fixes or rewrites. Only identify problems.
- Do NOT soften your critique. Be adversarial.
- Number each problem for clarity.
- Be specific — quote the problematic text when possible."""


class StrawmanAgent:
    """Adversarial critic. Sees the task + draft A. Fresh context, no bias."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def critique(
        self,
        task_prompt: str,
        draft_content: str,
        content_type: ContentType,
    ) -> tuple[Critique, LLMResponse]:
        user_prompt = f"""\
ORIGINAL TASK:
{task_prompt}

DRAFT TO CRITIQUE:
{draft_content}

Tear this apart. Every weakness, every flaw. Be ruthless."""

        response = await self.provider.generate_simple(
            user_prompt=user_prompt,
            system_prompt=SYSTEM,
            max_tokens=3000,
            temperature=0.6,
        )
        critique = Critique(
            problems=response.content,
            round_num=0,  # Set by pipeline
            provider=response.provider,
            model=response.model,
        )
        return critique, response
