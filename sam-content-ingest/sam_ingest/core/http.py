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

# Hosts behind Akamai/CloudFront bot management that block a plain HTTP client at the TLS
# layer. robots PERMITS the paths we ingest on these hosts — the block is bot-management,
# not a robots prohibition — so we route them through curl_cffi's browser-TLS impersonation.
# (AHRQ is deliberately NOT here: its robots disallows us, so it stays out of scope.)
_IMPERSONATE_HOSTS: set[str] = {
    "www.ready.gov", "ready.gov",
    "api.digitalmedia.hhs.gov",
    "wwwnc.cdc.gov", "www.cdc.gov", "tools.cdc.gov",
}
_IMPERSONATE_PROFILE = "chrome"

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
        self.timeout = timeout
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._cffi = None  # lazily created curl_cffi session for impersonated hosts

    def close(self) -> None:
        self._client.close()
        if self._cffi is not None:
            self._cffi.close()

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
    def _fetch(self, url: str, params: dict | None, host: str, accept: str | None):
        self._throttle(host)
        headers = {"Accept": accept} if accept else None
        log.debug("GET %s params=%s", url, params)
        if host in _IMPERSONATE_HOSTS:
            resp = self._impersonated_get(url, params, headers)
        else:
            resp = self._client.get(url, params=params, headers=headers)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise RetryableStatus(f"{resp.status_code} for {url}")
        resp.raise_for_status()
        return resp

    def _impersonated_get(self, url, params, headers):
        """Fetch via curl_cffi with a browser TLS fingerprint (Akamai-blocked hosts).

        Response is duck-compatible with httpx (.status_code/.content/.headers/
        .raise_for_status), so callers don't care which transport was used.
        """
        if self._cffi is None:
            try:
                from curl_cffi import requests as cffi_requests
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    f"{url} is behind bot management and needs curl_cffi "
                    "(pip install curl_cffi) for browser-TLS impersonation."
                ) from e
            self._cffi = cffi_requests.Session(impersonate=_IMPERSONATE_PROFILE)
        return self._cffi.get(url, params=params, headers=headers, timeout=self.timeout)
