"""Incremental run state (PRD §4.4/§4.5).

Persists, per (source, source_id, section_index), the last content_hash and the
timestamps of when the content last CHANGED. This is what lets re-runs reuse timestamps
so output is byte-identical, and lets adapters skip unchanged items.
"""

from __future__ import annotations

import json
from pathlib import Path


class RunState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def key(source: str, source_id: str, section_index: int) -> str:
        return f"{source}:{source_id}:{section_index}"

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, record: dict) -> None:
        self._data[key] = record

    def item_version(self, source: str, source_id: str) -> str | None:
        """Highest recorded source_version for an item (for pre-fetch change detection)."""
        prefix = f"{source}:{source_id}:"
        versions = [
            v.get("source_version")
            for k, v in self._data.items()
            if k.startswith(prefix) and v.get("source_version")
        ]
        return versions[0] if versions else None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
        )
