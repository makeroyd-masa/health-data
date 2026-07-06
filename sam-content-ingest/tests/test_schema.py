"""Schema + helper unit tests (PRD §2, §4.5)."""

import pytest
from pydantic import ValidationError

from sam_ingest.core.schema import (
    Audience,
    Citation,
    GeoScope,
    KnowledgeBlock,
    License,
    Provenance,
    Source,
    TripType,
    UseCase,
    Volatility,
    content_hash,
    slugify,
)


def _block(**over):
    base = dict(
        id="medlineplus:diabetes:overview",
        use_case=UseCase.condition_explainer,
        source=Source.medlineplus,
        source_id="4589",
        source_url="https://medlineplus.gov/diabetes.html",
        title="Diabetes",
        section="Overview",
        audience=Audience.patient,
        summary="Short gist.",
        body_markdown="Some clean markdown body.",
        citation=Citation(
            publisher="MedlinePlus",
            source_url="https://medlineplus.gov/diabetes.html",
            attribution_text="Courtesy of MedlinePlus from the National Library of Medicine",
            retrieved_at="2026-07-03T00:00:00Z",
        ),
        license=License.medlineplus_terms,
        ingested_at="2026-07-03T00:00:00Z",
        content_hash="sha256:abc",
        provenance=Provenance(
            extractor="condition_explainer", adapter="medlineplus", pipeline_version="1.1"
        ),
    )
    base.update(over)
    return KnowledgeBlock(**base)


def test_valid_block_roundtrips():
    b = _block()
    assert b.language == "en"
    assert KnowledgeBlock.model_validate_json(b.model_dump_json()).id == b.id


def test_travel_facets_default_to_safe_values():
    # Non-travel blocks get safe defaults so they validate unchanged (PRD §T3.2).
    b = _block()
    assert b.geo is None
    assert b.trip_types == []
    assert b.volatility == Volatility.evergreen
    assert b.valid_until is None


def test_volatile_requires_valid_until():
    with pytest.raises(ValidationError):
        _block(volatility=Volatility.volatile)  # no valid_until
    # ok when valid_until present
    ok = _block(volatility=Volatility.volatile, valid_until="2026-12-31")
    assert ok.valid_until == "2026-12-31"


def test_country_geo_requires_valid_iso2():
    with pytest.raises(ValidationError):
        _block(geo=GeoScope(scope="country", country_iso2="XX"))
    good = _block(geo=GeoScope(scope="country", country_iso2="MX", country_name="Mexico"))
    assert good.geo.country_iso2 == "MX"
    # non-country scopes don't require an iso2
    assert _block(geo=GeoScope(scope="global")).geo.scope == "global"


def test_trip_types_and_facets_roundtrip():
    b = _block(trip_types=[TripType.altitude, TripType.cruise],
               volatility=Volatility.periodic)
    rt = KnowledgeBlock.model_validate_json(b.model_dump_json())
    assert rt.trip_types == [TripType.altitude, TripType.cruise]
    assert rt.volatility == Volatility.periodic


def test_summary_over_300_rejected():
    with pytest.raises(ValidationError):
        _block(summary="x" * 301)


def test_empty_body_rejected():
    with pytest.raises(ValidationError):
        _block(body_markdown="   ")


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        _block(unexpected="nope")


def test_slugify():
    assert slugify("When to see a doctor") == "when-to-see-a-doctor"
    assert slugify("Café / Ünïcode!!") == "cafe-unicode"
    assert slugify("") == "section"
    assert len(slugify("x" * 200)) <= 80


def test_content_hash_stable_and_whitespace_normalized():
    assert content_hash("a\nb") == content_hash("a  \nb   ")
    assert content_hash("a").startswith("sha256:")
    assert content_hash("a") != content_hash("b")
