"""The SourceAdapter contract (PRD §3).

Adapters resolve seeds to refs, fetch raw content (through the shared cache + rate
limiter), and parse it into cleaned sections. Use-case extractors compose adapters;
adapters never talk to consumers and never emit boilerplate.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from ..core.http import PoliteClient
from ..core.schema import ParsedSection, RawItem, SeedConfig, SourceRef


class SourceAdapter(Protocol):
    name: str

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        """Resolve seeds into item refs. Adapters own their own pagination.

        For bulk-file sources (e.g. MedlinePlus bulk XML) discover() downloads and splits
        the bulk file into per-item refs; fetch() then reads from that local store.
        """
        ...

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        """Fetch raw content for one ref via the shared cache + rate limiter."""
        ...

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        """Return cleaned sections. License is per-section (varies within a source)."""
        ...


class BaseAdapter:
    """Convenience base holding the shared client."""

    name: str = "base"

    def __init__(self, client: PoliteClient):
        self.client = client
