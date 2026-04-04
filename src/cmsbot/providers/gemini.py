"""Google Gemini provider using the new google-genai SDK."""

from __future__ import annotations

from google import genai
from google.genai import types

from cmsbot.budget import compute_cost
from cmsbot.models import LLMResponse
from cmsbot.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3-flash-preview"):
        self.model = model
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        # Build contents for the Gemini API
        contents = []
        for msg in messages:
            if not isinstance(msg["content"], str):
                raise TypeError("GeminiProvider only supports text-only messages")
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=msg["content"])],
            ))

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        text = response.text or ""
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage else 0
        output_tokens = usage.candidates_token_count if usage else 0

        return LLMResponse(
            content=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
            provider=self.name,
            cost=compute_cost(self.model, input_tokens, output_tokens),
        )
