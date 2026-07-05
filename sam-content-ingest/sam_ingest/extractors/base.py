"""Extractor contract, run context, and registry.

An extractor composes one or more adapters for a single use case and returns validated
KnowledgeBlocks. Extractors register themselves via @register so the CLI can dispatch
by use-case name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..core.http import PoliteClient
from ..core.schema import KnowledgeBlock, SeedConfig
from ..core.state import RunState


@dataclass
class RunContext:
    client: PoliteClient
    seed: SeedConfig
    state: RunState
    run_ts: str
    config_dir: Path
    limit: int | None = None
    refresh: bool = False
    dry_run: bool = False
    log: logging.Logger = logging.getLogger("sam_ingest.extractor")


class Extractor(Protocol):
    use_case: str

    def run(self, ctx: RunContext) -> list[KnowledgeBlock]: ...


_REGISTRY: dict[str, Callable[[], Extractor]] = {}


def register(use_case: str) -> Callable:
    def deco(cls):
        _REGISTRY[use_case] = cls
        cls.use_case = use_case
        return cls
    return deco


def get_extractor(use_case: str) -> Extractor | None:
    factory = _REGISTRY.get(use_case)
    return factory() if factory else None


def registered_use_cases() -> list[str]:
    return sorted(_REGISTRY)
