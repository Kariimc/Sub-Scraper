"""Async HTTP layer: a persistent, pooled :class:`aiohttp` session plus a
resilient chunked streaming downloader.

This is the network module for *direct* HTTP transfers. It provides:

* **Connection pooling** — one long-lived ``ClientSession`` backed by a tuned
  ``TCPConnector`` (bounded concurrency, keep-alive, DNS cache) so repeated
  requests reuse TCP/TLS connections instead of re-handshaking every time.
* **Chunked stream piping** — responses are streamed to disk in 64-256 KiB
  chunks via a context-managed file handle; whole files are never buffered in
  memory, so RAM stays flat regardless of file size.
* **Resilience** — per-attempt timeouts, jittered exponential backoff, optional
  circuit breaker, and ``Retry-After`` honouring for HTTP 429.
* **Integrity** — streamed SHA-256 plus a Content-Length size check, so a
  truncated transfer is caught instead of silently corrupting the library.

Platform extraction (Spotify/SoundCloud) still delegates to the battle-tested
yt-dlp / aria2c pipeline; this module handles direct media URLs and assets.

``aiohttp`` is imported lazily so the rest of the app runs even when it is not
installed — only direct-URL transfers require it.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .logging_config import get_logger, kv
from .resilience import CircuitBreaker, CircuitOpen, backoff_delay

try:  # optional dependency — only needed for direct-URL transfers
    import aiohttp
except ImportError:  # pragma: no cover - exercised only in minimal installs
    aiohttp = None  # type: ignore[assignment]

log = get_logger("net")

# 128 KiB sits in the middle of the recommended 64-256 KiB window: large enough
# to amortise syscall overhead, small enough to keep memory flat.
DEFAULT_CHUNK_BYTES = 1 << 17


def aiohttp_available() -> bool:
    return aiohttp is not None


class TransientHTTPError(Exception):
    """A retryable HTTP failure. Carries an optional server-advised delay."""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class DownloadResult:
    path: str
    size: int
    sha256: str


class HttpClient:
    """A persistent, pooled async HTTP client.

    Create one per process and reuse it; ``close()`` (or ``async with``) releases
    the underlying connector and all keep-alive sockets.
    """

    def __init__(
        self,
        *,
        limit: int = 16,
        limit_per_host: int = 8,
        timeout: float = 30.0,
        chunk_size: int = DEFAULT_CHUNK_BYTES,
        retry_limit: int = 3,
        user_agent: str = "Sub-Scraper/2.0 (+https://github.com)",
    ) -> None:
        if aiohttp is None:
            raise RuntimeError(
                "aiohttp is required for direct HTTP downloads. "
                "Install it with: pip install aiohttp"
            )
        self._limit = max(1, limit)
        self._limit_per_host = max(1, limit_per_host)
        self._timeout = max(1.0, timeout)
        self._chunk_size = max(1 << 14, chunk_size)  # >= 16 KiB
        self._retry_limit = max(1, retry_limit)
        self._user_agent = user_agent
        self._session: "Optional[aiohttp.ClientSession]" = None

    async def session(self) -> "aiohttp.ClientSession":
        """Lazily build (and memoise) the pooled session on the running loop."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=self._limit,
                limit_per_host=self._limit_per_host,
                ttl_dns_cache=300,
                keepalive_timeout=30.0,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(
                total=None,  # whole-file time is unbounded; per-op below guards stalls
                sock_connect=self._timeout,
                sock_read=self._timeout,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": self._user_agent},
                raise_for_status=False,
            )
            log.debug("net.session.open " + kv(limit=self._limit, per_host=self._limit_per_host))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            log.debug("net.session.close")
        self._session = None

    async def __aenter__(self) -> "HttpClient":
        await self.session()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def stream_download(
        self,
        url: str,
        dest: str | Path,
        *,
        expected_size: Optional[int] = None,
        breaker: Optional[CircuitBreaker] = None,
        headers: Optional[dict] = None,
        on_log=None,
    ) -> DownloadResult:
        """Stream ``url`` to ``dest`` in chunks, with retries and integrity checks.

        Writes to a ``.part`` sidecar and atomically renames on success, so a
        crash mid-transfer never leaves a half file masquerading as complete.
        Returns the final size and SHA-256.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._retry_limit + 1):
            if breaker is not None:
                breaker.before()  # raises CircuitOpen → caller fails fast
            try:
                result = await self._attempt(url, tmp, dest, expected_size, headers)
                if breaker is not None:
                    breaker.record(True)
                log.debug("net.download.ok " + kv(url=url, size=result.size))
                return result
            except CircuitOpen:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                last_exc = exc
                tripped = breaker.record(False) if breaker is not None else False
                tmp.unlink(missing_ok=True)
                if tripped:
                    log.warning("net.circuit.open " + kv(url=url))
                if attempt >= self._retry_limit:
                    break
                advised = getattr(exc, "retry_after", 0.0) or 0.0
                delay = advised if advised > 0 else backoff_delay(attempt, cap=self._timeout)
                msg = (
                    f"net.retry attempt={attempt}/{self._retry_limit} "
                    f"delay={delay:.1f}s url={url} error={exc}"
                )
                log.info(msg)
                if on_log:
                    on_log(f"[net] retry {attempt}/{self._retry_limit} in {delay:.1f}s ({exc})")
                await asyncio.sleep(delay)

        raise last_exc if last_exc else RuntimeError("download failed")

    async def _attempt(
        self,
        url: str,
        tmp: Path,
        dest: Path,
        expected_size: Optional[int],
        headers: Optional[dict],
    ) -> DownloadResult:
        session = await self.session()
        async with session.get(url, headers=headers) as resp:
            if resp.status == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                raise TransientHTTPError("429 Too Many Requests", retry_after=retry_after)
            if resp.status >= 500:
                raise TransientHTTPError(f"server error {resp.status}")
            if resp.status >= 400:
                # 4xx (other than 429) won't fix itself — fail without retrying.
                raise RuntimeError(f"HTTP {resp.status} for {url}")

            declared = expected_size or _content_length(resp.headers)
            digest = hashlib.sha256()
            written = 0
            # Context-managed handle: closed deterministically even on error.
            with open(tmp, "wb") as fh:
                async for chunk in resp.content.iter_chunked(self._chunk_size):
                    fh.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)

            if written == 0:
                raise TransientHTTPError("empty response body")
            if declared and written != declared:
                raise TransientHTTPError(
                    f"size mismatch: got {written} bytes, expected {declared}"
                )

        tmp.replace(dest)  # atomic on the same filesystem
        return DownloadResult(str(dest), written, digest.hexdigest())


def _content_length(headers) -> int:
    try:
        return int(headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_retry_after(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0
