from __future__ import annotations

from cmsbot.providers.image_providers import _returns_base64_by_default
from cmsbot.providers.openai_provider import _returns_base64_by_default as openai_returns_base64_by_default


def test_gpt_image_models_return_base64_by_default():
    for model in ("gpt-image-2", "gpt-image-1.5", "gpt-image-1", "chatgpt-image-latest"):
        assert _returns_base64_by_default(model)
        assert openai_returns_base64_by_default(model)


def test_dalle_models_need_explicit_response_format():
    assert not _returns_base64_by_default("dall-e-3")
    assert not openai_returns_base64_by_default("dall-e-3")
