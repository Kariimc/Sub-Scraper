from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable, Optional

from ..scrapers.base import DownloadStatus, Track

if TYPE_CHECKING:
    from ..scrapers.spotify import SpotifyScraper
    from ..scrapers.soundcloud import SoundCloudScraper
    from ..uploaders.gdrive import GDriveUploader


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
    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
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

    def submit(self, job: DownloadJob) -> Future:
        if self._executor is None:
            self.start()

        def _run() -> None:
            track = job.track
            track.status = DownloadStatus.DOWNLOADING
            if job.on_progress:
                job.on_progress(track)
            try:
                if job.source == "spotify":
                    if self._spotify is None:
                        raise RuntimeError("Spotify scraper not configured")
                    path = self._spotify.download(track, job.output_dir, job.quality, job.fmt, job.on_log)
                else:
                    if self._soundcloud is None:
                        raise RuntimeError("SoundCloud scraper not configured")
                    path = self._soundcloud.download(track, job.output_dir, job.quality, job.fmt, job.on_log)

                track.local_path = path
                track.status = DownloadStatus.COMPLETE

                if self._gdrive:
                    self._gdrive.upload(path)

            except Exception as exc:
                track.status = DownloadStatus.FAILED
                track.error = str(exc)

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
