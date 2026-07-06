"""TSA/FAA medications-in-transit stream (PRD §T4.4).

Scraped general rules from TSA/FAA pages (via the generalized GovPagesAdapter) plus a small
hand-authored drug-specific overlay that links to the DailyMed corpus by codes.rxcui —
without copying any DailyMed body text.
"""

from __future__ import annotations

import itertools

from ..adapters.gov_pages import GovPagesAdapter
from ..core.pipeline import build_blocks
from ..core.schema import (
    Audience,
    ItemContext,
    KnowledgeBlock,
    License,
    ParsedSection,
    SeedConfig,
    Source,
    TripType,
    UseCase,
    Volatility,
    slugify,
)
from .base import RunContext

_SOURCE_META = {
    "tsa": (Source.tsa, "U.S. Transportation Security Administration (TSA)",
            "Content from the U.S. Transportation Security Administration (TSA)."),
    "faa": (Source.faa, "U.S. Federal Aviation Administration (FAA)",
            "Content from the U.S. Federal Aviation Administration (FAA)."),
}


def run_tsa_faa(ctx: RunContext, section: dict, *, use_case: UseCase,
                extractor: str) -> list[KnowledgeBlock]:
    blocks: list[KnowledgeBlock] = []
    blocks += _scraped_pages(ctx, section.get("direct_pages", []), use_case, extractor)
    blocks += _drug_overlay(ctx, section.get("drug_overlay", []), use_case, extractor)
    return blocks


def _ctx_for(src_key: str, *, source_id, source_url, title, use_case, extractor,
             rxcui=None) -> ItemContext:
    source, publisher, attribution = _SOURCE_META.get(src_key, _SOURCE_META["tsa"])
    return ItemContext(
        source=source, source_id=source_id, source_url=source_url, title=title,
        publisher=publisher, attribution_text=attribution,
        default_license=License.us_gov, default_audience=Audience.general,
        adapter_name=("gov_pages" if rxcui is None else "authored"),
        extractor_name=extractor, use_case=use_case, id_stem=slugify(title),
        trip_types=[TripType.chronic_condition], volatility=Volatility.evergreen,
    )


def _scraped_pages(ctx, pages, use_case, extractor) -> list[KnowledgeBlock]:
    if not pages:
        return []
    adapter = GovPagesAdapter(ctx.client)
    seed = SeedConfig(use_case=extractor, raw={"direct_pages": pages})
    discovered = adapter.discover(seed)
    refs = list(itertools.islice(discovered, ctx.limit) if ctx.limit else discovered)
    out: list[KnowledgeBlock] = []
    for ref in refs:
        try:
            sections = adapter.parse(adapter.fetch(ref, refresh=ctx.refresh))
        except Exception as e:  # noqa: BLE001 - log + skip (PRD §4.8)
            ctx.log.warning("tsa/faa fetch/parse failed for %s: %s", ref.url, e)
            continue
        if not sections:
            ctx.log.info("SKIP (no content) %s", ref.url)
            continue
        item_ctx = _ctx_for(ref.meta.get("source") or "tsa", source_id=ref.source_id,
                            source_url=ref.url, title=ref.title or ref.source_id,
                            use_case=use_case, extractor=extractor)
        out += build_blocks(item_ctx, sections, ctx.state, ctx.run_ts)
        ctx.log.info("tsa/faa page %s -> blocks", ref.source_id)
    return out


def _drug_overlay(ctx, overlay, use_case, extractor) -> list[KnowledgeBlock]:
    """Authored medication-in-transit blocks, joined to DailyMed by codes.rxcui."""
    out: list[KnowledgeBlock] = []
    for entry in overlay:
        body = (entry.get("body") or "").strip()
        rxcui = str(entry["rxcui"]) if entry.get("rxcui") else None
        if not body:
            continue
        item_ctx = _ctx_for(entry.get("source", "tsa"), source_id=entry["id"],
                            source_url=entry.get("source_url", ""), title=entry["title"],
                            use_case=use_case, extractor=extractor, rxcui=rxcui)
        sec = ParsedSection(
            section_title=entry.get("section", entry["title"]),
            body_markdown=body, license=License.us_gov,
            keywords=entry.get("keywords", ["travel", "medications"]),
            codes={"rxcui": [rxcui]} if rxcui else {},
        )
        out += build_blocks(item_ctx, [sec], ctx.state, ctx.run_ts)
    ctx.log.info("tsa/faa drug-overlay -> %d blocks", len(out))
    return out
