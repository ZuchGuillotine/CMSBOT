import json
import subprocess

import pytest

from cmsbot import video
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
BODY = {
    "hook": "Your magnesium might be the wrong one.",
    "beats": [{"narration": "Two bottles, same mineral, different result.", "overlay_text": "Glycinate vs oxide"},
              {"narration": "Oxide is poorly absorbed. [check figure]", "overlay_text": "Low absorption"}],
    "ending": "Check the form on your label tonight.", "caption": "c",
}


def test_normalize_orders_hook_beats_ending_and_drops_gap_notes():
    shots = video.normalize(BODY)
    assert [s["role"] for s in shots] == ["shot-01", "shot-02", "shot-03", "shot-04"]
    assert [s["kind"] for s in shots] == ["hook", "beat", "beat", "ending"]
    assert shots[2]["narration"] == "Oxide is poorly absorbed."
    assert shots[0]["captioned"] is False and shots[1]["captioned"] is True
    with pytest.raises(video.VideoError):
        video.normalize({"caption": "only"})


def test_timing_without_voiceover_is_estimated_and_contiguous():
    plan = video.plan_timing(video.normalize(BODY))
    assert plan["source"] == "estimated"
    for current, following in zip(plan["shots"], plan["shots"][1:]):
        assert current["end"] == following["start"]
    assert all(s["end"] - s["start"] >= video.MIN_SHOT_SECONDS for s in plan["shots"])


def test_timing_shares_out_voiceover_length():
    plan = video.plan_timing(video.normalize(BODY), audio_seconds=20)
    assert plan["source"] == "proportional" and plan["seconds"] == pytest.approx(20, abs=0.01)


def test_timing_follows_word_timestamps_and_rejects_a_mismatched_file():
    shots = video.normalize(BODY)
    total = sum(len(s["narration"].split()) for s in shots)
    words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(total)]
    plan = video.plan_timing(shots, audio_seconds=total * 0.5 + 1, words=words)
    assert plan["source"] == "measured"
    first_of_second = len(shots[0]["narration"].split())
    assert plan["shots"][1]["start"] == pytest.approx(first_of_second * 0.5 - 0.08)
    assert plan["shots"][1]["words"][0]["start"] == first_of_second * 0.5
    short = video.plan_timing(shots, audio_seconds=12, words=words[:5])
    assert short["source"] == "proportional" and short["notes"]


def test_captions_are_short_ordered_and_stay_inside_their_shot():
    plan = video.plan_timing(video.normalize(BODY), audio_seconds=14)
    chunks = video.caption_chunks(plan["shots"])
    assert all(len(c["text"]) <= video.CAPTION_MAX_CHARS + 12 and len(c["text"].split()) <= video.CAPTION_MAX_WORDS for c in chunks)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(chunks, chunks[1:]))
    by_shot = {s["number"]: s for s in plan["shots"]}
    assert all(by_shot[c["shot"]]["start"] <= c["start"] and c["end"] <= by_shot[c["shot"]]["end"] + 1e-6 for c in chunks)
    assert 1 not in {c["shot"] for c in chunks}  # the hook is shown large instead
    spoken = sum(len(s["words"]) for s in plan["shots"] if s["captioned"])
    assert sum(len(c["text"].split()) for c in chunks) == spoken


def test_storyboard_hash_tracks_words_and_footage_not_the_caption():
    base = video.storyboard_hash(BODY, {"shot-02": "/media/a.mp4"})
    assert video.storyboard_hash({**BODY, "caption": "other"}, {"shot-02": "/media/a.mp4"}) == base
    assert video.storyboard_hash({**BODY, "ending": "Different."}, {"shot-02": "/media/a.mp4"}) != base
    assert video.storyboard_hash(BODY, {"shot-02": "/media/b.mp4"}) != base


def test_overlay_html_escapes_and_uses_brand():
    theme = video.carousel.resolve_theme({"name": "Acme", "visual": {"colors": {"accent": "#ff0000"}}})
    shot = {**video.normalize({"hook": "<script>x</script>"})[0]}
    page = video.overlay_html(shot, theme, source="Src", card=True)
    assert "&lt;script&gt;" in page and "<script>x" not in page and "frame card" in page
    assert "--accent:#ff0000" in video._sheet(theme, [page]) and "display=block" not in video._sheet(theme, [page])


def test_two_stage_review_in_the_hub():
    hub = Hub(Store(":memory:"), "https://hub.example.com")
    hub.upsert_project(ADMIN, "acme", "Acme")
    item = hub.create_item(GEN, "acme", kind="short_video", channel="tiktok", title="V", body=BODY)
    hub.submit(GEN, item["id"])
    with pytest.raises(HubError) as blocked:
        hub.approve(ADMIN, item["id"])
    assert "Script stage" in str(blocked.value)
    with pytest.raises(HubError):
        hub.approve_script(GEN, item["id"])
    staged = hub.approve_script(ADMIN, item["id"])
    assert staged["status"] == "needs_revision" and "Script approved" in staged["review_notes"]
    hub.add_media(GEN, item["id"], "/media/acme/1/clip.mp4", name="clip.mp4", role="shot-02")
    current = hub.get_item(GEN, item["id"])
    with pytest.raises(HubError):
        hub.set_video(GEN, item["id"], "/media/acme/1/v.mp4", report={"hash": "stale"})
    report = {"hash": video.storyboard_hash(BODY, hub.video_assets(current)), "seconds": 14, "checks": []}
    done = hub.set_video(GEN, item["id"], "/media/acme/1/v.mp4", contact_sheet_url="/media/acme/1/s.jpg", report=report)
    assert done["video_state"] == "current"
    hub.submit(GEN, item["id"])
    assert hub.approve(ADMIN, item["id"])["status"] == "approved"
    ready = hub.ready_to_post(Actor("grok", "distributor"))[0]
    assert [m["role"] for m in ready["media"]] == ["video"]  # working files are not handed to the poster
    assert ready["media"][0]["url"].startswith("https://hub.example.com/media/")
    edited = hub.update_item(ADMIN, item["id"], body={**BODY, "ending": "New ending."})
    assert edited["status"] == "in_review" and edited["video_state"] == "stale"


@pytest.mark.skipif(not video.can_render(), reason="needs ffmpeg and Chrome")
def test_render_end_to_end(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    quiet = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
    # generated clips arrive at 24 fps and tagged bt709; stills and cards carry no colour tags
    subprocess.run([*quiet, "-i", "testsrc2=s=400x736:r=24", "-t", "2", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-color_range", "tv", str(assets / "shot-02.mp4")], check=True)
    subprocess.run([*quiet, "-i", "color=c=0x334466:s=800x600", "-frames:v", "1", str(assets / "shot-03.png")], check=True)
    subprocess.run([*quiet, "-i", "sine=frequency=220:duration=8", "-c:a", "aac", str(assets / "voiceover.m4a")], check=True)
    found = video.find_assets(assets)
    assert set(found) == {"shot-02", "shot-03", "voiceover"}
    report = video.render(BODY, {"name": "Acme"}, tmp_path / "out", assets=found)
    assert report["file"] == {"width": 1080, "height": 1920, "has_audio": True, "seconds": pytest.approx(8, abs=0.2),
                              "picture_seconds": pytest.approx(8, abs=0.2)}
    assert report["video"].stat().st_size > 10_000 and report["contact_sheet"].exists()
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["every word captioned"]["ok"] and checks["size"]["ok"] and checks["picture"]["ok"]
    # the picture must run as long as the sound: count frames, since the file's length is the sound's
    frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                             "-of", "csv=p=0", str(report["video"])], capture_output=True, text=True, check=True).stdout.strip()
    assert int(frames) == pytest.approx(8 * video.FPS, abs=6)
    assert not checks["footage"]["ok"] and "1, 4" in checks["footage"]["detail"]
    saved = json.loads((tmp_path / "out" / "render.json").read_text())
    assert saved["hash"] == video.storyboard_hash(BODY)
    assert not (tmp_path / "out" / "work").exists()
    # captions must be on screen in every captioned shot, including after real footage: measure, don't assume
    from PIL import Image
    for chunk in report["captions"]:
        frame = tmp_path / f"c{chunk['shot']}.png"
        middle = (chunk["start"] + chunk["end"]) / 2
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{middle:.2f}", "-i", str(report["video"]), "-frames:v", "1", str(frame)], check=True)
        with Image.open(frame) as image:
            band = image.convert("L").crop((84, 1080, 910, 1310))
            assert band.getextrema()[1] > 200, f"no caption visible at {middle:.1f}s"


def test_a_picture_that_ends_before_the_sound_fails_its_check():
    timing = video.plan_timing(video.normalize(BODY))
    captions = video.caption_chunks(timing["shots"])
    whole = {"width": 1080, "height": 1920, "has_audio": True, "seconds": timing["seconds"], "picture_seconds": timing["seconds"]}
    cut = {**whole, "picture_seconds": round(timing["seconds"] * 0.4, 2)}
    picture = lambda file: next(c for c in video.check(timing, captions, {}, file) if c["check"] == "picture")
    assert picture(whole)["ok"]
    assert not picture(cut)["ok"] and "of picture" in picture(cut)["detail"]


def test_every_segment_is_encoded_with_the_same_colour_tags():
    tags = dict(zip(video._ENCODE[::2], video._ENCODE[1::2]))
    assert (tags["-colorspace"], tags["-color_primaries"], tags["-color_trc"], tags["-color_range"]) == ("bt709", "bt709", "bt709", "tv")
