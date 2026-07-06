"""Pluggable output sink (PRD §4.9).

The default sink writes newline-delimited JSON per use case, individual
markdown-with-frontmatter files for human review, and a manifest that includes the
per-use-case richness summary used for prioritization (PRD §2). A future licensed
source (Mayo) can supply a no-persist sink behind the same interface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import yaml

from .schema import KnowledgeBlock, Source

# DailyMed sections that are consumer/patient-facing rather than clinical (PRD §6.4).
_PATIENT_FACING_SECTIONS = (
    "patient package insert",
    "information for patients",
    "medication guide",
    "medguide",
    "instructions for use",
)


def _is_patient_facing(block: KnowledgeBlock) -> bool:
    label = block.section.lower()
    return any(s in label for s in _PATIENT_FACING_SECTIONS)


class OutputSink(Protocol):
    def write(self, use_case: str, blocks: list[KnowledgeBlock]) -> None: ...
    def finalize(self, run_ts: str, pipeline_version: str) -> dict: ...


class JsonlMarkdownSink:
    """Writes out/blocks/*.jsonl + out/markdown/<use_case>/*.md + out/manifest.json."""

    def __init__(self, out_dir: str | Path):
        self.out = Path(out_dir)
        self.blocks_dir = self.out / "blocks"
        self.md_dir = self.out / "markdown"
        self._by_use_case: dict[str, list[KnowledgeBlock]] = {}

    def write(self, use_case: str, blocks: list[KnowledgeBlock]) -> None:
        blocks = sorted(blocks, key=lambda b: b.id)  # deterministic order
        self._by_use_case[use_case] = blocks

        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.blocks_dir / f"{use_case}.jsonl"
        with jsonl_path.open("w", encoding="utf-8", newline="\n") as fh:
            for b in blocks:
                fh.write(b.model_dump_json() + "\n")

        md_uc_dir = self.md_dir / use_case
        md_uc_dir.mkdir(parents=True, exist_ok=True)
        for b in blocks:
            self._write_markdown(md_uc_dir, b)

    def _write_markdown(self, dir_path: Path, b: KnowledgeBlock) -> None:
        frontmatter = {
            "id": b.id,
            "use_case": b.use_case.value,
            "source": b.source.value,
            "source_url": b.source_url,
            "title": b.title,
            "section": b.section,
            "audience": b.audience.value,
            "language": b.language,
            "license": b.license.value,
            "keywords": b.keywords,
            "publisher": b.citation.publisher,
            "attribution_text": b.citation.attribution_text,
            "source_last_updated": b.source_last_updated,
            "content_hash": b.content_hash,
        }
        fm = yaml.safe_dump(frontmatter, sort_keys=True, allow_unicode=True).strip()
        text = f"---\n{fm}\n---\n\n# {b.title} — {b.section}\n\n{b.body_markdown}\n"
        fname = b.id.replace(":", "__").replace("/", "_") + ".md"
        (dir_path / fname).write_text(text, encoding="utf-8")

    def finalize(self, run_ts: str, pipeline_version: str) -> dict:
        # Recompute from all jsonl on disk so the manifest reflects every use case
        # ingested so far, not only those written in the current invocation.
        use_cases = {}
        block_index = []
        for jsonl in sorted(self.blocks_dir.glob("*.jsonl")) if self.blocks_dir.exists() else []:
            uc = jsonl.stem
            blocks = [
                KnowledgeBlock.model_validate_json(line)
                for line in jsonl.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not blocks:
                continue
            use_cases[uc] = self._richness(blocks)
            for b in blocks:
                block_index.append(
                    {"id": b.id, "content_hash": b.content_hash, "source_url": b.source_url}
                )
        manifest = {
            "run_timestamp": run_ts,
            "pipeline_version": pipeline_version,
            "total_blocks": sum(v["block_count"] for v in use_cases.values()),
            "use_cases": use_cases,
            "blocks": sorted(block_index, key=lambda x: x["id"]),
        }
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        return manifest

    @staticmethod
    def _richness(blocks: list[KnowledgeBlock]) -> dict:
        """Per-use-case content-richness summary for prioritization (PRD §2)."""
        by_source: dict[str, int] = {}
        by_audience = {"patient": 0, "caregiver": 0, "general": 0}
        total_chars = 0
        has_dailymed = False
        patient_facing = clinical = 0
        for b in blocks:
            by_source[b.provenance.adapter] = by_source.get(b.provenance.adapter, 0) + 1
            by_audience[b.audience.value] += 1
            total_chars += len(b.body_markdown)
            if b.source == Source.dailymed:
                has_dailymed = True
                if _is_patient_facing(b):
                    patient_facing += 1
                else:
                    clinical += 1
        summary = {
            "block_count": len(blocks),
            "by_source": dict(sorted(by_source.items())),
            "by_audience": by_audience,
            "total_body_chars": total_chars,
            "avg_body_chars": round(total_chars / len(blocks)) if blocks else 0,
        }
        # Patient-facing vs clinical only where the source distinguishes (DailyMed).
        if has_dailymed:
            summary["dailymed_nature"] = {
                "patient_facing": patient_facing,
                "clinical": clinical,
            }
        # Travel facets: report only when present (PRD §T6).
        by_volatility: dict[str, int] = {}
        by_trip_type: dict[str, int] = {}
        countries: set[str] = set()
        for b in blocks:
            by_volatility[b.volatility.value] = by_volatility.get(b.volatility.value, 0) + 1
            for tt in b.trip_types:
                by_trip_type[tt.value] = by_trip_type.get(tt.value, 0) + 1
            if b.geo and b.geo.country_iso2:
                countries.add(b.geo.country_iso2)
        if set(by_volatility) - {"evergreen"} or countries or by_trip_type:
            summary["by_volatility"] = dict(sorted(by_volatility.items()))
            summary["geo_country_count"] = len(countries)
            summary["by_trip_type"] = dict(sorted(by_trip_type.items()))
        return summary
