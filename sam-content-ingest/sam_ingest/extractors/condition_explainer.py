"""Condition explainer / new diagnosis extractor (PRD §6.2). Source: MedlinePlus."""

from __future__ import annotations

import itertools

from ..adapters.medlineplus import MedlinePlusAdapter
from ..core.pipeline import build_blocks
from ..core.schema import (
    Audience,
    ItemContext,
    KnowledgeBlock,
    License,
    Source,
    UseCase,
)
from .base import RunContext, register

PUBLISHER = "MedlinePlus (U.S. National Library of Medicine)"
ATTRIBUTION = "Courtesy of MedlinePlus from the National Library of Medicine"


def medlineplus_item_context(ref, *, use_case: UseCase, extractor: str,
                             audience: Audience = Audience.patient) -> ItemContext:
    """Shared MedlinePlus ItemContext builder (reused by visit_prep / aging)."""
    return ItemContext(
        source=Source.medlineplus,
        source_id=ref.source_id,
        source_url=ref.url,
        title=ref.title,
        publisher=PUBLISHER,
        attribution_text=ATTRIBUTION,
        default_license=License.medlineplus_terms,
        default_audience=audience,
        adapter_name="medlineplus",
        extractor_name=extractor,
        use_case=use_case,
        id_stem=ref.source_id,
        source_last_updated=ref.meta.get("source_last_updated"),
    )


@register("condition_explainer")
class ConditionExplainerExtractor:
    use_case = "condition_explainer"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        adapter = MedlinePlusAdapter(ctx.client)
        discovered = adapter.discover(ctx.seed)
        refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)

        blocks: list[KnowledgeBlock] = []
        for ref in refs:
            try:
                raw = adapter.fetch(ref, refresh=ctx.refresh)
                sections = adapter.parse(raw)
            except Exception as e:  # noqa: BLE001 - log + skip (PRD §4.8)
                ctx.log.warning("medlineplus failed for %s: %s", ref.url, e)
                continue
            if not sections:
                continue
            item_ctx = medlineplus_item_context(
                ref, use_case=UseCase.condition_explainer, extractor=self.use_case
            )
            item_blocks = build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
            ctx.log.info("%s -> %d blocks", ref.source_id, len(item_blocks))
            blocks.extend(item_blocks)
        return blocks
