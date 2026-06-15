"""Persistent record of everything already downloaded.

Used to keep the library view clean: tracks that have already been pulled are
hidden by default so the user only ever sees what's left to grab.

The index is keyed by ``"<source>:<track id>"`` and stored as JSON under
``~/.sub_scraper/downloaded.json``. It is thread-safe (the engine records
completions from worker threads while the GUI reads it on the main thread) and
self-healing: an entry whose file has since been deleted is treated as
*not* downloaded and pruned, so removing a file un-hides it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .logging_config import get_logger, kv

if TYPE_CHECKING:
    from ..scrapers.base import Track

log = get_logger("index")

DEFAULT_INDEX_PATH = Path.home() / ".sub_scraper" / "downloaded.json"


def _key(source: str, track_id: str) -> str:
    return f"{source}:{track_id}"


class DownloadIndex:
    """Thread-safe, JSON-backed set of completed downloads."""

    def __init__(self, path: Path = DEFAULT_INDEX_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            if isinstance(data, dict):
                self._entries = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("index.load.failed " + kv(error=exc))
            self._entries = {}

    def _save_locked(self) -> None:
        """Atomically persist the index (temp file + replace) under the lock."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".idx_", suffix=".json")
            with os.fdopen(fd, "w") as fh:
                json.dump(self._entries, fh, indent=2)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("index.save.failed " + kv(error=exc))

    # ------------------------------------------------------------------
    # Queries / mutations
    # ------------------------------------------------------------------

    def contains(self, source: str, track_id: str) -> bool:
        """True if this track was downloaded *and* its file still exists.

        A missing file is pruned so the track reappears as available.
        """
        if not track_id:
            return False
        with self._lock:
            entry = self._entries.get(_key(source, track_id))
            if entry is None:
                return False
            path = entry.get("path")
            if path and not Path(path).exists():
                self._entries.pop(_key(source, track_id), None)
                self._save_locked()
                return False
            return True

    def record(self, source: str, track: "Track") -> None:
        """Mark a track as downloaded and persist."""
        if not track.id:
            return
        with self._lock:
            self._entries[_key(source, track.id)] = {
                "title": track.title,
                "artist": track.artist,
                "path": track.local_path or "",
                "size": int(track.size_bytes or 0),
                "sha256": track.checksum or "",
                "ts": int(time.time()),
            }
            self._save_locked()
        log.debug("index.record " + kv(source=source, track=track.id))

    def remove(self, source: str, track_id: str) -> None:
        with self._lock:
            if self._entries.pop(_key(source, track_id), None) is not None:
                self._save_locked()

    def clear(self) -> int:
        """Forget all download history (un-hides everything). Returns count cleared."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._save_locked()
        log.info("index.cleared " + kv(count=count))
        return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ------------------------------------------------------------------
    # Filesystem fallback (catches files grabbed before the index existed)
    # ------------------------------------------------------------------

    def file_exists_for(self, download_dir: str, track: "Track") -> Optional[str]:
        """Best-effort match of a track to an existing file in ``download_dir``.

        Downloaders name files ``"Artist - Title.ext"``; we compare on a
        normalised, case-folded stem so a previously grabbed file counts as
        already-downloaded even if it predates the index. Conservative by
        design — only an (almost) exact stem match counts.
        """
        directory = Path(download_dir)
        if not directory.is_dir():
            return None
        wanted = _normalise(f"{track.artist} - {track.title}")
        title_only = _normalise(track.title)
        for entry in directory.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            stem = _normalise(entry.stem)
            if stem == wanted or (len(title_only) > 4 and stem.endswith(title_only)):
                return str(entry)
        return None


def _normalise(text: str) -> str:
    """Lower-case, strip spacing/punctuation noise for tolerant filename matching."""
    keep = [ch.lower() for ch in text if ch.isalnum() or ch.isspace()]
    return " ".join("".join(keep).split())
