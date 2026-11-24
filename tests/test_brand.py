import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from cmsbot.hub import brand, palette
from cmsbot.hub.app import create_app
from cmsbot.hub.media import LocalStorage
from cmsbot.hub.service import Actor, Hub, HubError
from cmsbot.hub.store import Store

ADMIN = Actor("ben", "admin")
GEN = Actor("claude", "generator")
SWATCHES = ["#14213d", "#fca311", "#2a9d8f", "#e5e5e5"]


def picture() -> bytes:
    image = Image.new("RGB", (600, 300), "#faf6ee")
    draw = ImageDraw.Draw(image)
    for index, colour in enumerate(SWATCHES):
        draw.rectangle([20 + index * 140, 60, 140 + index * 140, 240], fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_palette_reads_flat_colours_exactly():
    found = [c["hex"] for c in palette.from_image(picture())]
    assert found[0] == "#faf6ee" and set(SWATCHES) <= set(found)


def test_suggested_theme_is_readable():
    theme = palette.suggest_theme(palette.from_image(picture()))
    assert set(theme) == set(palette.ROLES)
    assert theme["base"] == "#faf6ee" and theme["ink"] == "#14213d"
    assert palette.contrast(theme["base"], theme["ink"]) >= 4.5
    assert palette.contrast(theme["accent"], theme["base"]) >= 2
    dark = palette.suggest_theme([{"hex": "#101014", "share": 0.7}, {"hex": "#22222a", "share": 0.3}])
    assert palette.contrast(dark["base"], dark["ink"]) >= 4.5  # no usable text colour found: one is supplied


def test_css_colours_are_counted():
    found = palette.from_css("a{color:#FFF} b{color:#ffffff;background:#1a2b3c} c{border:#1A2B3C} d{color:#abc}")
    assert found[0]["hex"] in ("#ffffff", "#1a2b3c") and {c["hex"] for c in found} == {"#ffffff", "#1a2b3c", "#aabbcc"}


def test_clean_validates_and_keeps_unknown_fields():
    kit = brand.clean({"website": "https://acme.com", "tone": " Plain. ", "lexicon": {"avoid": "unlock, game-changing\nsynergy"},
                       "visual": {"colors": {"accent": "#ABC", "bogus": "#000000"}, "fonts": {"headline": "Inter; } x{"}},
                       "personas": {"x": {"account": "@acme", "type": "character", "disclosure": "We make this."},
                                    "tiktok": {}, "nowhere": {"account": "a"}}},
                      {"legacy_field": 1, "visual": {"themes": {"sleep": {}}, "wordmark": "Acme"}})
    assert kit["legacy_field"] == 1 and kit["visual"]["themes"] == {"sleep": {}} and kit["visual"]["wordmark"] == "Acme"
    assert kit["tone"] == "Plain." and kit["lexicon"]["avoid"] == ["unlock", "game-changing", "synergy"]
    assert kit["visual"]["colors"] == {"accent": "#aabbcc"} and kit["visual"]["fonts"]["headline"] == "Inter  x"
    assert kit["personas"] == {"x": {"account": "acme", "type": "character", "who": "", "disclosure": "We make this."}}
    assert kit["disclosure"] == {"x": "We make this."}
    for bad in ({"website": "javascript:alert(1)"}, {"visual": {"colors": {"accent": "red"}}}, {"visual": {"logo_url": "file:///etc/passwd"}}):
        with pytest.raises(HubError):
            brand.clean(bad)


@pytest.fixture
def hub():
    h = Hub(Store(":memory:"))
    h.create_token("ben", "admin")
    h.upsert_project(ADMIN, "acme", "Acme")
    return h


def test_saved_kit_reaches_writers_checks_and_renderers(hub):
    hub.set_brand(ADMIN, "acme", {"tone": "Dry and plain.", "lexicon": {"avoid": ["unlock"]},
                                  "visual": {"colors": {"accent": "#ff0000"}, "wordmark": "ACME CO"}}, name="Acme Co")
    assert hub.brief(GEN, "acme")["brand"]["tone"] == "Dry and plain."
    item = hub.create_item(GEN, "acme", kind="post", channel="linkedin", title="t", body={"text": "Unlock your books."})
    assert any(c["check"] == "brand words" for c in item["writing_checks"])
    from cmsbot import carousel
    slide = carousel.build_slides({"slides": [{"type": "cover", "headline": "h"}]}, hub.brand("acme"))["html"][0]
    assert "--accent:#ff0000" in slide and "ACME CO" in slide
    assert hub.list_projects(ADMIN)[0]["name"] == "Acme Co"


def test_agent_draft_waits_for_a_person(hub):
    with pytest.raises(HubError):
        hub.set_brand(GEN, "acme", {"tone": "x"})
    hub.propose_brand(GEN, "acme", {"tone": "Proposed tone.", "visual": {"colors": {"accent": "#00ff00"}}})
    state = hub.get_brand(ADMIN, "acme")
    assert state["brand"] == {} and state["draft"]["tone"] == "Proposed tone." and state["draft"]["proposed_by"] == "claude"
    assert "tone" not in hub.brief(GEN, "acme")["brand"]  # not in effect yet
    hub.set_brand(ADMIN, "acme", {"tone": state["draft"]["tone"]})
    after = hub.get_brand(ADMIN, "acme")
    assert after["brand"]["tone"] == "Proposed tone." and after["draft"] is None
    with pytest.raises(HubError):
        hub.set_brand(Actor("dana", "admin", "other"), "acme", {"tone": "x"})


def test_brand_routes(hub, tmp_path):
    token = hub.create_token("r", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(hub=hub, storage=LocalStorage(tmp_path), with_mcp=False)) as client:
        up = client.post("/api/projects/acme/brand/images?name=palette.png", content=picture(), headers={**headers, "Content-Type": "image/png"}).json()
        assert up["url"].startswith("/media/acme/brand/") and "#fca311" in {c["hex"] for c in up["colors_found"]}
        assert client.post("/api/projects/acme/brand/images?name=notes.txt", content=b"hi", headers=headers).status_code == 400
        assert client.post("/api/projects/acme/brand/images?name=fake.png", content=b"not a picture", headers=headers).status_code == 400
        preview = client.post("/api/projects/acme/brand/preview", headers=headers,
                              json={"brand": {"visual": {"colors": up["suggested_colors"]}, "ctas": ["Start here"]}}).json()
        assert len(preview["html"]) == 3 and "--base:#faf6ee" in preview["html"][0] and "Start here" in preview["html"][2]
        assert hub.get_brand(ADMIN, "acme")["brand"] == {}  # a preview saves nothing
        saved = client.put("/api/projects/acme/brand", headers=headers,
                           json={"brand": {"reference_images": [up["url"]], "visual": {"colors": up["suggested_colors"]}}}).json()
        assert saved["brand"]["reference_images"] == [up["url"]]
        scan = client.post("/api/projects/acme/brand/scan", headers=headers, json={"url": "http://127.0.0.1:1/"})
        assert scan.status_code == 400 and "public internet" in scan.json()["error"]
