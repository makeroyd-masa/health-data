"""Household Emergency Readiness extractor (PRD §6.1). Source: Ready.gov / FEMA."""

from __future__ import annotations

import itertools

from ..adapters.ready_gov import ReadyGovAdapter
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

_PUBLISHER = "Ready.gov (FEMA)"
_ATTRIBUTION = (
    "Content from Ready.gov (U.S. Federal Emergency Management Agency). "
    "Reproduced without alteration; not an endorsement by FEMA or the U.S. Government."
)


@register("household_readiness")
class HouseholdReadinessExtractor:
    use_case = "household_readiness"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        # local_dir (optional): read pre-captured files instead of fetching live —
        # for running from an Akamai-blocked egress. Relative paths resolve to the
        # project root (config_dir's parent).
        local = ctx.seed.get("local_dir")
        if local:
            local_path = local if str(local).startswith(("/", "\\")) or ":" in str(local) \
                else ctx.config_dir.parent / local
            ctx.log.info("ready_gov: reading captured files from %s", local_path)
        else:
            local_path = None

        adapter = ReadyGovAdapter(ctx.client, local_dir=local_path)
        discovered = adapter.discover(ctx.seed)
        refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)

        blocks: list[KnowledgeBlock] = []
        for ref in refs:
            try:
                raw = adapter.fetch(ref, refresh=ctx.refresh)
                sections = adapter.parse(raw)
            except Exception as e:  # noqa: BLE001 - log + skip, don't crash the run (PRD §4.8)
                ctx.log.warning("ready_gov fetch/parse failed for %s: %s", ref.url, e)
                continue
            if not sections:
                ctx.log.info("no sections for %s", ref.url)
                continue

            is_pdf = ref.meta.get("kind") == "pdf"
            item_ctx = ItemContext(
                source=Source.ready_gov,
                source_id=ref.source_id,
                source_url=ref.url,
                title=ref.title,
                publisher=_PUBLISHER,
                attribution_text=_ATTRIBUTION,
                default_license=License.ready_gov_reprint if is_pdf else License.us_gov,
                default_audience=Audience(ref.meta.get("audience", "general")),
                adapter_name=adapter.name,
                extractor_name=self.use_case,
                use_case=UseCase.household_readiness,
                id_stem=ref.source_id,
                source_last_updated=ref.meta.get("source_last_updated"),
            )
            item_blocks = build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
            ctx.log.info("%s -> %d blocks (%s)", ref.source_id, len(item_blocks),
                         "cached" if raw.from_cache else "fetched")
            blocks.extend(item_blocks)
        return blocks
