"""CDC direct-page adapter (PRD §6.5/§6.6).

CDC's evergreen STEADI/older-adult-fall-prevention and Travelers' Health prep content is
NOT in the HHS syndication catalog (see the discovery-spike finding), so we fetch the
specific public-domain CDC pages directly. Now a thin alias of the generalized
`GovPagesAdapter` (PRD §T4.4) — kept as its own class for the `cdc` source and its tests.
"""

from __future__ import annotations

from .gov_pages import GovPagesAdapter, _page_id  # noqa: F401 (re-export for callers/tests)


class CdcPagesAdapter(GovPagesAdapter):
    name = "cdc"
