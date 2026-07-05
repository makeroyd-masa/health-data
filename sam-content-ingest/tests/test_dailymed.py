"""DailyMed adapter tests (against recorded fixtures)."""

from pathlib import Path

from sam_ingest.adapters.dailymed import DailyMedAdapter, _LOINC
from sam_ingest.core.schema import License, RawItem, SourceRef

FIXTURES = Path(__file__).parent / "fixtures" / "dailymed"


def _adapter():
    return DailyMedAdapter(client=None)


class _FakeClient:
    """Minimal stand-in for PoliteClient that serves a fixed payload."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def get(self, url, params=None, **kw):
        from sam_ingest.core.http import Response
        return Response(url, self._payload, "application/json", from_cache=True)


def test_resolve_picks_latest_spl_version():
    import json

    payload = (FIXTURES / "spls_ibuprofen.json").read_bytes()
    a = DailyMedAdapter(client=_FakeClient(payload))
    ref = a._resolve_one(name="ibuprofen", rxcui=None, prefer_generic=False)
    assert ref is not None and ref.source_id  # a SETID
    assert ref.url.endswith(".xml")

    data = json.loads(payload)["data"]
    assert int(ref.meta["spl_version"]) == max(int(d["spl_version"]) for d in data)


def test_parse_extracts_loinc_sections():
    xml = (FIXTURES / "spl_lisinopril.xml").read_bytes()
    ref = SourceRef(url="https://dailymed.nlm.nih.gov/x/spls/SID.xml", source_id="SID",
                    title="Lisinopril", meta={"rxcui": "29046", "spl_version": "7"})
    sections = _adapter().parse(RawItem(ref=ref, content=xml))

    labels = {s.section_title for s in sections}
    assert "Indications & Usage" in labels
    assert "Dosage & Administration" in labels
    assert "Warnings and Precautions" in labels
    assert "Adverse Reactions" in labels
    assert "Information for Patients" in labels  # 34076-0 Patient Counseling Information

    # every emitted section maps to a known consumer LOINC, carries rxcui + loinc, PD license
    for s in sections:
        assert s.license == License.public_domain
        assert s.codes["rxcui"] == ["29046"]
        assert s.codes["loinc"][0] in _LOINC
        assert s.body_markdown.strip()


def test_no_duplicate_sections():
    xml = (FIXTURES / "spl_lisinopril.xml").read_bytes()
    ref = SourceRef(url="x", source_id="SID", title="Lisinopril", meta={"spl_version": "7"})
    sections = _adapter().parse(RawItem(ref=ref, content=xml))
    codes = [s.codes["loinc"][0] for s in sections]
    assert len(codes) == len(set(codes))  # ancestor-skip prevents dupes


def test_parse_bad_xml_returns_empty():
    ref = SourceRef(url="x", source_id="SID", title="X", meta={})
    assert _adapter().parse(RawItem(ref=ref, content=b"<not-xml")) == []
