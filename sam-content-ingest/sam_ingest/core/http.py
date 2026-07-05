"""Central polite HTTP client (PRD §4.3).

Per-host rate limits, exponential backoff with jitter, an identifying-but-browser-like
User-Agent, and integration with the on-disk cache. Adapters never construct their own
client — they receive this one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .cache import DiskCache

log = logging.getLogger("sam_ingest.http")

# Browser-like (many .gov / HHS hosts 403 generic fetchers) yet identifying, per PRD §4.3.
# We do NOT spoof to evade a robots prohibition — sources that disallow us are out of scope.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "SAM-Content-Ingest/1.1 (+mailto:makeroyd@masaglobal.com)"
)

# Minimum seconds between requests to a given host (PRD §4.3).
#   MedlinePlus: 85 req/min  -> 60/85 ≈ 0.706s
#   HHS Digital Media: 100 req / 60s -> 0.6s
#   Everything else: conservative 1 req/s default.
_HOST_MIN_INTERVAL: dict[str, float] = {
    "medlineplus.gov": 0.71,
    "wsearch.nlm.nih.gov": 0.71,
    "connect.medlineplus.gov": 0.71,
    "api.digitalmedia.hhs.gov": 0.6,
    "tools.cdc.gov": 0.6,
}
_DEFAULT_MIN_INTERVAL = 1.0


class RetryableStatus(Exception):
    pass


@dataclass
class Response:
    url: str
    content: bytes
    content_type: str
    from_cache: bool

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


class PoliteClient:
    def __init__(self, cache: DiskCache, timeout: float = 30.0):
        self.cache = cache
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self, host: str) -> None:
        interval = _HOST_MIN_INTERVAL.get(host, _DEFAULT_MIN_INTERVAL)
        last = self._last_request.get(host)
        if last is not None:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl: float | None = None,
        key_override: str | None = None,
        accept: str | None = None,
        refresh: bool = False,
    ) -> Response:
        """GET with cache + rate limiting. `refresh=True` bypasses the cache read."""
        if not refresh:
            cached = self.cache.get(url, params, ttl=ttl, key_override=key_override)
            if cached is not None:
                log.debug("cache hit %s", url)
                return Response(url, cached, "", from_cache=True)

        host = urlsplit(url).hostname or ""
        resp = self._fetch(url, params, host, accept)
        self.cache.set(
            url,
            resp.content,
            params=params,
            content_type=resp.headers.get("content-type", ""),
            key_override=key_override,
        )
        return Response(
            url, resp.content, resp.headers.get("content-type", ""), from_cache=False
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, RetryableStatus)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _fetch(
        self, url: str, params: dict | None, host: str, accept: str | None
    ) -> httpx.Response:
        self._throttle(host)
        headers = {"Accept": accept} if accept else None
        log.debug("GET %s params=%s", url, params)
        resp = self._client.get(url, params=params, headers=headers)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise RetryableStatus(f"{resp.status_code} for {url}")
        resp.raise_for_status()
        return resp
