"""visit_prep static AHRQ seed test (no network)."""

from pathlib import Path

import yaml

from sam_ingest.core.schema import License, SeedConfig, Source
from sam_ingest.core.state import RunState
from sam_ingest.extractors.base import RunContext
from sam_ingest.extractors.visit_prep import VisitPrepExtractor

REPO_CONFIG = Path(__file__).parent.parent / "config"


def test_static_ahrq_block(tmp_path):
    # Point the extractor at the repo's real static seed, no MedlinePlus topics.
    ctx = RunContext(
        client=None,
        seed=SeedConfig(use_case="visit_prep", raw={"static_dir": "visit_prep_static"}),
        state=RunState(tmp_path / "state.json"),
        run_ts="2026-07-05T00:00:00Z",
        config_dir=REPO_CONFIG,
    )
    blocks = VisitPrepExtractor()._static_blocks(ctx)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.source == Source.ahrq
    assert b.license == License.us_gov
    assert b.provenance.adapter == "ahrq_static"
    assert "AHRQ" in b.citation.attribution_text or "Agency" in b.citation.attribution_text
    assert b.body_markdown.count("\n- ") + b.body_markdown.count("- ") >= 10  # 10 questions
    assert b.summary.endswith("10 items.")


def test_static_seed_yaml_is_valid():
    data = yaml.safe_load((REPO_CONFIG / "visit_prep_static" / "ahrq_10_questions.yaml").read_text(encoding="utf-8"))
    assert len(data["questions"]) == 10
    assert data["source_url"].startswith("https://www.ahrq.gov/")
