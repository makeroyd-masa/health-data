"""Travel health readiness extractor (PRD §6.6 + Travel Expansion §T4).

Composes source-keyed config sections into one travel_health corpus:
  cdc_prep         evergreen CDC prep pages
  cdc_destinations ~200 CDC country pages (periodic; geo=country from iso2)
  cdc_yellowbook   consumer-relevant trip-type chapters (periodic; trip_types)
  state_dept       advisories (volatile, RSS) + country_info (periodic)
  tsa_faa          medications/devices in transit (evergreen) + drug overlay
"""

from __future__ import annotations

from ..core.schema import Audience, KnowledgeBlock, UseCase, Volatility
from ._cdc_common import run_cdc_direct_pages
from .base import RunContext, register

# CDC direct-page sections and their volatility.
_CDC_SECTIONS = [
    ("cdc_prep", Volatility.evergreen),
    ("cdc_destinations", Volatility.periodic),
    ("cdc_yellowbook", Volatility.periodic),
]


@register("travel_health")
class TravelHealthExtractor:
    use_case = "travel_health"

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]:
        blocks: list[KnowledgeBlock] = []

        for key, volatility in _CDC_SECTIONS:
            section = ctx.seed.get(key)
            if not section:
                continue
            ctx.log.info("travel_health: ingesting %s (%s)", key, volatility.value)
            blocks += run_cdc_direct_pages(
                ctx, section, use_case=UseCase.travel_health, extractor=self.use_case,
                default_audience=Audience.general, volatility=volatility,
            )

        # State Dept (Phase D) and TSA/FAA (Phase E) sections are added by their modules.
        blocks += self._state_dept(ctx)
        blocks += self._tsa_faa(ctx)
        return blocks

    def _state_dept(self, ctx: RunContext) -> list[KnowledgeBlock]:
        section = ctx.seed.get("state_dept")
        if not section:
            return []
        from ._state_common import run_state_dept
        return run_state_dept(ctx, section, use_case=UseCase.travel_health,
                              extractor=self.use_case)

    def _tsa_faa(self, ctx: RunContext) -> list[KnowledgeBlock]:
        section = ctx.seed.get("tsa_faa")
        if not section:
            return []
        from ._gov_common import run_tsa_faa
        return run_tsa_faa(ctx, section, use_case=UseCase.travel_health,
                           extractor=self.use_case)
