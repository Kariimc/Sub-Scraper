"""Play short (~30s) audio previews via whatever player is on the system.

We don't bundle an audio library — instead we shell out to the first available
of ffplay (ships with the ffmpeg the app already requires), mpv, mpg123, or
afplay. Players are fed the preview URL directly (ffplay/mpv stream HTTP fine),
so there's no temp file to manage.

Only one preview plays at a time. A watcher thread notices when playback ends
naturally and fires ``on_finished(url)`` so the UI can reset its play button;
the caller is responsible for marshalling that callback onto the main thread.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Callable, Optional

from ..core.logging_config import get_logger

log = get_logger("preview")

# (binary, argv-template) in preference order. ffplay first: ffmpeg is already a
# hard dependency, so it's almost always present.
_PLAYERS = [
    ("ffplay", lambda url: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", url]),
    ("mpv", lambda url: ["mpv", "--no-video", "--really-quiet", url]),
    ("mpg123", lambda url: ["mpg123", "-q", url]),
    ("afplay", lambda url: ["afplay", url]),
]


def _find_player():
    for name, builder in _PLAYERS:
        if shutil.which(name):
            return name, builder
    return None, None


class PreviewPlayer:
    """Single-stream preview playback with natural-end notification."""

    def __init__(self, on_finished: Optional[Callable[[str], None]] = None) -> None:
        self._on_finished = on_finished
        self._name, self._builder = _find_player()
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._url: Optional[str] = None
        self._gen = 0

    def available(self) -> bool:
        return self._builder is not None

    @property
    def player_name(self) -> Optional[str]:
        return self._name

    def playing_url(self) -> Optional[str]:
        with self._lock:
            return self._url

    def toggle(self, url: str) -> bool:
        """Start ``url`` (stopping anything else) or stop it if it's already the
        one playing. Returns True if ``url`` is now playing."""
        if not url or not self.available():
            return False
        with self._lock:
            if self._url == url and self._proc is not None and self._proc.poll() is None:
                self._stop_locked()
                return False
            self._stop_locked()
            return self._play_locked(url)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    # ------------------------------------------------------------------

    def _play_locked(self, url: str) -> bool:
        try:
            self._proc = subprocess.Popen(
                self._builder(url),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001 - playback is non-essential
            log.debug(f"preview.spawn.failed error={exc}")
            self._proc = None
            self._url = None
            return False
        self._url = url
        self._gen += 1
        gen = self._gen
        proc = self._proc
        threading.Thread(
            target=self._watch, args=(proc, url, gen), daemon=True,
        ).start()
        return True

    def _stop_locked(self) -> None:
        # Invalidate any running watcher so a kill doesn't fire on_finished.
        self._gen += 1
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self._url = None

    def _watch(self, proc: subprocess.Popen, url: str, gen: int) -> None:
        try:
            proc.wait()
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            # Only a *natural* end of the current playback notifies the UI;
            # a stop()/replace bumps _gen so this is a no-op.
            if gen != self._gen:
                return
            self._proc = None
            self._url = None
        if self._on_finished is not None:
            self._on_finished(url)

    def close(self) -> None:
        self.stop()
