"""Schema + helper unit tests (PRD §2, §4.5)."""

import pytest
from pydantic import ValidationError

from sam_ingest.core.schema import (
    Audience,
    Citation,
    KnowledgeBlock,
    License,
    Provenance,
    Source,
    UseCase,
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
