from __future__ import annotations

import pytest

from cmsbot.config import Config, ProviderConfig
from cmsbot.repurpose import parse_json_object, pick_provider_name


def test_pick_provider_name_prefers_structured_provider_over_openai_compat():
    config = Config(
        anthropic=ProviderConfig(api_key="anthropic", model="claude"),
        openai_compat=ProviderConfig(api_key="compat", model="qwen"),
    )

    assert pick_provider_name(config) == "anthropic"


def test_parse_json_object_accepts_fenced_json():
    payload = parse_json_object('```json\n{"posts": []}\n```', required_keys=["posts"])

    assert payload == {"posts": []}


def test_parse_json_object_reports_empty_response():
    with pytest.raises(ValueError, match="empty response"):
        parse_json_object("")
