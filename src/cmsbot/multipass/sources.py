"""Evidence sources for multipass Pass 1, and citation verification.

PubMed (NCBI E-utilities) supplies real papers for the material stage so the
draft cites studies that exist; verification resolves every claimed PMID/DOI
before a piece reaches review. Set NCBI_API_KEY for higher rate limits.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

import httpx

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF = "https://api.crossref.org/works"
_TIMEOUT = httpx.Timeout(20.0)
_PMID = re.compile(r"\b(?:PMID:?\s*)?(\d{6,9})\b")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)])", re.IGNORECASE)


@dataclass
class Paper:
    pmid: str
    title: str
    journal: str
    year: str
    abstract: str
    doi: str | None = None

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    def as_dict(self) -> dict:
        return {**asdict(self), "url": self.url}


class SourceError(RuntimeError):
    """A source lookup failed after retries."""


# NCBI allows 3 requests/second without an API key (10 with one) and answers
# bursts with HTTP 500 "Search Backend failed", so every E-utilities call goes
# through one throttled, retrying gate.
_ncbi_locks: dict[int, asyncio.Lock] = {}
_ncbi_last = 0.0
_RETRIES = 3


def _ncbi_lock() -> asyncio.Lock:
    # One lock per event loop: a lock created under one asyncio.run() can't be used under another
    loop_id = id(asyncio.get_running_loop())
    return _ncbi_locks.setdefault(loop_id, asyncio.Lock())


def _ncbi_interval() -> float:
    return 0.12 if os.getenv("NCBI_API_KEY") else 0.4


async def _ncbi_get(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> httpx.Response:
    global _ncbi_last
    last_error = "no response"
    for attempt in range(_RETRIES):
        async with _ncbi_lock():
            wait = _ncbi_interval() - (time.monotonic() - _ncbi_last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await client.get(f"{EUTILS}/{path}", params=params)
            except httpx.HTTPError as exc:
                resp, last_error = None, str(exc)
            _ncbi_last = time.monotonic()
        if resp is not None:
            backend_error = resp.status_code == 200 and '"ERROR"' in resp.text[:400]
            if resp.status_code == 200 and not backend_error:
                return resp
            last_error = f"HTTP {resp.status_code}" + (" (backend error)" if backend_error else "")
            if resp.status_code not in (429, 500, 502, 503, 504) and not backend_error:
                break
        await asyncio.sleep(1.5 * (attempt + 1))
    raise SourceError(f"NCBI {path} failed: {last_error}")


def _params(**extra: str) -> dict[str, str]:
    params = {"tool": "cmsbot", **extra}
    if key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = key
    if email := os.getenv("NCBI_EMAIL"):
        params["email"] = email
    return params


def parse_pubmed_xml(xml_text: str, *, abstract_chars: int = 1500) -> list[Paper]:
    """Parse an efetch PubmedArticleSet into Paper records."""
    papers: list[Paper] = []
    root = ET.fromstring(xml_text)
    for article in root.iter("PubmedArticle"):
        pmid = (article.findtext("MedlineCitation/PMID") or "").strip()
        art = article.find("MedlineCitation/Article")
        if not pmid or art is None:
            continue
        title = "".join(art.find("ArticleTitle").itertext()).strip() if art.find("ArticleTitle") is not None else ""
        journal = (art.findtext("Journal/Title") or "").strip()
        year = (
            art.findtext("Journal/JournalIssue/PubDate/Year")
            or (art.findtext("Journal/JournalIssue/PubDate/MedlineDate") or "")[:4]
        ).strip()
        parts = []
        for node in art.findall("Abstract/AbstractText"):
            label = node.get("Label")
            text = "".join(node.itertext()).strip()
            parts.append(f"{label}: {text}" if label else text)
        doi = None
        for node in article.findall("PubmedData/ArticleIdList/ArticleId"):
            if node.get("IdType") == "doi" and node.text:
                doi = node.text.strip()
        papers.append(Paper(pmid=pmid, title=title, journal=journal, year=year,
                            abstract=" ".join(parts)[:abstract_chars], doi=doi))
    return papers


async def search_pubmed(query: str, *, max_results: int = 6, client: httpx.AsyncClient | None = None) -> list[Paper]:
    """Top PubMed results for a query, with abstracts. Raises SourceError when PubMed can't be reached."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        search = await _ncbi_get(
            client, "esearch.fcgi",
            _params(db="pubmed", term=query, retmode="json", retmax=str(max_results), sort="relevance"),
        )
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        fetch = await _ncbi_get(client, "efetch.fcgi", _params(db="pubmed", id=",".join(ids), retmode="xml"))
        return parse_pubmed_xml(fetch.text)
    except (ET.ParseError, ValueError) as exc:
        raise SourceError(f"PubMed returned an unreadable response: {exc}") from exc
    finally:
        if own:
            await client.aclose()


def extract_identifier(citation: str | None) -> tuple[str, str] | None:
    """('doi', value) or ('pmid', value) from a free-text citation or URL."""
    if not citation:
        return None
    if match := _DOI.search(citation):
        return "doi", match.group(1)
    if match := re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", citation):
        return "pmid", match.group(1)
    if match := _PMID.search(citation):
        return "pmid", match.group(1)
    return None


async def verify_citation(citation: str | None, *, client: httpx.AsyncClient | None = None) -> dict:
    """Check that a citation resolves. Returns {status: verified|unresolved|missing|error, ...}."""
    ident = extract_identifier(citation)
    if ident is None:
        return {"status": "missing", "reason": "No PMID or DOI found in the citation."}
    kind, value = ident
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        if kind == "pmid":
            resp = await _ncbi_get(client, "esummary.fcgi", _params(db="pubmed", id=value, retmode="json"))
            record = resp.json().get("result", {}).get(value)
            if record and not record.get("error"):
                return {"status": "verified", "pmid": value, "title": record.get("title", ""),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{value}/"}
            return {"status": "unresolved", "pmid": value, "reason": "PMID not found on PubMed."}
        resp = await client.get(f"{CROSSREF}/{value}")
        if resp.status_code == 404:
            return {"status": "unresolved", "doi": value, "reason": "DOI not found on Crossref."}
        resp.raise_for_status()
        titles = resp.json().get("message", {}).get("title") or [""]
        return {"status": "verified", "doi": value, "title": titles[0], "url": f"https://doi.org/{value}"}
    except (httpx.HTTPError, ValueError, SourceError) as exc:
        return {"status": "error", "reason": f"Lookup failed: {exc}"}
    finally:
        if own:
            await client.aclose()
