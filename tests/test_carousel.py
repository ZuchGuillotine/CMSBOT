import pytest

from cmsbot import carousel
from cmsbot.hub.media import LocalStorage, MediaError, S3Storage, content_type_for, media_key
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
SPEC = {"slides": [{"type": "cover", "headline": "Same <b>mineral</b>", "dek": "Why form matters [check dose in trial]"},
                   {"type": "point", "headline": "Oxide", "body": "Poorly absorbed."}], "caption": "c"}
BRAND = {"name": "Acme", "visual": {"colors": {"accent": "#ff0000", "base": "javascript:1"}, "fonts": {"headline": "Inter; } body{x"}}}


def test_slide_html_uses_brand_and_escapes_text():
    built = carousel.build_slides(SPEC, BRAND)
    first = built["html"][0]
    assert "--accent:#ff0000" in first and "javascript" not in first
    assert "Same &lt;b&gt;mineral&lt;/b&gt;" in first and "<b>mineral" not in first
    assert "check dose" not in first  # reviewer gap notes never reach an image
    assert "ACME" in first and "01 / 02" in first
    assert "--font-headline:Inter  bodyx" in first


def test_plain_title_body_slides_are_normalised():
    spec = carousel.normalize({"slides": [{"title": "A", "body": "a"}, {"title": "B", "body": "b"}]}, cta="Try it")
    assert [s["type"] for s in spec["slides"]] == ["cover", "point", "cta"]
    with pytest.raises(carousel.CarouselError):
        carousel.normalize({"slides": []})


def test_slides_hash_tracks_slide_text_only():
    base = carousel.slides_hash(SPEC)
    assert carousel.slides_hash({**SPEC, "caption": "different"}) == base
    changed = {**SPEC, "slides": [{**SPEC["slides"][0], "headline": "New"}, SPEC["slides"][1]]}
    assert carousel.slides_hash(changed) != base


def test_unsafe_background_is_ignored():
    spec = {"slides": [{"type": "point", "headline": "x", "background": "javascript:alert(1)"}]}
    assert "kind-point has-bg" not in carousel.build_slides(spec)["html"][0]
    ok = {"slides": [{"type": "point", "headline": "x", "background": "https://cdn.example.com/a.png"}]}
    assert "kind-point has-bg" in carousel.build_slides(ok)["html"][0]


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"), "https://hub.example.com")
    h.upsert_project(ADMIN, "acme", "Acme", brand=BRAND)
    return h


def test_carousel_cannot_be_approved_with_stale_images(hub):
    item = hub.create_item(GEN, "acme", kind="carousel", channel="instagram", title="C", body=SPEC)
    hub.submit(GEN, item["id"])
    assert item["slides_stale"] is True
    with pytest.raises(HubError) as blocked:
        hub.approve(ADMIN, item["id"])
    assert "Slide images" in str(blocked.value)
    with pytest.raises(HubError):
        hub.set_render(GEN, item["id"], ["/media/a.png"], style="editorial", render_hash="wrong")
    rendered = hub.set_render(GEN, item["id"], ["/media/acme/1/a.png", "/media/acme/1/b.png"], style="editorial",
                              render_hash=carousel.slides_hash(SPEC, "editorial"))
    assert rendered["slides_stale"] is False and rendered["status"] == "in_review"
    hub.approve(ADMIN, item["id"])
    ready = hub.ready_to_post(Actor("grok", "distributor"))[0]
    assert ready["media"][0]["url"] == "https://hub.example.com/media/acme/1/a.png"
    # editing a slide afterwards withdraws approval and marks the images stale again
    edited = hub.update_item(ADMIN, item["id"], body={**SPEC, "slides": [{"type": "cover", "headline": "New"}]})
    assert edited["status"] == "in_review" and edited["slides_stale"] is True


def test_project_scoped_reviewer_cannot_touch_other_projects(hub):
    hub.upsert_project(ADMIN, "other", "Other")
    scoped = Actor("client", "admin", "acme")
    assert [p["slug"] for p in hub.list_projects(scoped)] == ["acme"]
    other = hub.create_item(GEN, "other", kind="post", channel="x", title="t", body={"text": "x"})
    for attempt in (lambda: hub.get_item(scoped, other["id"]), lambda: hub.upsert_project(scoped, "other", "Hijack"),
                    lambda: hub.brief(scoped, "other"), lambda: hub.approve(scoped, other["id"])):
        with pytest.raises(HubError) as denied:
            attempt()
        assert denied.value.status == 403
    assert hub.list_items(scoped, project="other") == []
    learning = hub.propose_learning(GEN, "other", "x")
    with pytest.raises(HubError):
        hub.decide_learning(scoped, learning["id"], "accepted")


def test_media_keys_and_types(tmp_path):
    key = media_key("acme", 7, "../../etc/My Clip!.MP4")
    assert key.startswith("acme/7/") and key.endswith("-My-Clip.mp4") and ".." not in key
    assert content_type_for("a.png") == "image/png"
    with pytest.raises(MediaError):
        content_type_for("run.sh", "text/x-sh")
    storage = LocalStorage(tmp_path)
    url = storage.put(key, b"data", "video/mp4")
    assert url == f"/media/{key}" and storage.path_for(key).read_bytes() == b"data"
    assert storage.path_for("../outside") is None


def test_bucket_storage_returns_public_url():
    calls = []

    class FakeClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

    storage = S3Storage("bucket", "https://media.example.com/", client=FakeClient())
    assert storage.put("acme/1/a.png", b"x", "image/png") == "https://media.example.com/acme/1/a.png"
    assert calls[0]["Bucket"] == "bucket" and calls[0]["ContentType"] == "image/png"
    with pytest.raises(MediaError):
        S3Storage("bucket", "", client=FakeClient())


@pytest.mark.skipif(not carousel.can_render(), reason="no Chrome available")
def test_render_pngs(tmp_path):
    result = carousel.render_pngs(SPEC, BRAND, tmp_path)
    assert [p.name for p in result["files"]] == ["slide-01.png", "slide-02.png"]
    assert result["files"][0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result["hash"] == carousel.slides_hash(SPEC, "editorial")
