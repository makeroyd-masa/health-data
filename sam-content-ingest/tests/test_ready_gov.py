"""Ready.gov adapter + household_readiness extractor tests (against fixtures).

CI never hits live .gov hosts (PRD §7). These use a recorded fixture mirroring the
verified Ready.gov Drupal structure (div.field--name-body, Before/During/After h2s).
"""

from pathlib import Path

import pytest

from sam_ingest.adapters.ready_gov import ReadyGovAdapter
from sam_ingest.core.schema import License, RawItem, SeedConfig, SourceRef
from sam_ingest.core.state import RunState
from sam_ingest.extractors.base import RunContext
from sam_ingest.extractors.household_readiness import HouseholdReadinessExtractor

FIXTURES = Path(__file__).parent / "fixtures" / "ready_gov"


def _raw(name: str, **meta) -> RawItem:
    ref = SourceRef(
        url=f"https://www.ready.gov/{name}",
        source_id=name,
        title=meta.pop("title", name.title()),
        meta={"kind": "html", **meta},
    )
    return RawItem(ref=ref, content=(FIXTURES / f"{name}.html").read_bytes())


def test_html_parse_splits_before_during_after():
    sections = ReadyGovAdapter(client=None).parse(_raw("hurricanes", hazard="hurricane"))
    titles = [s.section_title for s in sections]
    # Lead paragraph before the first h2 becomes an untitled ("") Overview block.
    assert titles == ["", "Before a Hurricane", "During a Hurricane", "After a Hurricane"]
    assert all(s.license == License.us_gov for s in sections)
    assert all("hurricane" in s.keywords for s in sections)


def test_html_parse_strips_chrome():
    sections = ReadyGovAdapter(client=None).parse(_raw("hurricanes", hazard="hurricane"))
    joined = "\n".join(s.body_markdown for s in sections)
    for chrome in ("Skip to main content", "Accept", "Related", "official website", "Home"):
        assert chrome not in joined
    assert "Stay indoors" in joined  # real content survives


def test_pdf_parse_handles_garbage_gracefully():
    ref = SourceRef(url="https://www.ready.gov/x.pdf", source_id="x", title="X",
                    meta={"kind": "pdf"})
    raw = RawItem(ref=ref, content=b"%PDF-1.4 not really a pdf")
    assert ReadyGovAdapter(client=None).parse(raw) == []  # logged + skipped, no crash


def test_extractor_e2e_against_fixture(tmp_path, monkeypatch):
    seed = SeedConfig(
        use_case="household_readiness",
        raw={"pages": [
            {"id": "hurricanes", "title": "Hurricanes",
             "url": "https://www.ready.gov/hurricanes", "kind": "html", "hazard": "hurricane"},
        ]},
    )
    ctx = RunContext(
        client=None, seed=seed, state=RunState(tmp_path / "state.json"),
        run_ts="2026-07-05T00:00:00Z", config_dir=tmp_path,
    )
    # Serve the fixture instead of hitting the network.
    monkeypatch.setattr(
        ReadyGovAdapter, "fetch",
        lambda self, ref, refresh=False: _raw("hurricanes", hazard="hurricane", title=ref.title),
    )
    blocks = HouseholdReadinessExtractor().run(ctx)

    assert len(blocks) == 4
    assert {b.id for b in blocks} == {
        "ready_gov:hurricanes:overview",
        "ready_gov:hurricanes:before-a-hurricane",
        "ready_gov:hurricanes:during-a-hurricane",
        "ready_gov:hurricanes:after-a-hurricane",
    }
    assert all(b.license == License.us_gov for b in blocks)
    assert all("hurricane" in b.keywords for b in blocks)
    assert all(b.citation.attribution_text.startswith("Content from Ready.gov") for b in blocks)
