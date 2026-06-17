"""Background playlist auto-sync.

A user can mark playlists to "keep in sync": on a fixed interval the scheduler
re-fetches each one, figures out which tracks aren't on disk yet, and queues
just those for download. This turns Sub-Scraper from a one-shot grab into a
background library agent.

Design notes
------------
* The scheduler is a single daemon thread that sleeps in short steps so it can
  stop promptly. Network fetches happen on this thread; the actual downloads are
  handed to the existing :class:`DownloadManager`, so concurrency/resilience are
  unchanged.
* Synced playlists are persisted in :class:`Config` (``autosync``) keyed by
  ``"<source>:<playlist_id>"`` so they survive restarts.
* Everything is best-effort: a failing fetch logs and is retried next cycle; it
  never tears the thread (or the app) down.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from .logging_config import get_logger, kv
from ..scrapers.factory import build_scraper

if TYPE_CHECKING:
    from .config import Config
    from .download_manager import DownloadManager
    from .library_index import DownloadIndex

log = get_logger("autosync")

# Delay before the first sweep so launch isn't slowed by network I/O.
_INITIAL_DELAY = 25.0
# Granularity of the sleep loop (keeps stop() responsive).
_TICK = 5.0


def sync_key(source: str, playlist_id: str) -> str:
    return f"{source}:{playlist_id}"


@dataclass(frozen=True)
class SyncEntry:
    source: str
    playlist_id: str
    name: str

    @property
    def key(self) -> str:
        return sync_key(self.source, self.playlist_id)


class AutoSyncManager:
    def __init__(
        self,
        config: "Config",
        manager: "DownloadManager",
        index: "DownloadIndex",
        *,
        on_log: Optional[Callable[[str], None]] = None,
        on_synced: Optional[Callable[[SyncEntry, int], None]] = None,
        scraper_factory: Callable = build_scraper,
    ) -> None:
        self._config = config
        self._manager = manager
        self._index = index
        self._on_log = on_log
        # on_synced(entry, new_count) fires after a playlist sync queues work.
        self._on_synced = on_synced
        # Injectable for tests; defaults to the real config-driven factory.
        self._scraper_factory = scraper_factory

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._last_sync: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Persistence-backed membership
    # ------------------------------------------------------------------

    def _store(self) -> dict:
        store = getattr(self._config, "autosync", None)
        if not isinstance(store, dict):
            store = {}
            self._config.autosync = store
        return store

    def entries(self) -> list[SyncEntry]:
        out: list[SyncEntry] = []
        for rec in self._store().values():
            if isinstance(rec, dict) and rec.get("playlist_id") and rec.get("source"):
                out.append(SyncEntry(rec["source"], rec["playlist_id"], rec.get("name", "")))
        return out

    def is_synced(self, source: str, playlist_id: str) -> bool:
        return sync_key(source, playlist_id) in self._store()

    def add(self, source: str, playlist_id: str, name: str) -> None:
        with self._lock:
            self._store()[sync_key(source, playlist_id)] = {
                "source": source, "playlist_id": playlist_id, "name": name,
            }
            self._config.save()
        log.info("autosync.add " + kv(source=source, name=name))
        self._wake.set()  # sweep the new entry promptly

    def remove(self, source: str, playlist_id: str) -> None:
        with self._lock:
            if self._store().pop(sync_key(source, playlist_id), None) is not None:
                self._config.save()
        self._last_sync.pop(sync_key(source, playlist_id), None)
        log.info("autosync.remove " + kv(source=source, id=playlist_id))

    def interval_seconds(self) -> float:
        try:
            hours = float(getattr(self._config, "autosync_interval_hours", 6.0))
        except (TypeError, ValueError):
            hours = 6.0
        return max(0.1, hours) * 3600.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autosync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _loop(self) -> None:
        # Initial grace period (interruptible by stop() or a fresh add()).
        self._wake.wait(_INITIAL_DELAY)
        self._wake.clear()
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception as exc:  # noqa: BLE001 - never kill the thread
                log.warning("autosync.sweep.error " + kv(error=exc))
            # Sleep until the next tick or an explicit wake.
            self._wake.wait(_TICK)
            self._wake.clear()

    def _sweep(self) -> None:
        interval = self.interval_seconds()
        now = time.time()
        for entry in self.entries():
            if self._stop.is_set():
                return
            last = self._last_sync.get(entry.key, 0.0)
            if now - last < interval:
                continue
            self._last_sync[entry.key] = now
            try:
                self.sync_now(entry)
            except Exception as exc:  # noqa: BLE001
                log.warning("autosync.playlist.error " + kv(name=entry.name, error=exc))
                self._log(f"[auto-sync] '{entry.name}' failed: {exc}")

    # ------------------------------------------------------------------
    # Syncing
    # ------------------------------------------------------------------

    def sync_now(self, entry: SyncEntry) -> int:
        """Fetch ``entry``'s tracks, queue the ones not yet on disk. Returns the
        number of new downloads queued."""
        from .download_manager import DownloadJob  # local import avoids a cycle

        scraper = self._scraper_factory(self._config, entry.source)
        if entry.source == "spotify":
            self._manager.configure_spotify(scraper)
        else:
            self._manager.configure_soundcloud(scraper)

        tracks = scraper.fetch_playlist_tracks(entry.playlist_id)
        new_jobs: list[DownloadJob] = []
        for t in tracks:
            if self._index.contains(entry.source, t.id):
                continue
            # Catch files grabbed before the index existed.
            existing = self._index.file_exists_for(self._config.download_path, t)
            if existing:
                t.local_path = existing
                self._index.record(entry.source, t)
                continue
            new_jobs.append(DownloadJob(
                track=t,
                source=entry.source,
                output_dir=self._config.download_path,
                quality=self._config.audio_quality,
                fmt=self._config.output_format,
                on_log=self._on_log,
            ))

        if new_jobs:
            log.info("autosync.queue " + kv(name=entry.name, new=len(new_jobs)))
            self._log(f"[auto-sync] '{entry.name}': queuing {len(new_jobs)} new track(s)")
            self._manager.submit_batch(new_jobs)
        if self._on_synced is not None:
            self._on_synced(entry, len(new_jobs))
        return len(new_jobs)

    def _log(self, msg: str) -> None:
        if self._on_log:
            self._on_log(msg)
