"""Evidence sources for multipass Pass 1, and citation verification.

PubMed (NCBI E-utilities) supplies real papers for the material stage so the
draft cites studies that exist; verification resolves every claimed PMID/DOI
before a piece reaches review. Set NCBI_API_KEY for higher rate limits.
"""

from __future__ import annotations

import os
import re
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
    """Top PubMed results for a query, with abstracts. Returns [] on network errors."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        search = await client.get(
            f"{EUTILS}/esearch.fcgi",
            params=_params(db="pubmed", term=query, retmode="json", retmax=str(max_results), sort="relevance"),
        )
        search.raise_for_status()
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        fetch = await client.get(
            f"{EUTILS}/efetch.fcgi", params=_params(db="pubmed", id=",".join(ids), retmode="xml")
        )
        fetch.raise_for_status()
        return parse_pubmed_xml(fetch.text)
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return []
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
            resp = await client.get(f"{EUTILS}/esummary.fcgi", params=_params(db="pubmed", id=value, retmode="json"))
            resp.raise_for_status()
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
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "error", "reason": f"Lookup failed: {exc}"}
    finally:
        if own:
            await client.aclose()
