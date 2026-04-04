from __future__ import annotations

from cmsbot.social_threads import (
    _find_existing_media,
    _normalize_posts,
    _select_section,
    _split_markdown_sections,
)


def test_split_markdown_sections_extracts_top_level_sections():
    content = """# Sample Title

Intro paragraph.

## First Section
First body paragraph.

## Second Section
Second body paragraph.
"""

    sections = _split_markdown_sections(content)

    assert [section["heading"] for section in sections] == ["First Section", "Second Section"]
    assert sections[0]["index"] == 1
    assert "First body paragraph." in sections[0]["body"]


def test_select_section_prefers_first_substantial_section_by_default():
    sections = [
        {"index": 1, "heading": "Short", "body": "too short"},
        {"index": 2, "heading": "Long", "body": "A" * 300},
    ]

    selected = _select_section(sections, section=None, section_title=None)

    assert selected["heading"] == "Long"


def test_normalize_posts_injects_project_reference_and_strips_hashtags():
    posts = [
        {"index": 1, "text": "A concise launch summary with a trailing #tag"},
        {"index": 2, "text": "A second post without the project name."},
    ]

    normalized = _normalize_posts(posts, project_name="AcmeCloud")

    assert normalized[0].startswith("AcmeCloud: ")
    assert "#tag" not in normalized[0]


def test_find_existing_media_prefers_section_image_then_hero(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    hero = images_dir / "hero.jpg"
    hero.write_bytes(b"hero")

    paths = _find_existing_media(tmp_path, section_index=3)
    assert paths == [hero]

    section = images_dir / "section-3.jpg"
    section.write_bytes(b"section")

    paths = _find_existing_media(tmp_path, section_index=3)
    assert paths == [section]
