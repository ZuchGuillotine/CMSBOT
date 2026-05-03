"""Image generation providers — Grok Imagine, Nano Banana (Gemini), Unsplash, and GPT Image.

Each provider implements the same interface: given a prompt and size,
return raw image bytes.  The ImageDirector generates candidates from
multiple providers and feeds them to the ImageJudge.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

@dataclass
class ImageResult:
    """Raw output from an image provider."""
    image_bytes: bytes
    provider: str
    model: str
    cost: float  # USD


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ImageProvider(ABC):
    """Unified interface for image generation backends."""

    name: str

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        """Generate an image and return raw bytes + metadata."""
        ...


# ---------------------------------------------------------------------------
# Grok Imagine (xAI — OpenAI-compatible API)
# ---------------------------------------------------------------------------

GROK_IMAGE_COSTS = {
    "1024x1024": 0.07,
    "1024x1536": 0.10,
    "1536x1024": 0.10,
}


class GrokImageProvider(ImageProvider):
    """xAI Grok Imagine via OpenAI-compatible images endpoint."""

    name = "grok"

    def __init__(self, api_key: str, model: str = "grok-imagine-image-pro"):
        import openai

        self.model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        # Grok Imagine API does not support the size parameter
        response = await self._client.images.generate(
            model=self.model,
            prompt=prompt,
            n=1,
            response_format="b64_json",
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        return ImageResult(
            image_bytes=image_bytes,
            provider=self.name,
            model=self.model,
            cost=GROK_IMAGE_COSTS.get(size, 0.07),
        )


# ---------------------------------------------------------------------------
# Nano Banana (Google Gemini image generation)
# ---------------------------------------------------------------------------

NANOBANANA_COST_PER_IMAGE = 0.04  # Gemini image gen pricing


class NanobananProvider(ImageProvider):
    """Google Gemini native image generation (Nano Banana Pro)."""

    name = "nanobanana"

    def __init__(self, api_key: str, model: str = "gemini-3-pro-image-preview"):
        from google import genai

        self.model = model
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        from google.genai import types as genai_types

        w, h = (int(x) for x in size.split("x"))
        orientation = "landscape" if w > h else "portrait" if h > w else "square"
        aspect_ratios = {"landscape": "16:9", "portrait": "9:16", "square": "1:1"}
        image_size = "2K" if max(w, h) > 1024 else "1K"

        full_prompt = (
            f"{prompt}\n\n"
            f"Image orientation: {orientation} ({w}x{h} pixels). "
            "Photorealistic, cinematic realism, shot on DSLR, natural light. "
            "No text, watermarks, or logos in the image."
        )

        config_params = {
            "response_modalities": ["IMAGE"],
            "image_config": genai_types.ImageConfig(
                aspect_ratio=aspect_ratios.get(orientation, "16:9"),
                image_size=image_size,
            ),
        }

        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=full_prompt,
            config=genai_types.GenerateContentConfig(**config_params),
        )

        # Extract image bytes from response parts
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.inline_data and part.inline_data.data:
                    raw = part.inline_data.data
                    # Gemini may return base64-encoded or raw bytes
                    if isinstance(raw, str):
                        image_bytes = base64.b64decode(raw)
                    else:
                        image_bytes = raw
                    return ImageResult(
                        image_bytes=image_bytes,
                        provider=self.name,
                        model=self.model,
                        cost=NANOBANANA_COST_PER_IMAGE,
                    )

        raise RuntimeError("Gemini returned no image data")


# ---------------------------------------------------------------------------
# Unsplash (real stock photos)
# ---------------------------------------------------------------------------

UNSPLASH_BASE = "https://api.unsplash.com"


class UnsplashProvider(ImageProvider):
    """Unsplash photo search — returns real photographs, not AI-generated."""

    name = "unsplash"

    def __init__(self, access_key: str):
        import httpx

        self.access_key = access_key
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=30.0,
        )

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        w, h = (int(x) for x in size.split("x"))
        orientation = "landscape" if w > h else "portrait" if h > w else "squarish"

        # Search for photos matching the prompt
        search_resp = await self._http.get(
            f"{UNSPLASH_BASE}/search/photos",
            params={
                "query": prompt,
                "per_page": 5,
                "orientation": orientation,
                "content_filter": "high",
            },
        )
        search_resp.raise_for_status()
        results = search_resp.json().get("results", [])

        if not results:
            raise RuntimeError(f"Unsplash: no results for '{prompt[:60]}...'")

        # Pick the first result
        photo = results[0]

        # Trigger download tracking (required by Unsplash API guidelines)
        download_url = photo.get("links", {}).get("download_location", "")
        if download_url:
            await self._http.get(download_url)

        # Download the image at the requested width
        image_url = photo["urls"]["raw"] + f"&w={w}&h={h}&fit=crop&auto=format&q=85"
        img_resp = await self._http.get(image_url)
        img_resp.raise_for_status()

        return ImageResult(
            image_bytes=img_resp.content,
            provider=self.name,
            model=f"unsplash/{photo.get('user', {}).get('username', 'unknown')}",
            cost=0.0,  # Unsplash is free
        )

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# GPT Image (replaces deprecated DALL-E 3)
# ---------------------------------------------------------------------------

GPT_IMAGE_COSTS = {
    ("1024x1024", "low"): 0.011,
    ("1024x1024", "medium"): 0.042,
    ("1024x1024", "high"): 0.167,
    ("1024x1536", "low"): 0.016,
    ("1024x1536", "medium"): 0.063,
    ("1024x1536", "high"): 0.250,
    ("1536x1024", "low"): 0.016,
    ("1536x1024", "medium"): 0.063,
    ("1536x1024", "high"): 0.250,
}


class GPTImageProvider(ImageProvider):
    """OpenAI GPT Image generation — natively multimodal, replaces deprecated DALL-E."""

    name = "gpt_image"

    def __init__(self, api_key: str, model: str = "gpt-image-2", quality: str = "medium"):
        import openai

        self.model = model
        self.quality = quality
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
    ) -> ImageResult:
        response = await self._client.images.generate(
            model=self.model,
            prompt=prompt,
            size=size,
            quality=self.quality,
            n=1,
            response_format="b64_json",
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        return ImageResult(
            image_bytes=image_bytes,
            provider=self.name,
            model=self.model,
            cost=GPT_IMAGE_COSTS.get((size, self.quality), 0.042),
        )
