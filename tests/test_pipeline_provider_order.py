from __future__ import annotations

from cmsbot.config import Config, ProviderConfig
from cmsbot.pipeline import AutoreasonPipeline, build_raster_image_providers


class StubProvider:
    def __init__(self, name: str):
        self.name = name


class StubRasterProvider:
    def __init__(self, name: str, *, model: str | None = None):
        self.name = name
        self.model = model


def test_raster_image_providers_use_quality_preference_order(monkeypatch):
    monkeypatch.setattr(
        "cmsbot.pipeline.GrokImageProvider",
        lambda api_key: StubRasterProvider("grok"),
    )
    monkeypatch.setattr(
        "cmsbot.pipeline.GPTImageProvider",
        lambda api_key, model, quality: StubRasterProvider("gpt_image", model=model),
    )
    monkeypatch.setattr(
        "cmsbot.pipeline.NanobananProvider",
        lambda api_key: StubRasterProvider("nanobanana"),
    )

    pipeline = AutoreasonPipeline(
        Config(
            openai=ProviderConfig(api_key="openai", model="gpt"),
            gemini=ProviderConfig(api_key="gemini", model="gemini"),
            xai_key="xai",
            openai_image_model="gpt-image-2",
        )
    )

    providers = build_raster_image_providers(pipeline.config)

    assert [provider.name for provider in providers] == ["grok", "gpt_image", "nanobanana"]
    assert providers[1].model == "gpt-image-2"


def test_infographic_generation_prefers_anthropic(monkeypatch):
    def fake_build_provider(name, config):
        return StubProvider(name)

    monkeypatch.setattr("cmsbot.pipeline.build_provider", fake_build_provider)
    monkeypatch.setattr("cmsbot.pipeline.OpenAIProvider", lambda api_key, model: StubProvider("image_judge"))
    monkeypatch.setattr("cmsbot.pipeline.GPTImageProvider", lambda api_key, model, quality: StubRasterProvider("gpt_image"))

    pipeline = AutoreasonPipeline(
        Config(
            openai=ProviderConfig(api_key="openai", model="gpt"),
            anthropic=ProviderConfig(api_key="anthropic", model="claude"),
        )
    )
    pipeline._build_agents()

    svg_generator = pipeline.image_director.svg_generator
    assert svg_generator.primary_provider.name == "anthropic"
    assert svg_generator.fallback_provider.name == "openai"
