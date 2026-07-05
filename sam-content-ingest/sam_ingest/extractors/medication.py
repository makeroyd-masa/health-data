"""Medication questions extractor (PRD §6.4). Source: DailyMed (SPL labels).

DailyMed labels are authoritative but clinical; many have no patient-facing section.
That gap is a prioritization signal (see the richness summary) — it's where licensed
consumer drug content (Mayo) would later improve readability. No MedlinePlus here: its
consumer drug info is the ASHP monographs excluded by §5.3.
"""

from __future__ import annotations

import itertools

from ..adapters.dailymed import DailyMedAdapter
from ..core.pipeline import build_blocks
from ..core.schema import Audience, ItemContext, KnowledgeBlock, License, Source, UseCase
from .base import RunContext, register

PUBLISHER = "DailyMed (U.S. National Library of Medicine)"
ATTRIBUTION = "Drug label information from DailyMed, U.S. National Library of Medicine."


@register("medication")
class MedicationExtractor:
    use_case = "medication"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        adapter = DailyMedAdapter(ctx.client)
        discovered = adapter.discover(ctx.seed)
        refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)

        blocks: list[KnowledgeBlock] = []
        for ref in refs:
            try:
                raw = adapter.fetch(ref, refresh=ctx.refresh)
                sections = adapter.parse(raw)
            except Exception as e:  # noqa: BLE001 - log + skip (PRD §4.8)
                ctx.log.warning("dailymed failed for %s: %s", ref.url, e)
                continue
            if not sections:
                ctx.log.info("no consumer sections for %s", ref.meta.get("drug_name"))
                continue

            item_ctx = ItemContext(
                source=Source.dailymed,
                source_id=ref.source_id,  # SETID
                source_url=f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={ref.source_id}",
                title=ref.title,
                publisher=PUBLISHER,
                attribution_text=ATTRIBUTION,
                default_license=License.public_domain,
                default_audience=Audience.patient,
                adapter_name=adapter.name,
                extractor_name=self.use_case,
                use_case=UseCase.medication,
                id_stem=(ref.meta.get("drug_name") or ref.title),
                source_version=ref.meta.get("spl_version"),
            )
            item_blocks = build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
            ctx.log.info("%s (v%s) -> %d blocks", ref.meta.get("drug_name"),
                         ref.meta.get("spl_version"), len(item_blocks))
            blocks.extend(item_blocks)
        return blocks
