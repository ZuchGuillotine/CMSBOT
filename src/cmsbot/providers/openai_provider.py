"""OpenAI provider (GPT + DALL-E) and OpenAI-compatible provider for Qwen etc."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import openai

from cmsbot.budget import DALLE_COSTS, compute_cost
from cmsbot.models import LLMResponse
from cmsbot.providers.base import LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-5.4-mini"):
        self.model = model
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def generate(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # Newer OpenAI models require max_completion_tokens instead of max_tokens
        params: dict = dict(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
        )
        if self.model.startswith(("o1", "o3", "gpt-5", "gpt-4.1")):
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens

        response = await self._client.chat.completions.create(**params)

        choice = response.choices[0]
        usage = response.usage
        content = choice.message.content or ""

        return LLMResponse(
            content=content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=self.model,
            provider=self.name,
            cost=compute_cost(self.model, usage.prompt_tokens, usage.completion_tokens),
        )

    async def generate_image(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1024",
    ) -> float:
        """Generate an image with DALL-E 3 and save to output_path. Returns cost."""
        response = await self._client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            n=1,
            response_format="b64_json",
        )

        image_data = base64.b64decode(response.data[0].b64_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_data)

        return DALLE_COSTS.get(size, 0.04)


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible API provider for Qwen, Mistral, etc. via Together/OpenRouter."""

    name = "openai_compat"

    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        choice = response.choices[0]
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        content = choice.message.content or ""

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
            provider=self.name,
            cost=compute_cost(self.model, input_tokens, output_tokens),
        )
