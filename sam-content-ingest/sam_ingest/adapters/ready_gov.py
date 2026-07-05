"""Ready.gov / FEMA adapter (PRD §6.1).

No API — server-rendered Drupal HTML pages + linked publication PDFs. HTML pages are
`us_gov` (public domain); reprint PDFs are `ready_gov_reprint` (free to reproduce,
must not be altered, no implied endorsement). Requires a browser-like User-Agent
(handled by the shared client) or the host returns 403.
"""

from __future__ import annotations

import io
import logging
from typing import Iterable

import pdfplumber
from bs4 import BeautifulSoup

from ..core.chunk import clean_soup, html_to_markdown, split_by_headings
from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.ready_gov")

# HTML content cached 24h; PDFs are frozen (2025-09-30) so cache long.
_HTML_TTL = 24 * 3600
_PDF_TTL = 30 * 24 * 3600

# Candidate selectors for the Drupal main-content region, most specific first.
_CONTENT_SELECTORS = [
    "div.field--name-body",
    "main article",
    "main",
    "article",
    "#main-content",
    "#content",
]


class ReadyGovAdapter(BaseAdapter):
    name = "ready_gov"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        for page in seed.get("pages", []):
            yield SourceRef(
                url=page["url"],
                source_id=page["id"],
                title=page.get("title", page["id"]),
                meta={
                    "kind": page.get("kind", "html"),  # html | pdf
                    "hazard": page.get("hazard"),
                    "audience": page.get("audience", "general"),
                },
            )

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        is_pdf = ref.meta.get("kind") == "pdf"
        resp = self.client.get(
            ref.url,
            ttl=_PDF_TTL if is_pdf else _HTML_TTL,
            refresh=refresh,
            accept="application/pdf" if is_pdf else "text/html",
        )
        return RawItem(ref=ref, content=resp.content, content_type=resp.content_type,
                       from_cache=resp.from_cache)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        if raw.ref.meta.get("kind") == "pdf":
            return self._parse_pdf(raw)
        return self._parse_html(raw)

    # ------------------------------------------------------------------ html
    def _parse_html(self, raw: RawItem) -> list[ParsedSection]:
        soup = BeautifulSoup(raw.text(), "lxml")
        container = None
        for sel in _CONTENT_SELECTORS:
            container = soup.select_one(sel)
            if container is not None:
                break
        if container is None:
            container = soup.body or soup
        clean_soup(container)

        hazard = raw.ref.meta.get("hazard")
        keywords = [hazard] if hazard else []
        sections = []
        for title, body in split_by_headings(container):
            if not body.strip():
                continue
            sections.append(
                ParsedSection(
                    section_title=title,
                    body_markdown=body,
                    license=License.us_gov,  # HTML pages are public domain
                    keywords=list(keywords),
                    meta={"kind": "html"},
                )
            )
        return sections

    # ------------------------------------------------------------------- pdf
    def _parse_pdf(self, raw: RawItem) -> list[ParsedSection]:
        try:
            with pdfplumber.open(io.BytesIO(raw.content)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
        except Exception as e:  # noqa: BLE001 - a corrupt/graphic-only PDF must not crash the run
            log.warning("PDF parse failed for %s: %s", raw.ref.url, e)
            return []

        text = "\n".join(pages).strip()
        if not text:
            log.warning("PDF %s yielded no extractable text (likely graphic/form) — skipping",
                        raw.ref.url)
            return []

        # Faithful text as a reference block; keep list-like lines as markdown bullets.
        lines = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                continue
            lines.append(f"- {s}" if _looks_like_item(s) else s)
        body = "\n".join(lines)
        hazard = raw.ref.meta.get("hazard")
        return [
            ParsedSection(
                section_title=raw.ref.title,
                body_markdown=body,
                license=License.ready_gov_reprint,  # reprint PDFs
                keywords=[hazard] if hazard else [],
                meta={"kind": "pdf"},
            )
        ]


def _looks_like_item(line: str) -> bool:
    return line[:2] in ("• ", "- ", "* ") or (len(line) < 90 and line.endswith((":",)))
