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

    def fake_video(prompt, *, cfg, seconds, first_frame_url="", **_):
        assert cfg("OPENROUTER_API_KEY") == "or-key"
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
    for env in ("XAI_API_KEY", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY", "CMSBOT_RATE_SEEDANCE_PER_SECOND"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "from-env")           # environment fallback
    hub.set_setting(ADMIN, "OPENROUTER_API_KEY", "or-key")  # Hub-wide, entered in Settings
    hub.set_setting(ADMIN, "ELEVENLABS_API_KEY", "el-key-123456", "acme")  # this project only
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
    hub.clear_setting(ADMIN, "OPENROUTER_API_KEY")
    with pytest.raises(HubError) as missing:
        generate.generate_footage(hub, storage, GEN, item, shot=1, prompt="x", provider="seedance")
    assert "Settings" in str(missing.value)
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


# ---- music ------------------------------------------------------------------------------------

MUSIC = "Calm minimal ambient instrumental, warm analog synth pads, no vocals."


class FakeEleven:
    """Stands in for api.elevenlabs.io through httpx's mock transport."""

    def __init__(self, tier="creator", status=200):
        self.tier, self.status, self.requests = tier, status, []

    def __call__(self, request):
        import httpx
        self.requests.append(request)
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": self.tier})
        if request.url.path == "/v1/music":
            if self.status >= 400:
                return httpx.Response(self.status, json={"detail": {"status": "error", "message": "nope"}})
            return httpx.Response(200, content=b"ID3-music", headers={"content-type": "audio/mpeg", "song-id": "song-42"})
        return httpx.Response(404)

    def music_bodies(self):
        import json
        return [json.loads(r.content) for r in self.requests if r.url.path == "/v1/music"]


@pytest.fixture
def eleven(monkeypatch):
    import httpx
    fake = FakeEleven()
    real = httpx.Client
    monkeypatch.setattr(generate.httpx, "Client", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(fake)}))
    monkeypatch.setitem(generate.PROVIDERS["elevenlabs-music"], "run", generate.elevenlabs_music)  # the real provider
    monkeypatch.delenv("CMSBOT_ELEVENLABS_MUSIC_MODEL", raising=False)
    monkeypatch.delenv("MUSIC_PROMPT", raising=False)
    return fake


def _approved(hub, item):
    hub.submit(GEN, item)
    hub.approve_script(ADMIN, item)


def test_music_request_is_instrumental_clamped_and_uses_the_model_setting(ctx, eleven, monkeypatch):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    monkeypatch.setenv("CMSBOT_ELEVENLABS_MUSIC_MODEL", "music_v2")
    result = generate.generate_music(hub, storage, GEN, item, prompt=MUSIC, seconds=1)
    body = eleven.music_bodies()[0]
    request = next(r for r in eleven.requests if r.url.path == "/v1/music")
    assert request.method == "POST" and request.headers["xi-api-key"] == "el-key-123456"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert body == {"prompt": MUSIC, "music_length_ms": 3000, "model_id": "music_v2", "force_instrumental": True}
    assert result["seconds"] == 3 and result["song_id"] == "song-42" and result["plan"] == "creator"
    long = generate.generate_music(hub, storage, GEN, item, prompt=MUSIC, seconds=5000)  # 10 minutes at most
    assert eleven.music_bodies()[-1]["music_length_ms"] == 600000 and long["cost_usd"] == pytest.approx(1.5)


def test_music_length_is_clamped_to_the_provider_limits():
    run = generate.PROVIDERS["elevenlabs-music"]
    assert (run["min_seconds"], run["max_seconds"]) == (3, 600)


def test_music_follows_the_voiceover_timing_and_records_cost(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    generate._store(hub, storage, GEN, hub.get_item(GEN, item), "timing",
                    generate.Result(b'{"words": [{"word": "Hook", "start": 0.0, "end": 0.4}, {"word": "End.", "start": 9.1, "end": 9.6}]}',
                                    ".json", "application/json"))
    hub.set_setting(ADMIN, "MUSIC_PROMPT", MUSIC, "acme")
    result = generate.generate_music(hub, storage, GEN, item)
    assert result["seconds"] == 12 and result["length_from"] == "voiceover timing"   # 9.6s + 2s, rounded up
    assert eleven.music_bodies()[0]["music_length_ms"] == 12000 and eleven.music_bodies()[0]["prompt"] == MUSIC
    assert result["cost_usd"] == pytest.approx(12 * 0.0025) and result["cost_is_estimate"] is True
    assert hub.get_item(GEN, item)["cost_usd"] == pytest.approx(0.03)
    assert "music" in generate.options(hub, GEN, item)["providers"]["elevenlabs-music"]["kind"]


def test_music_length_is_estimated_from_the_script_without_timing(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    result = generate.generate_music(hub, storage, GEN, item, prompt=MUSIC)
    assert result["length_from"] == "estimated from the script" and 3 <= result["seconds"] <= 30


def test_music_is_refused_before_script_approval_and_without_a_prompt(ctx, eleven):
    hub, storage, item, _ = ctx
    hub.set_setting(ADMIN, "MUSIC_PROMPT", MUSIC, "acme")
    with pytest.raises(HubError) as early:
        generate.precheck_music(hub, storage, GEN, item)
    assert "not been approved" in str(early.value)
    hub.clear_setting(ADMIN, "MUSIC_PROMPT", "acme")
    _approved(hub, item)
    with pytest.raises(HubError) as no_prompt:
        generate.generate_music(hub, storage, GEN, item)
    assert "MUSIC_PROMPT" in str(no_prompt.value)
    assert eleven.music_bodies() == []
    hub.upsert_project(ADMIN, "acme", "Acme", brand={"voiceover": {"voice_id": "v1"}, "music": {"prompt": MUSIC}})
    assert generate.generate_music(hub, storage, GEN, item)["prompt"] == MUSIC  # the brand kit's music.prompt


def test_music_respects_the_item_cap(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    hub.record_cost(GEN, "acme", amount_usd=5.99, category="video", item_id=item)
    with pytest.raises(HubError) as capped:
        generate.generate_music(hub, storage, GEN, item, prompt=MUSIC, seconds=60)  # $0.15 > $0.01 left
    assert capped.value.status == 402 and "media cap" in str(capped.value)
    assert eleven.music_bodies() == []


def test_new_music_replaces_the_old_and_strips_names(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    first = generate.generate_music(hub, storage, GEN, item, prompt=MUSIC)
    second = generate.generate_music(hub, storage, GEN, item, prompt="Warm piano in the style of Ludovico Einaudi, Acme theme, soft")
    music = [m for m in hub.get_item(GEN, item)["media"] if m.get("role") == "music"]
    assert [m["url"] for m in music] == [second["url"]] and first["url"] != second["url"]
    assert eleven.music_bodies()[-1]["prompt"] == "Warm piano, theme, soft"


def test_free_plan_is_refused(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    eleven.tier = "free"
    with pytest.raises(HubError) as refused:
        generate.generate_music(hub, storage, GEN, item, prompt=MUSIC)
    assert "Free plan" in str(refused.value) and eleven.music_bodies() == []


def test_music_is_copied_from_another_item_at_no_cost(ctx, eleven):
    hub, storage, item, _ = ctx
    _approved(hub, item)
    generate.generate_music(hub, storage, GEN, item, prompt=MUSIC)
    other = hub.create_item(GEN, "acme", kind="short_video", channel="tiktok", title="V2", body=BODY)["id"]
    _approved(hub, other)
    calls = len(eleven.music_bodies())
    copied = generate.generate_music(hub, storage, GEN, other, from_item_id=item)
    assert copied["copied_from"] == item and copied["cost_usd"] == 0 and len(eleven.music_bodies()) == calls
    url = hub.video_assets(hub.get_item(GEN, other))["music"]
    assert url == copied["url"] and storage.path_for(url[len("/media/"):]).read_bytes() == b"ID3-music"
    assert hub.get_item(GEN, other)["cost_usd"] == 0
    hub.upsert_project(ADMIN, "beta", "Beta")
    stranger = hub.create_item(GEN, "beta", kind="short_video", channel="tiktok", title="V3", body=BODY)["id"]
    with pytest.raises(HubError):
        generate.generate_music(hub, storage, GEN, stranger, from_item_id=item)


def test_clean_music_prompt():
    assert generate.clean_music_prompt("Lo-fi beat like Nujabes, MicrosTracker vibe", ["microstracker"]) == "Lo-fi beat, vibe"
    assert generate.clean_music_prompt("slow 70-80 BPM, no drums", []) == "slow 70-80 BPM, no drums"
