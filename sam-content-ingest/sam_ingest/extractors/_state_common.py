"""Orchestration for the State Dept stream (advisories + country info)."""

from __future__ import annotations

import itertools

from ..adapters.state_gov import StateGovAdapter, advisory_valid_until
from ..core.pipeline import build_blocks
from ..core.schema import (
    Audience,
    GeoScope,
    ItemContext,
    KnowledgeBlock,
    License,
    SeedConfig,
    Source,
    UseCase,
    Volatility,
    slugify,
)
from .base import RunContext

_PUBLISHER = "U.S. Department of State"
_ATTRIBUTION = "Content from the U.S. Department of State (travel.state.gov)."


def run_state_dept(ctx: RunContext, section: dict, *, use_case: UseCase,
                   extractor: str) -> list[KnowledgeBlock]:
    adapter = StateGovAdapter(ctx.client)
    ttl_days = int(section.get("advisory_ttl_days", 180))
    seed = SeedConfig(use_case=extractor, raw=section)
    discovered = adapter.discover(seed)
    refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)

    blocks: list[KnowledgeBlock] = []
    adv = info = 0
    for ref in refs:
        kind = ref.meta.get("kind")
        try:
            sections = adapter.parse(adapter.fetch(ref, refresh=ctx.refresh))
        except Exception as e:  # noqa: BLE001 - log + skip (PRD §4.8)
            ctx.log.warning("state_dept %s fetch/parse failed for %s: %s", kind, ref.url, e)
            continue
        if not sections:
            ctx.log.info("SKIP (no content) %s", ref.url)
            continue

        geo = GeoScope(scope="country", country_iso2=ref.meta["iso2"],
                       country_name=ref.meta.get("country"))
        if kind == "advisory":
            volatility = Volatility.volatile
            valid_until = advisory_valid_until(ref.meta.get("pub_date"), ttl_days)
            if not valid_until:
                ctx.log.warning("advisory %s missing pubDate — skipping (needs valid_until)",
                                ref.source_id)
                continue
        else:
            volatility, valid_until = Volatility.periodic, None

        item_ctx = ItemContext(
            source=Source.state_dept,
            source_id=ref.source_id,
            source_url=ref.url,
            title=ref.title,
            publisher=_PUBLISHER,
            attribution_text=_ATTRIBUTION,
            default_license=License.us_gov,
            default_audience=Audience.general,
            adapter_name="state_gov",
            extractor_name=extractor,
            use_case=use_case,
            id_stem=slugify(ref.title),
            source_last_updated=(ref.meta.get("pub_date") if kind == "advisory" else None),
            geo=geo,
            volatility=volatility,
            valid_until=valid_until,
        )
        item_blocks = build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
        blocks.extend(item_blocks)
        adv += kind == "advisory"
        info += kind == "country_info"
    ctx.log.info("state_dept: %d advisory + %d country-info items ingested", adv, info)
    return blocks
