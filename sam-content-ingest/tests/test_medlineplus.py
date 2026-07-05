"""MedlinePlus adapter tests (against recorded Web Service fixtures)."""

from pathlib import Path

from sam_ingest.adapters.medlineplus import MedlinePlusAdapter, _is_health_topic_url
from sam_ingest.core.schema import License, RawItem, SourceRef

FIXTURES = Path(__file__).parent / "fixtures" / "medlineplus"


def _adapter():
    return MedlinePlusAdapter(client=None)


def test_exclusion_by_url():
    assert _is_health_topic_url("https://medlineplus.gov/diabetes.html")
    assert not _is_health_topic_url("https://medlineplus.gov/ency/article/000305.htm")
    assert not _is_health_topic_url("https://medlineplus.gov/druginfo/meds/a600045.html")


def test_pick_document_diabetes():
    xml = (FIXTURES / "ws_diabetes.xml").read_text(encoding="utf-8")
    ref = _adapter()._pick_document("diabetes", xml)
    assert ref is not None
    assert ref.source_id == "diabetes"
    assert ref.url == "https://medlineplus.gov/diabetes.html"
    assert ref.meta["summary_html"]
    assert any("Diabetes" in k for k in ref.meta["keywords"])


def test_pick_document_prefers_exact_title_over_subtopic():
    # WS's top hit for this term can be "How to Prevent High Blood Pressure";
    # the scorer should prefer the main "High Blood Pressure" topic.
    xml = (FIXTURES / "ws_high_blood_pressure.xml").read_text(encoding="utf-8")
    ref = _adapter()._pick_document("High blood pressure", xml)
    assert ref is not None
    assert ref.source_id == "highbloodpressure"


def test_parse_produces_overview_block():
    xml = (FIXTURES / "ws_diabetes.xml").read_text(encoding="utf-8")
    ref = _adapter()._pick_document("diabetes", xml)
    raw = RawItem(ref=ref, content=ref.meta["summary_html"].encode("utf-8"))
    sections = _adapter().parse(raw)
    assert sections
    assert all(s.license == License.medlineplus_terms for s in sections)
    body = sections[0].body_markdown
    assert "diabetes" in body.lower()
    assert "qt0" not in body  # WS highlight spans stripped


def test_parse_refuses_excluded_url():
    ref = SourceRef(url="https://medlineplus.gov/ency/article/000305.htm", source_id="x",
                    meta={"summary_html": "<p>copyrighted</p>", "keywords": []})
    raw = RawItem(ref=ref, content=b"<p>copyrighted</p>")
    assert _adapter().parse(raw) == []
