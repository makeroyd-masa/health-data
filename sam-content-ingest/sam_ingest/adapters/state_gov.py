"""U.S. State Department adapter (PRD §T4.3).

Two ref kinds under one adapter (meta["kind"]):
  - "advisory": driven by the Travel Advisories RSS feed. The feed's <description> carries
    the full advisory text plus level, ISO2, and pubDate — self-sufficient, so no per-page
    fetch. Emitted as volatile blocks (valid_until derived from pubDate).
  - "country_info": the per-country Country Information page (entry/exit, local laws, etc.),
    scraped best-effort. Page structure varies by country, so 404/empty are handled
    gracefully and logged (PRD §T9).

travel.state.gov is behind Akamai; the shared client routes it through curl_cffi. License
us_gov; State Dept attribution preserved.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Iterable

from bs4 import BeautifulSoup
from lxml import etree

from ..core.chunk import clean_soup, html_to_markdown, split_by_headings
from ..core.countries import NAME_TO_ISO2
from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.state_gov")

_TTL = 24 * 3600
_LEVEL_RE = re.compile(r"Level\s*([1-4])")
# Country-info sections worth keeping (periodic); everything else (nav, the advisory
# banner which the RSS stream already covers) is dropped.
_KEEP_TITLE_HINTS = ("about", "travel requirement", "entry", "exit", "visa", "local law",
                     "special circumstance", "health", "safety and security",
                     "tips from the u.s. embassy", "quick facts")


class StateGovAdapter(BaseAdapter):
    name = "state_dept"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        rss_url = seed.get("advisories_rss")
        want_country_info = bool(seed.get("country_info", False))
        url_tmpl = seed.get(
            "country_info_url_template",
            "https://travel.state.gov/content/travel/en/international-travel/"
            "International-Travel-Country-Information-Pages/{country}.html",
        )
        if not rss_url:
            return
        resp = self.client.get(rss_url, ttl=_TTL)
        root = etree.fromstring(resp.content)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            country = title.split(" - ")[0].strip()
            # The RSS category 2-char code is FIPS, not ISO-3166 — resolve from the name.
            iso2 = _name_to_iso2(country)
            if not iso2:
                log.info("SKIP advisory (unmapped country) %r", title)
                continue
            level = _LEVEL_RE.search(title)
            yield SourceRef(
                url=(item.findtext("link") or "").strip(),
                source_id=f"{iso2.lower()}-advisory",
                title=f"{country} Travel Advisory",
                meta={
                    "kind": "advisory",
                    "iso2": iso2,
                    "country": country,
                    "level": level.group(1) if level else None,
                    "pub_date": item.findtext("pubDate"),
                    "description_html": item.findtext("description") or "",
                },
            )
            if want_country_info:
                yield SourceRef(
                    url=url_tmpl.format(country=_country_url_token(country)),
                    source_id=f"{iso2.lower()}-country-info",
                    title=f"{country} Country Information",
                    meta={"kind": "country_info", "iso2": iso2, "country": country},
                )

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        if ref.meta.get("kind") == "advisory":
            # Content already in the RSS description — no per-page fetch.
            return RawItem(ref=ref, content=ref.meta["description_html"].encode("utf-8"),
                           content_type="text/html", from_cache=True)
        resp = self.client.get(ref.url, ttl=_TTL, refresh=refresh, accept="text/html")
        return RawItem(ref=ref, content=resp.content, content_type=resp.content_type,
                       from_cache=resp.from_cache)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        if raw.ref.meta.get("kind") == "advisory":
            return self._parse_advisory(raw)
        return self._parse_country_info(raw)

    def _parse_advisory(self, raw: RawItem) -> list[ParsedSection]:
        body = html_to_markdown(raw.text())
        if not body.strip():
            return []
        level = raw.ref.meta.get("level")
        keywords = ["travel", "advisory"]
        if level:
            keywords.append(f"advisory_level_{level}")
        return [ParsedSection(section_title="Travel Advisory", body_markdown=body,
                              license=License.us_gov, keywords=keywords)]

    def _parse_country_info(self, raw: RawItem) -> list[ParsedSection]:
        soup = BeautifulSoup(raw.text(), "lxml")
        container = soup.body or soup
        clean_soup(container)
        sections = []
        for title, body in split_by_headings(container):
            t = title.strip().lower()
            if not t or not any(h in t for h in _KEEP_TITLE_HINTS):
                continue  # keep only substantive country-info sections
            if len(body.strip()) < 40:
                continue
            sections.append(ParsedSection(section_title=title, body_markdown=body,
                                          license=License.us_gov, keywords=["travel"]))
        return sections


def _name_to_iso2(country: str) -> str | None:
    key = "".join(c for c in unicodedata.normalize("NFKD", country)
                  if not unicodedata.combining(c)).lower().strip()
    if key in NAME_TO_ISO2:
        return NAME_TO_ISO2[key]
    if "," in key:  # "korea, south" -> "south korea"
        head, tail = [p.strip() for p in key.split(",", 1)]
        return NAME_TO_ISO2.get(f"{tail} {head}")
    return None


def _country_url_token(country: str) -> str:
    """Best-effort country name -> State Country-Information URL token.
    'Korea, South' -> 'SouthKorea'; 'Costa Rica' -> 'CostaRica'."""
    if "," in country:
        head, tail = [p.strip() for p in country.split(",", 1)]
        country = f"{tail} {head}"
    return re.sub(r"[^A-Za-z ]", "", country).title().replace(" ", "")


def advisory_valid_until(pub_date: str | None, ttl_days: int) -> str | None:
    """Deterministic valid_until from the RSS pubDate + ttl (keeps re-runs stable).

    The State feed uses a date-only RFC-822 form ("Sun, 28 Jun 2026") that
    parsedate_to_datetime rejects, so fall back to parsing the 'DD Mon YYYY' portion.
    """
    if not pub_date:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", pub_date)
        if m:
            try:
                dt = datetime.strptime(" ".join(m.groups()), "%d %b %Y")
            except ValueError:
                return None
    return (dt + timedelta(days=ttl_days)).date().isoformat() if dt else None
