"""Aging / caregiving / home safety extractor (PRD §6.5).

MedlinePlus topics (live) + CDC STEADI falls-prevention via HHS syndication (gated on
availability). Caregiver-focused topics are tagged audience=caregiver.
"""

from __future__ import annotations

import itertools

from ..adapters.medlineplus import MedlinePlusAdapter
from ..core.pipeline import build_blocks
from ..core.schema import Audience, KnowledgeBlock, SeedConfig, UseCase
from ._cdc_common import run_cdc_direct_pages, run_cdc_queries
from .base import RunContext, register
from .condition_explainer import medlineplus_item_context


@register("aging_home_safety")
class AgingHomeSafetyExtractor:
    use_case = "aging_home_safety"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        blocks: list[KnowledgeBlock] = []
        blocks += self._medlineplus(ctx)
        cdc_seed = ctx.seed.get("cdc")
        if cdc_seed:
            blocks += run_cdc_direct_pages(ctx, cdc_seed, use_case=UseCase.aging_home_safety,
                                           extractor=self.use_case, default_audience=Audience.patient)
            blocks += run_cdc_queries(ctx, cdc_seed, use_case=UseCase.aging_home_safety,
                                      extractor=self.use_case, audience=Audience.general)
        return blocks

    def _medlineplus(self, ctx: RunContext) -> list[KnowledgeBlock]:
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
            is_caregiver = "caregiver" in ref.title.lower()
            audience = Audience.caregiver if is_caregiver else Audience.patient
            if is_caregiver:
                for s in sections:
                    s.audience = Audience.caregiver
                    if "caregiver" not in s.keywords:
                        s.keywords = [*s.keywords, "caregiver"]
            item_ctx = medlineplus_item_context(
                ref, use_case=UseCase.aging_home_safety, extractor=self.use_case,
                audience=audience,
            )
            out += build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
            ctx.log.info("%s -> aging blocks (audience=%s)", ref.source_id, audience.value)
        return out
