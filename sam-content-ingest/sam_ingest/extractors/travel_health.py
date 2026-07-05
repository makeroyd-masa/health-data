"""Travel health readiness extractor (PRD §6.6).

CDC Travelers' Health via HHS syndication (gated on availability). Time-sensitive travel
*notices* are excluded by default. From an Akamai-blocked egress this yields nothing live
and is fixture-tested; run live from an unblocked network.
"""

from __future__ import annotations

from ..core.schema import Audience, KnowledgeBlock, UseCase
from ._cdc_common import run_cdc_queries
from .base import RunContext, register


@register("travel_health")
class TravelHealthExtractor:
    use_case = "travel_health"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        cdc_seed = ctx.seed.get("cdc")
        if not cdc_seed:
            ctx.log.info("travel_health: no cdc seed configured")
            return []
        if not cdc_seed.get("include_notices", False):
            ctx.log.info("travel_health: excluding time-sensitive travel notices (default)")
        return run_cdc_queries(ctx, cdc_seed, use_case=UseCase.travel_health,
                               extractor=self.use_case, audience=Audience.general)
