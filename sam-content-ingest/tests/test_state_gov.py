"""State Dept adapter tests (against recorded fixtures)."""

from pathlib import Path

from sam_ingest.adapters.state_gov import (
    StateGovAdapter,
    _name_to_iso2,
    advisory_valid_until,
)
from sam_ingest.core.http import Response
from sam_ingest.core.schema import License, RawItem, SeedConfig, SourceRef

FIXTURES = Path(__file__).parent / "fixtures" / "state_gov"


class _RssClient:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get(self, url, params=None, **kw):
        return Response(url, self.payload, "text/xml", from_cache=True)


def test_name_to_iso2_handles_fips_and_names():
    # RSS category codes are FIPS (Japan=JA); we resolve by NAME instead.
    assert _name_to_iso2("Japan") == "JP"
    assert _name_to_iso2("Mexico") == "MX"
    assert _name_to_iso2("Korea, South") == "KR"
    assert _name_to_iso2("Kuwait") == "KW"


def test_valid_until_from_date_only_pubdate():
    # State feed is date-only ("Sun, 28 Jun 2026") — parsedate rejects it; we fall back.
    assert advisory_valid_until("Mon, 15 Jun 2026", 180) == "2026-12-12"
    assert advisory_valid_until(None, 180) is None


def test_discover_advisories_from_rss():
    rss = (FIXTURES / "advisories_rss.xml").read_bytes()
    adapter = StateGovAdapter(_RssClient(rss))
    seed = SeedConfig("travel_health",
                      {"advisories_rss": "https://x/rss.xml", "country_info": True})
    refs = list(adapter.discover(seed))
    advisories = [r for r in refs if r.meta["kind"] == "advisory"]
    infos = [r for r in refs if r.meta["kind"] == "country_info"]
    assert len(advisories) == 2 and len(infos) == 2
    mx = advisories[0]
    assert mx.meta["iso2"] == "MX" and mx.meta["level"] == "2"
    # Japan's FIPS is JA but name resolves to JP
    assert advisories[1].meta["iso2"] == "JP"


def test_parse_advisory_body_and_keywords():
    rss = (FIXTURES / "advisories_rss.xml").read_bytes()
    adapter = StateGovAdapter(_RssClient(rss))
    seed = SeedConfig("travel_health", {"advisories_rss": "https://x/rss.xml"})
    adv = next(r for r in adapter.discover(seed) if r.meta["kind"] == "advisory")
    sections = adapter.parse(adapter.fetch(adv))
    assert len(sections) == 1
    assert "increased caution" in sections[0].body_markdown.lower()
    assert "advisory_level_2" in sections[0].keywords
    assert sections[0].license == License.us_gov


def test_parse_country_info_keeps_substantive_sections():
    html = (FIXTURES / "country_info_mexico.html").read_bytes()
    ref = SourceRef(url="https://travel.state.gov/x", source_id="mx-country-info",
                    title="Mexico Country Information", meta={"kind": "country_info", "iso2": "MX"})
    sections = StateGovAdapter(client=None).parse(RawItem(ref=ref, content=html))
    titles = [s.section_title for s in sections]
    assert any("About Mexico" in t for t in titles)
    assert any("Travel Requirements" in t for t in titles)
    assert not any("Popular Links" in t for t in titles)  # nav dropped
