"""Pipeline + chunk tests: block building, dedupe, ids, deterministic timestamps."""

from bs4 import BeautifulSoup

from sam_ingest.core.chunk import make_summary, split_by_headings
from sam_ingest.core.pipeline import build_blocks
from sam_ingest.core.schema import (
    Audience,
    ItemContext,
    License,
    ParsedSection,
    Source,
    UseCase,
)
from sam_ingest.core.state import RunState


def _ctx():
    return ItemContext(
        source=Source.ready_gov,
        source_id="hurricanes",
        source_url="https://www.ready.gov/hurricanes",
        title="Hurricanes",
        publisher="Ready.gov (FEMA)",
        attribution_text="Content from Ready.gov (FEMA).",
        default_license=License.ready_gov_reprint,
        default_audience=Audience.general,
        adapter_name="ready_gov",
        extractor_name="household_readiness",
        use_case=UseCase.household_readiness,
        id_stem="hurricanes",
    )


def _sections():
    return [
        ParsedSection("Before a Hurricane", "- Build a kit\n- Make a plan", License.ready_gov_reprint),
        ParsedSection("During a Hurricane", "Stay indoors and away from windows.", License.ready_gov_reprint),
    ]


def test_build_blocks_basic(tmp_path):
    state = RunState(tmp_path / "state.json")
    blocks = build_blocks(_ctx(), _sections(), state, "2026-07-05T00:00:00Z")
    assert len(blocks) == 2
    ids = {b.id for b in blocks}
    assert "ready_gov:hurricanes:before-a-hurricane" in ids
    assert all(b.use_case == UseCase.household_readiness for b in blocks)
    assert all(b.license == License.ready_gov_reprint for b in blocks)
    assert all(len(b.summary) <= 300 for b in blocks)


def test_dedupe_identical_sections(tmp_path):
    state = RunState(tmp_path / "state.json")
    secs = [
        ParsedSection("A", "same body text", License.us_gov),
        ParsedSection("B", "same body text", License.us_gov),
    ]
    blocks = build_blocks(_ctx(), secs, state, "2026-07-05T00:00:00Z")
    assert len(blocks) == 1  # second (duplicate body) dropped


def test_deterministic_timestamps_reused(tmp_path):
    state = RunState(tmp_path / "state.json")
    b1 = build_blocks(_ctx(), _sections(), state, "2026-07-05T00:00:00Z")
    state.save()

    # Re-run with a later run_ts; unchanged content must reuse original timestamps.
    state2 = RunState(tmp_path / "state.json")
    b2 = build_blocks(_ctx(), _sections(), state2, "2026-08-01T99:99:99Z")
    assert [b.ingested_at for b in b2] == ["2026-07-05T00:00:00Z"] * 2
    assert [b.model_dump_json() for b in b1] == [b.model_dump_json() for b in b2]


def test_changed_body_bumps_timestamp(tmp_path):
    state = RunState(tmp_path / "state.json")
    build_blocks(_ctx(), _sections(), state, "2026-07-05T00:00:00Z")
    state.save()

    state2 = RunState(tmp_path / "state.json")
    changed = [
        ParsedSection("Before a Hurricane", "- Build a kit\n- NEW ITEM", License.ready_gov_reprint),
        ParsedSection("During a Hurricane", "Stay indoors and away from windows.", License.ready_gov_reprint),
    ]
    blocks = build_blocks(_ctx(), changed, state2, "2026-08-01T00:00:00Z")
    by_section = {b.section: b for b in blocks}
    assert by_section["Before a Hurricane"].ingested_at == "2026-08-01T00:00:00Z"  # changed
    assert by_section["During a Hurricane"].ingested_at == "2026-07-05T00:00:00Z"  # unchanged


def test_split_by_headings():
    html = """
    <div>
      <h2>Symptoms</h2><p>Feeling thirsty.</p>
      <h2>Prevention</h2><ul><li>Exercise</li><li>Diet</li></ul>
    </div>
    """
    sections = split_by_headings(BeautifulSoup(html, "lxml"))
    titles = [t for t, _ in sections]
    assert titles == ["Symptoms", "Prevention"]


def test_split_no_headings_single_section():
    html = "<div><p>Just one narrative paragraph with no headings.</p></div>"
    sections = split_by_headings(BeautifulSoup(html, "lxml"))
    assert len(sections) == 1
    assert sections[0][0] == ""


def test_summary_list_mode():
    body = "- item one\n- item two\n- item three"
    s = make_summary(body, "Supply List")
    assert s == "Supply List: 3 items."
