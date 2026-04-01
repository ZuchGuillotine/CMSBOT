"""B·rewrite and AB·synthesize candidate generators."""

from __future__ import annotations

from cmsbot.models import ContentType, Draft, LLMResponse
from cmsbot.providers.base import LLMProvider

REWRITER_SYSTEM = """\
You are an expert content writer performing a complete rewrite.

You have the original task, a draft, and a detailed critique of that draft.
Your job: write a NEW version from scratch that addresses EVERY problem in the critique.

RULES:
- Do NOT reference the original draft or critique in your output
- Do NOT add meta-commentary — just write the content
- Use markdown-style headers (## for sections) — no HTML
- The result should be clearly superior to the original
- Maintain the same general topic and scope but improve everything"""

SYNTHESIZER_SYSTEM = """\
You are an expert editor merging the best of two drafts.

You have the original task and two different drafts (A and B) of the same content.
Your job: create a FINAL version that takes the strongest elements from both.

RULES:
- Preserve the best arguments, phrasing, and structure from each draft
- Resolve any contradictions between the drafts
- The result should be better than either individual draft
- Do NOT reference "Draft A" or "Draft B" in your output
- Do NOT add meta-commentary — just write the content
- Use markdown-style headers (## for sections) — no HTML"""


class RewriterAgent:
    """B·rewrite — sees the task, draft A, and critique. Rewrites from scratch."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def rewrite(
        self,
        task_prompt: str,
        draft_content: str,
        critique: str,
        content_type: ContentType,
    ) -> tuple[Draft, LLMResponse]:
        user_prompt = f"""\
ORIGINAL TASK:
{task_prompt}

CURRENT DRAFT:
{draft_content}

CRITIQUE (problems identified by an independent reviewer):
{critique}

Write a completely new version that fixes all these problems."""

        response = await self.provider.generate_simple(
            user_prompt=user_prompt,
            system_prompt=REWRITER_SYSTEM,
            max_tokens=4096,
            temperature=0.8,
        )
        draft = Draft(
            content=response.content,
            round_num=0,  # Set by pipeline
            origin="rewriter",
            provider=response.provider,
            model=response.model,
        )
        return draft, response


class SynthesizerAgent:
    """AB·synthesize — sees the task, draft A, and draft B. Merges the best of both."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def synthesize(
        self,
        task_prompt: str,
        draft_a_content: str,
        draft_b_content: str,
        content_type: ContentType,
    ) -> tuple[Draft, LLMResponse]:
        user_prompt = f"""\
ORIGINAL TASK:
{task_prompt}

DRAFT A:
{draft_a_content}

DRAFT B:
{draft_b_content}

Create a final version that merges the best elements of both drafts."""

        response = await self.provider.generate_simple(
            user_prompt=user_prompt,
            system_prompt=SYNTHESIZER_SYSTEM,
            max_tokens=4096,
            temperature=0.7,
        )
        draft = Draft(
            content=response.content,
            round_num=0,  # Set by pipeline
            origin="synthesizer",
            provider=response.provider,
            model=response.model,
        )
        return draft, response
