"""GovPagesAdapter + TSA/FAA drug-overlay tests."""

from pathlib import Path

from sam_ingest.adapters.gov_pages import GovPagesAdapter
from sam_ingest.core.schema import License, RawItem, Source, SourceRef, TripType
from sam_ingest.core.state import RunState
from sam_ingest.extractors._gov_common import _drug_overlay
from sam_ingest.extractors.base import RunContext

CDC_FIXTURES = Path(__file__).parent / "fixtures" / "cdc"


def test_gov_pages_parse_generalized():
    # Same logic CdcPagesAdapter used to own — now shared via GovPagesAdapter.
    html = (CDC_FIXTURES / "falls_prevention.html").read_bytes()
    ref = SourceRef(url="https://www.cdc.gov/falls/prevention/index.html",
                    source_id="falls-prevention", title="Falls", meta={"keywords": ["falls"]})
    sections = GovPagesAdapter(client=None).parse(RawItem(ref=ref, content=html))
    assert len(sections) >= 3
    assert all(s.license == License.us_gov for s in sections)


def test_drug_overlay_links_rxcui_without_copying_dailymed(tmp_path):
    ctx = RunContext(client=None, seed=None, state=RunState(tmp_path / "s.json"),
                     run_ts="2026-07-06T00:00:00Z", config_dir=tmp_path)
    overlay = [{
        "id": "insulin-in-transit", "source": "tsa", "rxcui": "274783",
        "title": "Traveling with Insulin", "section": "Insulin in Transit",
        "keywords": ["travel", "insulin"],
        "body": "Insulin is allowed through security in carry-on bags; declare it to the officer.",
    }]
    blocks = _drug_overlay(ctx, overlay, use_case="travel_health", extractor="travel_health")
    assert len(blocks) == 1
    b = blocks[0]
    assert b.source == Source.tsa
    assert b.codes.rxcui == ["274783"]          # joins to DailyMed by rxcui
    assert TripType.chronic_condition in b.trip_types
    assert "declare it to the officer" in b.body_markdown  # authored, not DailyMed text
