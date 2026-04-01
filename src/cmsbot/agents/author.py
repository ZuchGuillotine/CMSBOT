"""Author A — writes the initial draft from the task prompt only."""

from __future__ import annotations

from cmsbot.models import ContentType, Draft, LLMResponse
from cmsbot.providers.base import LLMProvider

BLOG_SYSTEM = """\
You are an expert content writer who produces compelling, well-researched blog posts.

Guidelines:
- Write in a clear, authoritative, and engaging voice
- Use concrete examples, data points, and vivid language
- Structure with a strong hook, clear sections, and a memorable conclusion
- Vary sentence length and rhythm for readability
- Avoid clichés, filler phrases, and generic statements
- Write for an intelligent general audience unless told otherwise
- Do NOT include HTML tags — output clean prose with markdown-style headers (## for sections)
- Do NOT add meta-commentary like "Here's the article" — just write the content"""

SOCIAL_SYSTEM = """\
You are an expert social media content creator who writes viral, engaging posts.

Guidelines:
- Write punchy, scroll-stopping content
- Lead with the most compelling hook
- Use short paragraphs and line breaks for readability
- Include relevant hashtag suggestions at the end (5-8 tags)
- Adapt tone to be conversational but authoritative
- Output the main post content, then a separator (---), then platform-specific variations:
  1. Twitter/X thread (280 char segments, numbered)
  2. LinkedIn post (professional tone, ~300 words)
  3. Instagram caption (with emoji, ~150 words)
- Do NOT add meta-commentary — just write the content"""

SYSTEM_PROMPTS = {
    ContentType.BLOG: BLOG_SYSTEM,
    ContentType.SOCIAL: SOCIAL_SYSTEM,
}


class AuthorAgent:
    """Writes the initial draft. Sees only the task prompt — fresh context, no bias."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def write(self, task_prompt: str, content_type: ContentType) -> tuple[Draft, LLMResponse]:
        system = SYSTEM_PROMPTS[content_type]
        response = await self.provider.generate_simple(
            user_prompt=task_prompt,
            system_prompt=system,
            max_tokens=4096,
            temperature=0.8,
        )
        draft = Draft(
            content=response.content,
            round_num=1,
            origin="author",
            provider=response.provider,
            model=response.model,
        )
        return draft, response
