"""Disk cache for raw HTTP responses (PRD §4.2).

Keyed by URL + params with a TTL. Re-runs read cache unless refreshed. Bulk/date-stamped
files should be cached with `key_override` (a stable content key) so a new calendar day
does not force a needless re-download of unchanged content.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode


class DiskCache:
    def __init__(self, root: str | Path, default_ttl: float = 24 * 3600):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl

    @staticmethod
    def _cache_key(url: str, params: dict | None, key_override: str | None) -> str:
        if key_override is not None:
            raw = key_override
        else:
            raw = url + ("?" + urlencode(sorted((params or {}).items())) if params else "")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.root / f"{key}.body", self.root / f"{key}.meta.json"

    def get(
        self,
        url: str,
        params: dict | None = None,
        ttl: float | None = None,
        key_override: str | None = None,
    ) -> bytes | None:
        key = self._cache_key(url, params, key_override)
        body_path, meta_path = self._paths(key)
        if not body_path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ttl = self.default_ttl if ttl is None else ttl
        if ttl >= 0 and (time.time() - meta.get("stored_at", 0)) > ttl:
            return None
        return body_path.read_bytes()

    def set(
        self,
        url: str,
        content: bytes,
        params: dict | None = None,
        content_type: str = "",
        key_override: str | None = None,
    ) -> None:
        key = self._cache_key(url, params, key_override)
        body_path, meta_path = self._paths(key)
        body_path.write_bytes(content)
        meta_path.write_text(
            json.dumps(
                {"url": url, "content_type": content_type, "stored_at": time.time()},
                indent=2,
            ),
            encoding="utf-8",
        )
