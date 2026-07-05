"""CDC content syndication adapter — via the HHS Digital Media platform (PRD §6.5/§6.6).

CDC content now syndicates through HHS Digital Media (`api.digitalmedia.hhs.gov`); the
legacy `tools.cdc.gov/api/v2` catalog has drained. Same API shape. Note:
  - search params are q / topic / sourceurl / mediatypes (NOT searchtext / topics)
  - the title field is `name`; body comes from /media/{id}/syndicate `content`
  - scripts are NOT stripped by default — we pass stripScripts=true explicitly
  - the host 403s generic bots and (from some egress) blocks at the TLS layer, so live
    ingestion may require an unblocked network. This adapter is fixture-tested (PRD §7).

`source` stays the logical publisher `cdc`; license is `us_gov`.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from bs4 import BeautifulSoup

from ..core.chunk import clean_soup, split_by_headings
from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.cdc")

_DEFAULT_BASE = "https://api.digitalmedia.hhs.gov/api/v2/resources"
_TTL = 24 * 3600
_SYNDICATE_PARAMS = {"stripScripts": "true", "stripStyles": "true", "stripImages": "true"}
# The API reports ISO 639-2 codes ("eng"/"spa"), not 639-1 ("en"/"es").
_LANG_ALIASES = {"en": {"en", "eng", "english"}, "es": {"es", "spa", "spanish"}}


class CdcSyndicationAdapter(BaseAdapter):
    name = "cdc"

    def __init__(self, client, base_url: str = _DEFAULT_BASE):
        super().__init__(client)
        self.base_url = base_url.rstrip("/")

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        base = seed.get("base_url", self.base_url)
        self.base_url = base.rstrip("/")
        max_items = str(seed.get("max", 50))
        lang = seed.get("language", "en")
        want_lang = _LANG_ALIASES.get(lang, {lang})
        exclude_notices = not seed.get("include_notices", False)
        for query in seed.get("queries", []):
            # Route by query kind: free-text `q` -> searchResults.json; structured
            # filters (sourceUrlContains, sourceAcronym, tagIds, ...) -> media.json.
            if "q" in query:
                path, params = "/media/searchResults.json", {**query}
            else:
                path, params = "/media.json", {**query}
            params["max"] = max_items
            resp = self.client.get(f"{self.base_url}{path}", params=params, ttl=_TTL)
            for item in _results(resp.text()):
                if (item.get("mediaType") or "").lower() != "html":
                    continue  # skip images/video — we ingest text
                iso = (item.get("language") or {}).get("isoCode", "").lower()
                if iso and iso not in want_lang:
                    continue
                src_url = item.get("sourceUrl", "")
                if exclude_notices and "/notices/" in src_url:
                    continue  # time-sensitive travel notices excluded by default (§6.6)
                mid = item.get("id")
                if mid is None:
                    continue
                yield SourceRef(
                    url=f"{self.base_url}/media/{mid}/syndicate",
                    source_id=str(mid),
                    title=item.get("name", ""),  # field is `name`, not `title`
                    meta={
                        "source_page_url": item.get("sourceUrl", ""),
                        "attribution": item.get("attribution", ""),
                        "source_name": item.get("source", {}).get("name", "CDC"),
                        "source_last_updated": item.get("dateContentUpdated")
                        or item.get("dateModified"),
                        "keywords": [t for t in item.get("tags", []) if isinstance(t, str)],
                    },
                )

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        resp = self.client.get(ref.url, params=_SYNDICATE_PARAMS, ttl=_TTL, refresh=refresh)
        return RawItem(ref=ref, content=resp.content, content_type=resp.content_type,
                       from_cache=resp.from_cache)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        soup = BeautifulSoup(raw.text(), "lxml")
        container = soup.body or soup
        clean_soup(container)
        keywords = raw.ref.meta.get("keywords", [])
        sections = []
        for title, body in split_by_headings(container):
            if not body.strip():
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


def _results(text: str) -> list:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data.get("results", data.get("data", []))
