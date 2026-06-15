"""Asynchronous, resilient download engine.

Architecture
------------
A single ``asyncio`` event loop runs on a dedicated daemon thread so it lives
happily alongside Tkinter's own loop. Work is submitted from the GUI thread via
``asyncio.run_coroutine_threadsafe`` and supervised entirely on the loop:

* **Concurrency control** — an ``asyncio.Semaphore`` caps in-flight downloads at
  ``MAX_CONCURRENT_DOWNLOADS``; everything else queues without spawning threads.
* **Non-blocking I/O** — subprocess downloaders (yt-dlp / spotdl) are spawned
  with ``create_subprocess_exec`` and their stdout is streamed asynchronously,
  so one loop thread supervises many downloads. Direct media URLs are streamed
  with the pooled :class:`HttpClient` instead.
* **Resilience** — jittered exponential backoff on failure, plus a per-source
  circuit breaker that pauses a flaky origin instead of hammering it.
* **Integrity** — every completed file is size/checksum-verified before it is
  marked done and recorded in the download index.

Progress and logs are delivered through the same callbacks the GUI already
marshals onto its main thread, so worker code never touches widgets directly.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from .config import Config
from .library_index import DownloadIndex
from .logging_config import get_logger, kv
from .net import DEFAULT_CHUNK_BYTES, HttpClient, aiohttp_available
from .resilience import CircuitBreaker, CircuitOpen, backoff_delay
from ..scrapers.base import DownloadStatus, Track, run_isolated_download_async

if TYPE_CHECKING:
    from ..scrapers.spotify import SpotifyScraper
    from ..scrapers.soundcloud import SoundCloudScraper
    from ..uploaders.gdrive import GDriveUploader

log = get_logger("engine")

_MEDIA_EXTS = (".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac")


def _is_direct_media(url: str) -> bool:
    """True when a URL points straight at a downloadable media file (so we can
    stream it ourselves) rather than at a platform page yt-dlp must extract."""
    if not url:
        return False
    path = url.split("?", 1)[0].lower()
    return path.startswith("http") and path.endswith(_MEDIA_EXTS)


def _safe_filename(track: Track, fmt: str) -> str:
    raw = f"{track.artist} - {track.title}".strip(" -") or (track.id or "track")
    cleaned = "".join(c for c in raw if c.isalnum() or c in " ._-()&,'").strip()
    return f"{cleaned or 'track'}.{fmt.lstrip('.')}"


class DownloadJob:
    __slots__ = ("track", "source", "output_dir", "quality", "fmt", "on_progress", "on_log")

    def __init__(
        self,
        track: Track,
        source: str,
        output_dir: str,
        quality: str,
        fmt: str,
        on_progress: Optional[Callable[[Track], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.track = track
        self.source = source
        self.output_dir = output_dir
        self.quality = quality
        self.fmt = fmt
        self.on_progress = on_progress
        self.on_log = on_log


class DownloadManager:
    """Public, thread-safe handle to the async engine."""

    def __init__(
        self,
        max_workers: int = 6,
        retry_limit: int = 3,
        *,
        breaker_threshold: int = 6,
        breaker_cooldown: float = 30.0,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        io_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        request_timeout: float = 30.0,
        verify: bool = True,
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._retry_limit = max(1, retry_limit)
        self._breaker_threshold = max(1, breaker_threshold)
        self._breaker_cooldown = max(0.0, breaker_cooldown)
        self._retry_base = max(0.0, retry_base_delay)
        self._retry_cap = max(self._retry_base, retry_max_delay)
        self._io_chunk = io_chunk_bytes
        self._timeout = request_timeout
        self._verify = verify

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._http: Optional[HttpClient] = None

        self._lock = threading.Lock()
        self._futures: dict[str, Future] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

        self._spotify: Optional[SpotifyScraper] = None
        self._soundcloud: Optional[SoundCloudScraper] = None
        self._gdrive: Optional[GDriveUploader] = None
        self._index: Optional[DownloadIndex] = None

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Config) -> "DownloadManager":
        def _i(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _f(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        return cls(
            max_workers=_i(config.max_concurrent, 6),
            retry_limit=_i(config.retry_limit, 3),
            breaker_threshold=_i(config.breaker_threshold, 6),
            breaker_cooldown=_f(config.breaker_cooldown, 30.0),
            retry_base_delay=_f(config.retry_base_delay, 1.0),
            retry_max_delay=_f(config.retry_max_delay, 30.0),
            io_chunk_bytes=_i(config.io_chunk_bytes, DEFAULT_CHUNK_BYTES),
            request_timeout=_f(config.request_timeout, 30.0),
            verify=bool(config.verify_downloads),
        )

    def configure_spotify(self, scraper: "SpotifyScraper") -> None:
        self._spotify = scraper

    def configure_soundcloud(self, scraper: "SoundCloudScraper") -> None:
        self._soundcloud = scraper

    def configure_gdrive(self, uploader: "GDriveUploader") -> None:
        self._gdrive = uploader

    def configure_index(self, index: DownloadIndex) -> None:
        self._index = index

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="dl-engine", daemon=True)
        self._thread.start()
        # Build loop-affine objects (semaphore) on the loop and wait for it.
        asyncio.run_coroutine_threadsafe(self._setup(), self._loop).result(timeout=10)
        log.info("engine.start " + kv(max_concurrent=self._max_workers, retries=self._retry_limit))

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _setup(self) -> None:
        self._sem = asyncio.Semaphore(self._max_workers)

    def stop(self) -> None:
        if self._loop is None:
            return
        with self._lock:
            for fut in self._futures.values():
                fut.cancel()

        async def _shutdown() -> None:
            if self._http is not None:
                await self._http.close()

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._sem = None
        self._http = None
        log.info("engine.stop")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _breaker(self, source: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(source)
            if breaker is None:
                breaker = self._breakers[source] = CircuitBreaker(
                    self._breaker_threshold, self._breaker_cooldown, name=source
                )
            return breaker

    async def _get_http(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient(
                limit=max(self._max_workers, 8),
                limit_per_host=max(self._max_workers, 4),
                timeout=self._timeout,
                chunk_size=self._io_chunk,
                retry_limit=self._retry_limit,
            )
        return self._http

    @staticmethod
    def _log(job: DownloadJob, msg: str) -> None:
        if job.on_log:
            job.on_log(msg)

    async def _process(self, job: DownloadJob) -> None:
        track = job.track
        track.status = DownloadStatus.DOWNLOADING
        track.error = None
        if job.on_progress:
            job.on_progress(track)

        try:
            assert self._sem is not None
            async with self._sem:  # concurrency gate
                path = await self._download_with_retry(job)
            track.local_path = path
            track.status = DownloadStatus.COMPLETE
            log.info("download.complete " + kv(
                track=track.id, source=job.source,
                size=track.size_bytes, sha256=(track.checksum or "")[:12],
            ))
            if self._index is not None:
                self._index.record(job.source, track)
            if self._gdrive is not None:
                # Drive client is sync + not thread-safe internally; offload it.
                assert self._loop is not None
                await self._loop.run_in_executor(None, self._gdrive.upload, path)
        except asyncio.CancelledError:
            track.status = DownloadStatus.FAILED
            track.error = "cancelled"
            log.info("download.cancelled " + kv(track=track.id))
        except CircuitOpen as exc:
            track.status = DownloadStatus.FAILED
            track.error = str(exc)
            self._log(job, f"[skip] {track.display_name}: {exc}")
            log.warning("download.skipped " + kv(track=track.id, reason="circuit_open"))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            track.status = DownloadStatus.FAILED
            track.error = str(exc)
            self._log(job, f"[error] {track.display_name}: {exc}")
            log.error("download.failed " + kv(track=track.id, error=exc))
        finally:
            if job.on_progress:
                job.on_progress(track)

    async def _download_with_retry(self, job: DownloadJob) -> str:
        breaker = self._breaker(job.source)
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._retry_limit + 1):
            breaker.before()  # raises CircuitOpen when tripped → fail fast
            try:
                path = await self._download_once(job)
                breaker.record(True)
                return path
            except CircuitOpen:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified/retried
                last_exc = exc
                if breaker.record(False):
                    self._log(job, f"[circuit] {job.source} paused "
                                   f"{self._breaker_cooldown:.0f}s after repeated failures")
                if attempt >= self._retry_limit:
                    break
                delay = backoff_delay(attempt, base=self._retry_base, cap=self._retry_cap)
                self._log(
                    job,
                    f"[retry] {job.track.display_name}: attempt {attempt}/{self._retry_limit} "
                    f"failed ({exc}); retrying in {delay:.1f}s",
                )
                log.info("download.retry " + kv(
                    track=job.track.id, attempt=attempt, delay=round(delay, 1),
                ))
                await asyncio.sleep(delay)
        raise last_exc if last_exc else RuntimeError("download failed")

    async def _download_once(self, job: DownloadJob) -> str:
        track = job.track
        # Fast path: a direct media URL is streamed by our own pooled client.
        if _is_direct_media(track.url):
            if not aiohttp_available():
                raise RuntimeError("aiohttp not installed; cannot stream direct URL")
            http = await self._get_http()
            dest = Path(job.output_dir) / _safe_filename(track, job.fmt)
            result = await http.stream_download(
                track.url, dest,
                breaker=self._breaker(job.source),
                on_log=job.on_log,
            )
            track.size_bytes = result.size
            track.checksum = result.sha256
            return result.path

        # Platform path: delegate extraction to yt-dlp / spotdl (async subprocess).
        scraper = self._spotify if job.source == "spotify" else self._soundcloud
        if scraper is None:
            raise RuntimeError(f"{job.source} scraper not configured")
        build_cmd = scraper.download_command(track, job.output_dir, job.quality, job.fmt)
        return await run_isolated_download_async(
            build_cmd, job.output_dir, track, scraper.log_prefix, job.on_log,
            verify=self._verify,
        )

    # ------------------------------------------------------------------
    # Public submission API
    # ------------------------------------------------------------------

    def _prune_done(self) -> None:
        for tid in [t for t, f in self._futures.items() if f.done()]:
            self._futures.pop(tid, None)

    def submit(self, job: DownloadJob) -> Future:
        if self._loop is None:
            self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._process(job), self._loop)
        with self._lock:
            self._prune_done()
            self._futures[job.track.id] = future
        return future

    def submit_batch(self, jobs: list[DownloadJob], chunk_size: int = 0) -> None:
        # The semaphore already bounds concurrency, so we can hand the whole
        # batch to the loop at once; `chunk_size` is accepted for compatibility.
        for job in jobs:
            self.submit(job)

    def cancel(self, track_id: str) -> None:
        with self._lock:
            future = self._futures.get(track_id)
        if future is not None:
            future.cancel()

    @property
    def active_count(self) -> int:
        with self._lock:
            self._prune_done()
            return sum(1 for f in self._futures.values() if not f.done())
