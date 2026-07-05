"""CDC (HHS Digital Media) syndication adapter tests — fixtures only (host is blocked)."""

from pathlib import Path

from sam_ingest.adapters.cdc_syndication import CdcSyndicationAdapter
from sam_ingest.core.http import Response
from sam_ingest.core.schema import License, RawItem, SeedConfig, SourceRef

FIXTURES = Path(__file__).parent / "fixtures" / "cdc"


class _FakeClient:
    """Serves the media-search fixture for /media requests."""

    def __init__(self, payload: bytes):
        self.payload = payload

    def get(self, url, params=None, **kw):
        return Response(url, self.payload, "application/json", from_cache=True)


def test_discover_maps_fields_and_filters_language():
    payload = (FIXTURES / "media_search.json").read_bytes()
    adapter = CdcSyndicationAdapter(_FakeClient(payload))
    seed = SeedConfig(use_case="aging_home_safety",
                      raw={"queries": [{"q": "STEADI"}], "language": "en"})
    refs = list(adapter.discover(seed))

    assert len(refs) == 1  # Spanish item filtered out
    ref = refs[0]
    assert ref.source_id == "456789"
    assert ref.title == "Prevent Falls: What You Can Do"          # from `name`
    assert ref.url.endswith("/media/456789/syndicate")
    assert ref.meta["source_page_url"] == "https://www.cdc.gov/steadi/patient-education.html"
    assert ref.meta["source_last_updated"] == "2025-08-15"        # dateContentUpdated
    assert "falls" in ref.meta["keywords"]


def test_parse_syndicated_html():
    html = (FIXTURES / "syndicate_steadi.html").read_bytes()
    ref = SourceRef(url="x", source_id="456789", title="Prevent Falls",
                    meta={"keywords": ["falls", "STEADI"]})
    sections = CdcSyndicationAdapter(client=None).parse(RawItem(ref=ref, content=html))

    titles = [s.section_title for s in sections]
    assert titles == ["Why falls matter", "What you can do"]
    assert all(s.license == License.us_gov for s in sections)
    assert all("falls" in s.keywords for s in sections)
    assert "balance and strength" in "\n".join(s.body_markdown for s in sections)
