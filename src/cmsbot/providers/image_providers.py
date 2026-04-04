"""Image generation providers — Grok Imagine, Nano Banana (Gemini), Unsplash, and DALL-E.

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

        # Parse target dimensions from size string for the prompt
        w, h = (int(x) for x in size.split("x"))
        orientation = "landscape" if w > h else "portrait" if h > w else "square"

        full_prompt = (
            f"{prompt}\n\n"
            f"Image orientation: {orientation} ({w}x{h} pixels). "
            "Photorealistic style. No text, watermarks, or logos in the image."
        )

        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=full_prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
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
# DALL-E (existing, wrapped in new interface for uniformity)
# ---------------------------------------------------------------------------

DALLE_IMAGE_COSTS = {
    "1024x1024": 0.040,
    "1024x1792": 0.080,
    "1792x1024": 0.080,
}


class DalleImageProvider(ImageProvider):
    """OpenAI DALL-E 3 — wrapped in the ImageProvider interface as fallback."""

    name = "dalle"

    def __init__(self, api_key: str, model: str = "dall-e-3"):
        import openai

        self.model = model
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
            n=1,
            response_format="b64_json",
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        return ImageResult(
            image_bytes=image_bytes,
            provider=self.name,
            model=self.model,
            cost=DALLE_IMAGE_COSTS.get(size, 0.04),
        )
