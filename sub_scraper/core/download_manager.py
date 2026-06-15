from __future__ import annotations

import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable, Optional

from ..scrapers.base import DownloadStatus, Track

if TYPE_CHECKING:
    from ..scrapers.spotify import SpotifyScraper
    from ..scrapers.soundcloud import SoundCloudScraper
    from ..uploaders.gdrive import GDriveUploader


class CircuitOpen(Exception):
    """Raised when a source's circuit breaker is tripped (cooling down)."""


class _CircuitBreaker:
    """Trips after `threshold` consecutive failures for one source, then refuses
    work for `cooldown` seconds so we stop hammering a dead/rate-limited host."""

    def __init__(self, threshold: int = 6, cooldown: float = 30.0) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._fails = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if time.monotonic() < self._open_until:
                raise CircuitOpen("too many recent failures; cooling down")

    def record(self, ok: bool) -> bool:
        """Record an outcome. Returns True if this call tripped the breaker."""
        with self._lock:
            if ok:
                self._fails = 0
                return False
            self._fails += 1
            if self._fails >= self._threshold:
                self._open_until = time.monotonic() + self._cooldown
                self._fails = 0
                return True
            return False


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
    def __init__(self, max_workers: int = 6, retry_limit: int = 3) -> None:
        self._max_workers = max_workers
        self._retry_limit = max(1, retry_limit)
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._breakers: dict[str, _CircuitBreaker] = {}
        self._spotify: Optional[SpotifyScraper] = None
        self._soundcloud: Optional[SoundCloudScraper] = None
        self._gdrive: Optional[GDriveUploader] = None

    def configure_spotify(self, scraper: "SpotifyScraper") -> None:
        self._spotify = scraper

    def configure_soundcloud(self, scraper: "SoundCloudScraper") -> None:
        self._soundcloud = scraper

    def configure_gdrive(self, uploader: "GDriveUploader") -> None:
        self._gdrive = uploader

    def start(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

    def stop(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _breaker(self, source: str) -> _CircuitBreaker:
        with self._lock:
            b = self._breakers.get(source)
            if b is None:
                b = self._breakers[source] = _CircuitBreaker()
            return b

    @staticmethod
    def _log(job: DownloadJob, msg: str) -> None:
        if job.on_log:
            job.on_log(msg)

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Exponential (2,4,8,… capped at 30s) with full jitter.
        base = min(2 ** attempt, 30)
        return base * (0.5 + random.random() * 0.5)

    def _download_with_retry(self, job: DownloadJob) -> str:
        track = job.track
        breaker = self._breaker(job.source)
        scraper = self._spotify if job.source == "spotify" else self._soundcloud
        if scraper is None:
            raise RuntimeError(f"{job.source} scraper not configured")

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._retry_limit + 1):
            breaker.before()  # raises CircuitOpen when tripped → fail fast
            try:
                path = scraper.download(track, job.output_dir, job.quality, job.fmt, job.on_log)
                breaker.record(True)
                return path
            except Exception as exc:
                last_exc = exc
                if breaker.record(False):
                    self._log(job, f"[circuit] {job.source} paused 30s after repeated failures")
                if attempt >= self._retry_limit:
                    break
                delay = self._backoff(attempt)
                self._log(
                    job,
                    f"[retry] {track.display_name}: attempt {attempt}/{self._retry_limit} "
                    f"failed ({exc}); retrying in {delay:.1f}s",
                )
                time.sleep(delay)
        raise last_exc if last_exc else RuntimeError("download failed")

    def submit(self, job: DownloadJob) -> Future:
        if self._executor is None:
            self.start()

        def _run() -> None:
            track = job.track
            track.status = DownloadStatus.DOWNLOADING
            track.error = None
            if job.on_progress:
                job.on_progress(track)
            try:
                path = self._download_with_retry(job)
                track.local_path = path
                track.status = DownloadStatus.COMPLETE
                if self._gdrive:
                    self._gdrive.upload(path)
            except CircuitOpen as exc:
                track.status = DownloadStatus.FAILED
                track.error = str(exc)
                self._log(job, f"[skip] {track.display_name}: {exc}")
            except Exception as exc:
                track.status = DownloadStatus.FAILED
                track.error = str(exc)
                self._log(job, f"[error] {track.display_name}: {exc}")

            if job.on_progress:
                job.on_progress(track)

        with self._lock:
            future = self._executor.submit(_run)
            self._futures[job.track.id] = future
        return future

    def submit_batch(self, jobs: list[DownloadJob], chunk_size: int = 0) -> None:
        if not jobs:
            return

        if chunk_size <= 0 or chunk_size >= len(jobs):
            for job in jobs:
                self.submit(job)
            return

        def _chunked() -> None:
            for i in range(0, len(jobs), chunk_size):
                futures = [self.submit(j) for j in jobs[i : i + chunk_size]]
                for f in futures:
                    try:
                        f.result()
                    except Exception:
                        pass

        threading.Thread(target=_chunked, daemon=True).start()

    def cancel(self, track_id: str) -> None:
        with self._lock:
            f = self._futures.get(track_id)
            if f:
                f.cancel()

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures.values() if not f.done())
