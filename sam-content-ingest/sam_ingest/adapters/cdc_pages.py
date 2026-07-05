"""CDC direct-page adapter (PRD §6.5/§6.6).

CDC's evergreen STEADI/older-adult-fall-prevention and Travelers' Health prep content is
NOT in the HHS syndication catalog (see the discovery-spike finding), so we fetch the
specific public-domain CDC pages directly. Those hosts are behind Akamai bot management
whose robots PERMITS these paths — the shared client routes them through curl_cffi
impersonation. Logical source `cdc`, license `us_gov`.
"""

from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..core.chunk import clean_soup, split_by_headings
from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.cdc_pages")

_TTL = 24 * 3600
# Content region, most specific first: wwwnc travel uses div[role=main]; cdc.gov uses main.
_CONTENT_SELECTORS = ["div[role=main]", "main", "div.syndicate", "#content", "div.content"]
# Boilerplate section titles to drop (page TOC / nav rails that survive as headings).
_SKIP_TITLES = {"on this page", "related pages", "related links"}
_MIN_SECTION_CHARS = 40


def _page_id(url: str, seed_id: str | None) -> str:
    if seed_id:
        return seed_id
    parts = [p for p in urlsplit(url).path.split("/") if p and p != "index.html"]
    tail = parts[-1].removesuffix(".html") if parts else "page"
    # prefix with the section for cdc.gov/<topic>/<section>/index.html readability
    return f"{parts[-2]}-{tail}" if len(parts) >= 2 and tail == "index" else tail


class CdcPagesAdapter(BaseAdapter):
    name = "cdc"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        for page in seed.get("direct_pages", []):
            yield SourceRef(
                url=page["url"],
                source_id=_page_id(page["url"], page.get("id")),
                title=page.get("title", ""),
                meta={
                    "audience": page.get("audience", "general"),
                    "keywords": page.get("keywords", []),
                },
            )

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        resp = self.client.get(ref.url, ttl=_TTL, refresh=refresh, accept="text/html")
        return RawItem(ref=ref, content=resp.content, content_type=resp.content_type,
                       from_cache=resp.from_cache)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        soup = BeautifulSoup(raw.text(), "lxml")
        container = self._pick_content(soup)
        clean_soup(container)
        keywords = raw.ref.meta.get("keywords", [])
        sections = []
        for title, body in split_by_headings(container):
            if title.strip().lower() in _SKIP_TITLES:
                continue
            if len(body.strip()) < _MIN_SECTION_CHARS:
                continue
            sections.append(
                ParsedSection(
                    section_title=title,
                    body_markdown=body,
                    license=License.us_gov,
                    keywords=list(keywords),
                )
            )
        return sections

    @staticmethod
    def _pick_content(soup: BeautifulSoup):
        best = None
        for sel in _CONTENT_SELECTORS:
            for el in soup.select(sel):
                score = len(el.get_text(" ", strip=True))
                if best is None or score > best[0]:
                    best = (score, el)
        return best[1] if best else (soup.body or soup)
