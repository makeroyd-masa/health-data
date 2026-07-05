"""Appointment / visit-prep extractor (PRD §6.3).

MedlinePlus "Talking With Your Doctor" (ingestible) + a one-time static AHRQ "10 Questions"
seed (AHRQ is not crawled — blocked + app-only). Prioritization finding: the richer,
encounter-specific AHRQ question bank is not ingestible from public sources.
"""

from __future__ import annotations

import itertools

import yaml

from ..adapters.medlineplus import MedlinePlusAdapter
from ..core.pipeline import build_blocks
from ..core.schema import (
    Audience,
    ItemContext,
    KnowledgeBlock,
    License,
    ParsedSection,
    SeedConfig,
    Source,
    UseCase,
    slugify,
)
from .base import RunContext, register
from .condition_explainer import medlineplus_item_context

_AHRQ_PUBLISHER = "Agency for Healthcare Research and Quality (AHRQ)"


@register("visit_prep")
class VisitPrepExtractor:
    use_case = "visit_prep"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        blocks: list[KnowledgeBlock] = []
        blocks += self._static_blocks(ctx)
        blocks += self._medlineplus_blocks(ctx)
        return blocks

    # ---------------------------------------------------- static AHRQ seed
    def _static_blocks(self, ctx: RunContext) -> list[KnowledgeBlock]:
        static_dir = ctx.config_dir / ctx.seed.get("static_dir", "visit_prep_static")
        if not static_dir.exists():
            return []
        out: list[KnowledgeBlock] = []
        for path in sorted(static_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            body = "\n".join(f"- {q}" for q in data.get("questions", []))
            if not body.strip():
                continue
            section = data.get("section", "Questions")
            item_ctx = ItemContext(
                source=Source.ahrq,
                source_id=slugify(data.get("title", path.stem)),
                source_url=data["source_url"],
                title=data.get("title", "Questions To Ask Your Doctor"),
                publisher=_AHRQ_PUBLISHER,
                attribution_text=data["attribution_text"].strip(),
                default_license=License.us_gov,
                default_audience=Audience.general,
                adapter_name="ahrq_static",
                extractor_name=self.use_case,
                use_case=UseCase.visit_prep,
                id_stem=slugify(section),
                source_last_updated=data.get("source_last_updated"),
            )
            sec = ParsedSection(
                section_title=section,
                body_markdown=body,
                license=License.us_gov,
                keywords=["visit prep", "questions", "doctor", "caregiver"],
                audience=Audience.general,
            )
            out += build_blocks(item_ctx, [sec], ctx.state, ctx.run_ts)
            ctx.log.info("static AHRQ seed %s -> 1 block", path.name)
        return out

    # ------------------------------------------------- MedlinePlus topics
    def _medlineplus_blocks(self, ctx: RunContext) -> list[KnowledgeBlock]:
        topics = ctx.seed.get("medlineplus_topics", [])
        if not topics:
            return []
        seed = SeedConfig(use_case=self.use_case,
                          raw={"mode": "list", "topics": topics, "retmax": 8})
        adapter = MedlinePlusAdapter(ctx.client)
        discovered = adapter.discover(seed)
        refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)

        out: list[KnowledgeBlock] = []
        for ref in refs:
            try:
                sections = adapter.parse(adapter.fetch(ref, refresh=ctx.refresh))
            except Exception as e:  # noqa: BLE001 - log + skip (PRD §4.8)
                ctx.log.warning("medlineplus failed for %s: %s", ref.url, e)
                continue
            if not sections:
                continue
            for s in sections:  # visit-prep content addresses caregivers too
                if "caregiver" not in s.keywords:
                    s.keywords = [*s.keywords, "caregiver"]
            item_ctx = medlineplus_item_context(
                ref, use_case=UseCase.visit_prep, extractor=self.use_case,
                audience=Audience.patient,
            )
            out += build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
            ctx.log.info("%s -> visit_prep blocks", ref.source_id)
        return out
