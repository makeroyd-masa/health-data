"""Core pipeline: turn ParsedSections into KnowledgeBlocks (PRD §3/§4).

Adds ids, use_case tag, citation, license, hashes, provenance; dedupes within an item;
manages deterministic (change-driven) timestamps via RunState. Adapters and extractors
do not build KnowledgeBlocks directly — they hand sections + an ItemContext to here.
"""

from __future__ import annotations

import logging

from .. import PIPELINE_VERSION
from .chunk import make_summary
from .schema import (
    Citation,
    Codes,
    ItemContext,
    KnowledgeBlock,
    ParsedSection,
    Provenance,
    content_hash,
    slugify,
)
from .state import RunState

log = logging.getLogger("sam_ingest.pipeline")


def build_blocks(
    ctx: ItemContext,
    sections: list[ParsedSection],
    state: RunState,
    run_ts: str,
) -> list[KnowledgeBlock]:
    """Build validated KnowledgeBlocks for one source item.

    Dedupes sections with identical body within the item, assigns stable ids with
    collision suffixes, and sets `ingested_at`/`retrieved_at` to when content last
    changed (reused from state when unchanged) so re-runs are byte-identical.
    """
    id_stem = ctx.id_stem or slugify(ctx.title)
    blocks: list[KnowledgeBlock] = []
    seen_hashes: set[str] = set()
    used_ids: set[str] = set()

    for index, sec in enumerate(sections):
        body = sec.body_markdown.strip()
        if not body:
            continue
        chash = content_hash(body)
        if chash in seen_hashes:  # dedupe within (source, source_id) scope
            log.debug("dedupe: dropping duplicate section %r in %s", sec.section_title, ctx.source_id)
            continue
        seen_hashes.add(chash)

        section_label = sec.section_title.strip() or "Overview"
        section_slug = slugify(section_label) if sec.section_title.strip() else "overview"
        block_id = f"{ctx.source.value}:{id_stem}:{section_slug}"
        if block_id in used_ids:
            n = 2
            while f"{block_id}-{n}" in used_ids:
                n += 1
            block_id = f"{block_id}-{n}"
        used_ids.add(block_id)

        # Deterministic timestamps: reuse from state when content is unchanged.
        skey = RunState.key(ctx.source.value, ctx.source_id, index)
        prior = state.get(skey)
        if prior and prior.get("content_hash") == chash:
            ingested_at = prior.get("ingested_at", run_ts)
            retrieved_at = prior.get("retrieved_at", run_ts)
        else:
            ingested_at = retrieved_at = run_ts
        state.put(
            skey,
            {
                "content_hash": chash,
                "ingested_at": ingested_at,
                "retrieved_at": retrieved_at,
                "source_version": ctx.source_version,
                "block_id": block_id,
            },
        )

        codes = Codes(**{k: v for k, v in sec.codes.items() if v})
        block = KnowledgeBlock(
            id=block_id,
            use_case=ctx.use_case,
            source=ctx.source,
            source_id=ctx.source_id,
            source_url=ctx.source_url,
            language=ctx.language,
            title=ctx.title,
            section=section_label,
            audience=sec.audience or ctx.default_audience,
            summary=make_summary(body, section_label),
            body_markdown=body,
            keywords=sorted(set(sec.keywords)),
            codes=codes,
            citation=Citation(
                publisher=ctx.publisher,
                source_url=ctx.source_url,
                attribution_text=ctx.attribution_text,
                retrieved_at=retrieved_at,
            ),
            license=sec.license,
            source_last_updated=ctx.source_last_updated,
            ingested_at=ingested_at,
            content_hash=chash,
            provenance=Provenance(
                extractor=ctx.extractor_name,
                adapter=ctx.adapter_name,
                pipeline_version=PIPELINE_VERSION,
                source_version=ctx.source_version,
            ),
        )
        blocks.append(block)

    return blocks
