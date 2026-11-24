"""The brand kit: what a project sounds and looks like. Edited by a person on the Brand tab; an agent
can draft one for them to accept. Everything that writes or renders for the project reads it."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx

from cmsbot.hub import fetch, palette
from cmsbot.hub.service import CHANNELS, HubError

SAFE_URL = re.compile(r"^(https?://|/media/)[^\s\"'<>()]+$")
FONT_ROLES = ("headline", "body", "label")
PERSONA_TYPES = ("brand", "character")


def _lines(value: Any, limit: int = 40) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\n,]", value) if "\n" in value or "," in value else [value]
    return [str(v).strip()[:300] for v in (value or []) if str(v).strip()][:limit]


def _url(value: Any, what: str) -> str:
    value = str(value or "").strip()
    if value and not SAFE_URL.match(value):
        raise HubError(f"{what} must be a web address starting with http:// or https://.")
    return value


def clean(incoming: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate what the form (or an agent) sent and merge it over the stored kit, keeping fields it doesn't know."""
    out = dict(existing or {})
    for key in ("description", "tone", "audience_notes"):
        if key in incoming:
            out[key] = str(incoming[key] or "").strip()[:6000]
    if "website" in incoming:
        out["website"] = _url(incoming["website"], "The site link")
    for key in ("disallowed_claims", "ctas", "brand_rules"):
        if key in incoming:
            out[key] = _lines(incoming[key])
    if "lexicon" in incoming:
        lexicon = incoming["lexicon"] or {}
        out["lexicon"] = {"prefer": _lines(lexicon.get("prefer")), "avoid": _lines(lexicon.get("avoid"))}
    if "reference_images" in incoming:
        out["reference_images"] = [_url(u, "A reference image") for u in (incoming["reference_images"] or [])][:12]
    if "voiceover" in incoming and isinstance(incoming["voiceover"], dict):
        out["voiceover"] = {"voice_id": str(incoming["voiceover"].get("voice_id") or "").strip()[:80]}
    if "visual" in incoming:
        given = incoming["visual"] or {}
        visual = dict(out.get("visual") or {})
        for key in ("wordmark", "footer"):
            if key in given:
                visual[key] = str(given[key] or "").strip()[:120]
        if "logo_url" in given:
            visual["logo_url"] = _url(given["logo_url"], "The logo")
        if "font_css_url" in given:
            visual["font_css_url"] = _url(given["font_css_url"], "The font stylesheet")
        if "carousel_style" in given:
            visual["carousel_style"] = re.sub(r"[^a-z0-9-]", "", str(given["carousel_style"] or "").lower())
        if "colors" in given:
            colours = {}
            for role, value in (given["colors"] or {}).items():
                if role in palette.ROLES and str(value).strip():
                    try:
                        colours[role] = palette.normalise(str(value))
                    except ValueError as exc:
                        raise HubError(str(exc)) from exc
            visual["colors"] = colours
        if "fonts" in given:
            visual["fonts"] = {role: re.sub(r"[;{}<>]", "", str(v)).strip()[:200] for role, v in (given["fonts"] or {}).items()
                               if role in FONT_ROLES and str(v).strip()}
        out["visual"] = visual
    if "personas" in incoming:
        personas, disclosure = {}, {}
        for channel, row in (incoming["personas"] or {}).items():
            if channel not in CHANNELS or not isinstance(row, dict):
                continue
            entry = {"account": str(row.get("account") or "").strip().lstrip("@")[:80],
                     "type": row.get("type") if row.get("type") in PERSONA_TYPES else "brand",
                     "who": str(row.get("who") or "").strip()[:600], "disclosure": str(row.get("disclosure") or "").strip()[:400]}
            if any(entry[k] for k in ("account", "who", "disclosure")):
                personas[channel] = entry
                if entry["disclosure"]:
                    disclosure[channel] = entry["disclosure"]
        out["personas"], out["disclosure"] = personas, disclosure
    return out


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.sheets: list[str] = []
        self.icon, self.title, self.inline = "", "", []
        self._in = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "stylesheet" in rel and a.get("href"):
                self.sheets.append(a["href"])
            elif "icon" in rel and a.get("href") and not self.icon:
                self.icon = a["href"]
        elif tag in ("title", "style"):
            self._in = tag
        if a.get("style"):
            self.inline.append(a["style"])

    def handle_endtag(self, tag):
        if tag == self._in:
            self._in = ""

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "style":
            self.inline.append(data)


def _get(url: str, limit: int = 1_500_000) -> str:
    fetch.check_public(url)
    with httpx.stream("GET", url, timeout=15, follow_redirects=True, headers={"User-Agent": "cmsbot-hub"}) as response:
        if response.status_code >= 400:
            raise fetch.FetchError(f"{url} answered {response.status_code}.")
        fetch.check_public(str(response.url))
        data = b""
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > limit:
                break
        return data.decode(response.encoding or "utf-8", errors="replace")


def scan_site(url: str) -> dict[str, Any]:
    """The mechanical part of a brand kit, read from the site: name, description, colours in use, a logo candidate.
    Tone is not something a page states; a person or a drafting agent writes that."""
    try:
        page = _get(url)
    except (fetch.FetchError, httpx.HTTPError) as exc:
        raise HubError(f"Could not read the site: {exc}") from exc
    head = _Head()
    head.feed(page)
    css = "\n".join(head.inline)
    for href in head.sheets[:3]:
        try:
            css += "\n" + _get(urljoin(url, href), 600_000)
        except (fetch.FetchError, httpx.HTTPError):
            continue
    colours = palette.from_css(css, 10)
    theme_colour = head.meta.get("theme-color", "")
    if palette.HEX.fullmatch(theme_colour or ""):
        colours.insert(0, {"hex": palette.normalise(theme_colour), "share": 0.0})
    logo = head.meta.get("og:image") or head.icon
    return {
        "website": url, "name": (head.meta.get("og:site_name") or head.title.split("|")[0].split(" - ")[0]).strip()[:80],
        "description": (head.meta.get("description") or head.meta.get("og:description") or "").strip()[:600],
        "colors_found": colours, "suggested_colors": palette.suggest_theme(colours) if colours else {},
        "logo_candidate": urljoin(url, logo) if logo else "",
    }
