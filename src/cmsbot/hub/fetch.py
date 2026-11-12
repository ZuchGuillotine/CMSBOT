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
SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe", "template"}
PREFIX = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "#### ", "h6": "#### ", "li": "- ", "blockquote": "> ",
          "dd": "", "p": "", "figcaption": ""}
QUESTION = {"summary", "dt"}  # accordion and definition-list questions: kept, in bold, above their answers
CONTAINERS = {"div", "section", "article", "main", "ul", "ol", "dl", "details", "table", "tbody", "thead", "figure", "td", "th", "tr", "br"}
SCOPES = ("article", "main")


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
    """Keeps every piece of visible text in the page's main content: paragraphs, headings, lists, tables,
    accordion questions, and text sitting directly in a container. Loose text is never dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.meta_title = "", ""
        self.blocks: list[str] = []
        self.scoped: list[str] = []
        self._skip, self._scope, self._in_title = 0, 0, False
        self._tag: str | None = None
        self._text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._rows_in_table = 0

    def _emit(self, line: str) -> None:
        self.blocks.append(line)
        if self._scope:
            self.scoped.append(line)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        self._text = []
        tag, self._tag = self._tag, None
        if not text:
            return
        if tag in QUESTION:
            self._emit(f"**{text}**")
        else:
            self._emit(PREFIX.get(tag or "p", "") + text)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in SKIP:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("property") == "og:title":
            self.meta_title = attributes.get("content") or ""
        if self._skip:
            return
        if tag in SCOPES:
            self._flush()
            self._scope += 1
        elif tag == "table":
            self._flush()
            self._rows_in_table = 0
        elif tag == "tr":
            self._flush()
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is None:
            self._text.append(" ")
        elif tag in PREFIX or tag in QUESTION:
            self._flush()
            self._tag = tag
        elif tag in CONTAINERS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if self._skip:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip().replace("|", "/"))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self._emit("| " + " | ".join(self._row) + " |")
                self._rows_in_table += 1
                if self._rows_in_table == 1:
                    self._emit("| " + " | ".join("---" for _ in self._row) + " |")
            self._row = None
        elif tag in SCOPES:
            self._flush()
            self._scope = max(0, self._scope - 1)
        elif tag in PREFIX or tag in QUESTION or tag in CONTAINERS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._skip:
            return
        elif self._cell is not None:
            self._cell.append(data)
        else:
            self._text.append(data)


def _join(blocks: list[str]) -> str:
    """Table rows and list items sit on consecutive lines; everything else gets a blank line between."""
    out, previous = "", ""
    for block in blocks:
        tight = (block.startswith("| ") and previous.startswith("| ")) or (block.startswith("- ") and previous.startswith("- "))
        out += (("\n" if tight else "\n\n") if out else "") + block
        previous = block
    return out


def extract(page: str) -> dict[str, str]:
    reader = _Reader()
    reader.feed(page)
    reader._flush()
    blocks = reader.scoped if len(" ".join(reader.scoped)) > 400 else reader.blocks  # prefer the <article>/<main>
    heading = next((b[2:] for b in blocks if b.startswith("# ")), "")
    title = heading or html_lib.unescape(reader.meta_title or reader.title).split(" | ")[0].strip()
    return {"title": title, "markdown": _join(blocks)}


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
