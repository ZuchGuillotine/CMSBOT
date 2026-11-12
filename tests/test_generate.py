import pytest

from cmsbot.hub import generate
from cmsbot.hub.media import LocalStorage
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
BODY = {"hook": "Hook line here.", "beats": [{"narration": "One beat of narration.", "overlay_text": "Beat"}], "ending": "End."}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    hub = Hub(Store(":memory:"), "https://hub.example.com")
    hub.upsert_project(ADMIN, "acme", "Acme", brand={"voiceover": {"voice_id": "v1"}},
                       settings={"monthly_budget_usd": 20, "item_media_cap_usd": 6})
    item = hub.create_item(GEN, "acme", kind="short_video", channel="tiktok", title="V", body=BODY)
    calls = []

    def fake_video(prompt, *, seconds, first_frame_url="", **_):
        calls.append(("video", prompt, seconds, first_frame_url))
        return generate.Result(b"mp4", ".mp4", "video/mp4", cost_usd=1.25)

    def fake_image(prompt, **_):
        calls.append(("image", prompt))
        return generate.Result(b"png", ".png", "image/png")

    def fake_voice(text, *, voice_id, **_):
        calls.append(("voice", text, voice_id))
        return generate.Result(b"mp3", ".mp3", "audio/mpeg", None, {"words": [{"word": "Hook", "start": 0.0, "end": 0.3}]})

    for name, fn in (("seedance", fake_video), ("grok-image", fake_image), ("elevenlabs", fake_voice)):
        monkeypatch.setitem(generate.PROVIDERS[name], "run", fn)
    for env in ("XAI_API_KEY", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(env, "test")
    monkeypatch.delenv("CMSBOT_RATE_SEEDANCE_PER_SECOND", raising=False)
    return hub, LocalStorage(tmp_path), item["id"], calls


def test_nothing_is_generated_before_the_script_is_approved(ctx):
    hub, storage, item, calls = ctx
    with pytest.raises(HubError) as refused:
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="a bottle")
    assert "not been approved" in str(refused.value) and calls == []


def test_still_then_video_uses_the_still_and_records_cost(ctx):
    hub, storage, item, calls = ctx
    hub.submit(GEN, item)
    hub.approve_script(ADMIN, item)
    still = generate.generate_footage(hub, storage, GEN, item, shot=2, prompt="two bottles", kind="image", provider="grok-image")
    assert still["cost_is_estimate"] is True and still["role"] == "shot-02"
    clip = generate.generate_footage(hub, storage, GEN, item, shot=2, prompt="slow push in", seconds=4, provider="seedance")
    assert calls[-1][3].startswith("https://hub.example.com/media/acme/") and calls[-1][3].endswith(".png")
    assert clip["cost_usd"] == 1.25 and clip["cost_is_estimate"] is False
    current = hub.get_item(GEN, item)
    assert [m["role"] for m in current["media"]] == ["shot-02"] and current["media"][0]["url"].endswith(".mp4")
    assert current["cost_usd"] == pytest.approx(1.32)
    assert clip["item_remaining_usd"] == pytest.approx(4.68)


def test_item_cap_and_monthly_budget_are_enforced(ctx):
    hub, storage, item, calls = ctx
    hub.submit(GEN, item)
    hub.approve_script(ADMIN, item)
    with pytest.raises(HubError) as capped:  # 12s * $0.50 estimate = $6.00 is the whole cap; 13 would exceed, but clamp is 12
        hub.record_cost(GEN, "acme", amount_usd=1, category="video", item_id=item)
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="x", seconds=12, provider="seedance")
    assert capped.value.status == 402 and "media cap" in str(capped.value)
    hub.record_cost(GEN, "acme", amount_usd=18.5, category="other")
    with pytest.raises(HubError) as broke:
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="x", seconds=4, provider="seedance")
    assert broke.value.status == 402 and "monthly budget" in str(broke.value)
    assert calls == []


def test_voiceover_attaches_audio_and_word_timing(ctx):
    hub, storage, item, calls = ctx
    hub.submit(GEN, item)
    hub.approve_script(ADMIN, item)
    result = generate.generate_voiceover(hub, storage, GEN, item)
    assert calls[-1] == ("voice", "Hook line here. One beat of narration. End.", "v1")
    assert result["words_timed"] == 1 and "AI-content label" in result["disclosure"]
    assert set(hub.video_assets(hub.get_item(GEN, item))) == {"voiceover", "timing"}


def test_missing_key_and_wrong_provider_are_explained(ctx, monkeypatch):
    hub, storage, item, _ = ctx
    hub.submit(GEN, item)
    hub.approve_script(ADMIN, item)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(HubError) as missing:
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="x", provider="seedance")
    assert "OPENROUTER_API_KEY" in str(missing.value)
    with pytest.raises(HubError):
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="x", provider="elevenlabs")
    with pytest.raises(HubError):
        generate.generate_footage(hub, storage, GEN, item, shot=9, prompt="x")
    assert "seedance" not in generate.options(hub, GEN, item)["providers"]


def test_words_from_character_alignment():
    alignment = {"characters": list("Hi  there."), "character_start_times_seconds": [0, .1, .2, .25, .3, .4, .5, .6, .7, .8],
                 "character_end_times_seconds": [.1, .2, .25, .3, .4, .5, .6, .7, .8, .9]}
    assert generate.words_from_alignment(alignment) == [{"word": "Hi", "start": 0.0, "end": 0.2},
                                                        {"word": "there.", "start": 0.3, "end": 0.9}]
