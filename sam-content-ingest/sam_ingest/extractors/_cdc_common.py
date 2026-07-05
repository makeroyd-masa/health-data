"""Shared helpers for CDC-syndication-backed extractors (aging, travel)."""

from __future__ import annotations

import itertools

from ..adapters.cdc_syndication import CdcSyndicationAdapter
from ..core.pipeline import build_blocks
from ..core.schema import Audience, ItemContext, KnowledgeBlock, License, Source, UseCase, slugify
from .base import RunContext

_DEFAULT_PUBLISHER = "Centers for Disease Control and Prevention (CDC)"


def cdc_item_context(ref, *, use_case: UseCase, extractor: str,
                     audience: Audience) -> ItemContext:
    return ItemContext(
        source=Source.cdc,
        source_id=ref.source_id,
        source_url=ref.meta.get("source_page_url") or ref.url,
        title=ref.title,
        publisher=ref.meta.get("source_name") or _DEFAULT_PUBLISHER,
        attribution_text=(ref.meta.get("attribution")
                          or "Content syndicated from the CDC via HHS Digital Media."),
        default_license=License.us_gov,
        default_audience=audience,
        adapter_name="cdc",
        extractor_name=extractor,
        use_case=use_case,
        id_stem=slugify(ref.title) or ref.source_id,
        source_last_updated=ref.meta.get("source_last_updated"),
    )


def run_cdc_queries(ctx: RunContext, cdc_seed: dict, *, use_case: UseCase,
                    extractor: str, audience: Audience) -> list[KnowledgeBlock]:
    """Discover + ingest CDC syndication items. Gated: if the HHS storefront yields
    nothing (or is unreachable from this egress), logs it and returns []."""
    from ..core.schema import SeedConfig

    adapter = CdcSyndicationAdapter(ctx.client, base_url=cdc_seed.get(
        "base_url", "https://api.digitalmedia.hhs.gov/api/v2/resources"))
    seed = SeedConfig(use_case=extractor, raw=cdc_seed)
    try:
        discovered = adapter.discover(seed)
        refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)
    except Exception as e:  # noqa: BLE001 - unreachable host / bad response: gate, don't crash
        ctx.log.warning("CDC discovery failed (availability unconfirmed): %s", e)
        return []
    if not refs:
        ctx.log.info("CDC: no syndicated items for %s — confirm availability in the "
                     "HHS storefront before enabling", extractor)
        return []

    blocks: list[KnowledgeBlock] = []
    for ref in refs:
        try:
            sections = adapter.parse(adapter.fetch(ref, refresh=ctx.refresh))
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("CDC fetch/parse failed for %s: %s", ref.url, e)
            continue
        if not sections:
            continue
        item_ctx = cdc_item_context(ref, use_case=use_case, extractor=extractor, audience=audience)
        blocks += build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
    return blocks
