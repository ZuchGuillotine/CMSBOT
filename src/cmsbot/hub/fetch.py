"""Read an already-published page so it can become an anchor. Public http(s) addresses only."""

from __future__ import annotations

import html as html_lib
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 3_000_000
SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe", "button"}
BLOCK = {"p": "", "h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "li": "- ", "blockquote": "> "}


class FetchError(Exception):
    pass


def check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Give a full http(s) address.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, None)}
    except socket.gaierror as exc:
        raise FetchError(f"Could not find {parts.hostname}.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("That address is not on the public internet.")


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.meta_title, self.blocks = "", "", []
        self._skip, self._tag, self._text, self._in_title, self._scope = 0, None, [], False, 0
        self.scoped: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("property") == "og:title":
            self.meta_title = attributes.get("content") or ""
        elif tag in ("article", "main"):
            self._scope += 1
        elif tag in BLOCK and not self._skip:
            self._flush()
            self._tag = tag

    def handle_endtag(self, tag):
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("article", "main"):
            self._flush()
            self._scope = max(0, self._scope - 1)
        elif tag in BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and self._tag:
            self._text.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if text and self._tag:
            line = BLOCK[self._tag] + text
            self.blocks.append(line)
            if self._scope:
                self.scoped.append(line)
        self._tag, self._text = None, []


def extract(page: str) -> dict[str, str]:
    reader = _Reader()
    reader.feed(page)
    reader._flush()
    blocks = reader.scoped if len(" ".join(reader.scoped)) > 400 else reader.blocks  # prefer the <article>/<main>
    heading = next((b[2:] for b in blocks if b.startswith("# ")), "")
    title = heading or html_lib.unescape(reader.meta_title or reader.title).split(" | ")[0].strip()
    return {"title": title, "markdown": "\n\n".join(blocks)}


def read_page(url: str) -> dict[str, str]:
    check_public(url)
    try:
        with httpx.stream("GET", url, timeout=20, follow_redirects=False, headers={"User-Agent": "cmsbot-hub"}) as response:
            if response.status_code in (301, 302, 307, 308) and response.headers.get("location"):
                target = str(httpx.URL(url).join(response.headers["location"]))
                check_public(target)
                return read_page(target) if target != url else {"title": "", "markdown": ""}
            if response.status_code >= 400:
                raise FetchError(f"The page answered {response.status_code}.")
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    break
            encoding = response.encoding or "utf-8"
    except httpx.HTTPError as exc:
        raise FetchError(f"Could not read the page: {exc}") from exc
    return extract(data.decode(encoding, errors="replace"))
