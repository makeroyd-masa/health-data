"""MedlinePlus adapter (PRD §6.2).

Two modes (config `mode: bulk|list`):
  - list: resolve each seed term via the MedlinePlus Web Service (free, no key, 85 req/min).
  - bulk: download the current date-stamped bulk health-topic XML (resolved dynamically
    from medlineplus.gov/xml.html — never hardcode a URL) and split it into topics.

Both yield the federally-produced, public-domain health-topic summary. We NEVER follow
/ency/ (A.D.A.M.) or /druginfo/ (ASHP) links — those copyrighted subsets are excluded
by URL, since the feed carries no flag for them (PRD §5.3).
"""

from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..core.chunk import clean_soup, split_by_headings
from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.medlineplus")

_WS_URL = "https://wsearch.nlm.nih.gov/ws/query"
_XML_INDEX = "https://medlineplus.gov/xml.html"
_WS_TTL = 18 * 3600  # within the 12–24h caching guidance
_BULK_TTL = 24 * 3600
_EXCLUDE_PATHS = ("/ency/", "/druginfo/")  # A.D.A.M. + ASHP (PRD §5.3)


def _is_health_topic_url(url: str) -> bool:
    path = urlsplit(url).path
    if any(p in path for p in _EXCLUDE_PATHS):
        return False
    return path.endswith(".html") and "medlineplus.gov" in url


def _topic_slug(url: str) -> str:
    return urlsplit(url).path.rsplit("/", 1)[-1].removesuffix(".html")


def _norm(text: str) -> str:
    # Alphanumeric-only so "Parkinson 's Disease" (WS spacing) == "parkinsons disease".
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _words(text: str) -> set[str]:
    """Significant (>=4 char) alphabetic words, for relevance overlap checks."""
    return set(re.findall(r"[a-z]{4,}", text.lower()))


def _strip_highlights(html: str) -> str:
    """Drop the WS search-term highlight spans, keeping their text."""
    soup = BeautifulSoup(html, "lxml")
    for span in soup.find_all("span", class_="qt0"):
        span.unwrap()
    return str(soup)


def _plain(html: str) -> str:
    return BeautifulSoup(_strip_highlights(html), "lxml").get_text(" ", strip=True)


class MedlinePlusAdapter(BaseAdapter):
    name = "medlineplus"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        mode = seed.get("mode", "list")
        if mode == "bulk":
            yield from self._discover_bulk(seed)
        else:
            yield from self._discover_list(seed)

    # -------------------------------------------------------------- list mode
    def _discover_list(self, seed: SeedConfig) -> Iterable[SourceRef]:
        retmax = int(seed.get("retmax", 5))
        for term in seed.get("topics", []):
            resp = self.client.get(
                _WS_URL,
                params={"db": "healthTopics", "term": term, "retmax": str(retmax)},
                ttl=_WS_TTL,
            )
            ref = self._pick_document(term, resp.text())
            if ref is None:
                log.info("no health-topic match for term %r", term)
                continue
            yield ref

    def _pick_document(self, term: str, xml: str) -> SourceRef | None:
        """Pick the best-matching health-topic document.

        WS ranks by relevance, but the top hit can be a sub-topic (e.g. "How to Prevent
        High Blood Pressure" for term "High blood pressure"). Prefer a title that matches
        the term; fall back to WS rank order.
        """
        soup = BeautifulSoup(xml, "xml")
        term_n = _norm(term)
        best: tuple | None = None  # (score, -index, ref)
        for i, doc in enumerate(soup.find_all("document")):
            url = doc.get("url", "")
            if not _is_health_topic_url(url):
                continue
            contents: dict[str, list[str]] = {}
            for c in doc.find_all("content"):
                contents.setdefault(c.get("name", ""), []).append(c.text)
            summary_html = contents.get("FullSummary", [""])[0]
            if not summary_html.strip():
                continue
            title = _plain(contents.get("title", [term])[0])
            title_n = _norm(title)
            score = 3 if title_n == term_n else 2 if (term_n in title_n or title_n in term_n) else 1
            overlap = bool(_words(term) & _words(title))
            keywords = [_plain(m) for m in contents.get("mesh", [])]
            keywords += [_plain(g) for g in contents.get("groupName", [])]
            ref = SourceRef(
                url=url,
                source_id=_topic_slug(url),
                title=title,
                meta={
                    "summary_html": summary_html,
                    "keywords": sorted({k for k in keywords if k}),
                    "term": term,
                },
            )
            # Rank by: score, then lexical overlap, then WS relevance order, then a
            # shorter (more specific) title. Overlap must beat length so we don't pick a
            # short unrelated topic (e.g. "Noise") over "Hearing Disorders and Deafness".
            cand = (score, 1 if overlap else 0, -i, -len(title_n), ref, overlap)
            if best is None or cand[:4] > best[:4]:
                best = cand
        if best is None:
            return None
        # WS relevance is authoritative (it knows synonyms, e.g. "Flu" for "Influenza"),
        # so trust the top pick — but flag weak matches (no shared word, no title overlap)
        # for human review of the seed rather than silently dropping a valid topic.
        if best[0] == 1 and not best[5]:
            log.warning("weak match for term %r -> %s (review seed if wrong)",
                        term, best[4].source_id)
        return best[4]

    # -------------------------------------------------------------- bulk mode
    def _discover_bulk(self, seed: SeedConfig) -> Iterable[SourceRef]:
        limit_terms = seed.get("topics")  # optional filter set for bulk
        idx = self.client.get(_XML_INDEX, ttl=_BULK_TTL).text()
        m = re.findall(r'href="(/xml/mplus_topics_\d{4}-\d{2}-\d{2}\.xml)"', idx)
        if not m:
            log.warning("could not resolve current bulk XML file from %s", _XML_INDEX)
            return
        bulk_url = "https://medlineplus.gov" + sorted(m)[-1]  # latest date
        log.info("bulk XML: %s", bulk_url)
        # Stable cache key so daily-changing filename doesn't force needless re-download.
        xml = self.client.get(bulk_url, ttl=_BULK_TTL, key_override="medlineplus_bulk").text()
        soup = BeautifulSoup(xml, "xml")
        for topic in soup.find_all("health-topic"):
            url = topic.get("url", "")
            if not _is_health_topic_url(url):
                continue
            title = topic.get("title", "")
            if limit_terms and not any(t.lower() in title.lower() for t in limit_terms):
                continue
            summary_el = topic.find("full-summary")
            if summary_el is None or not summary_el.text.strip():
                continue
            keywords = [d.text for d in topic.find_all("descriptor")]
            keywords += [g.text for g in topic.find_all("group")]
            yield SourceRef(
                url=url,
                source_id=_topic_slug(url),
                title=title,
                meta={
                    "summary_html": summary_el.text,
                    "keywords": sorted({k for k in keywords if k}),
                    "source_last_updated": topic.get("date-created"),
                },
            )

    # ------------------------------------------------------- fetch / parse
    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        # Content was resolved during discover() (WS query / bulk file are the cached
        # network calls). This is a passthrough so parse() gets the summary HTML.
        return RawItem(ref=ref, content=ref.meta["summary_html"].encode("utf-8"),
                       content_type="text/html", from_cache=True)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        if not _is_health_topic_url(raw.ref.url):  # defensive exclusion guard
            log.warning("refusing non-health-topic url %s", raw.ref.url)
            return []
        html = _strip_highlights(raw.text())
        soup = BeautifulSoup(html, "lxml")
        clean_soup(soup)
        keywords = raw.ref.meta.get("keywords", [])
        sections = []
        for title, body in split_by_headings(soup):
            if not body.strip():
                continue
            sections.append(
                ParsedSection(
                    section_title=title,
                    body_markdown=body,
                    license=License.medlineplus_terms,
                    keywords=list(keywords),
                )
            )
        return sections
