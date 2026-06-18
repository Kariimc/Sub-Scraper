"""Portable audio device sync — detect removable drives and copy a track list to them.

Supports macOS (/Volumes), Linux (/media/$USER, /mnt), and Windows (wmic DriveType=2).
Conversion via ffmpeg subprocess is optional; pass ``convert_to="mp3"`` or
``convert_to="flac"`` to transcode on the fly.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .logging_config import get_logger, kv

log = get_logger("device_sync")


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    """Represents a mounted removable audio player / drive."""
    name: str
    path: Path
    free_bytes: int
    total_bytes: int

    def __str__(self) -> str:
        return f"{self.name} ({_human_bytes(self.free_bytes)} free)"


@dataclass
class SyncResult:
    """Aggregate outcome of a single sync run."""
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_copied: int = 0


# ---------------------------------------------------------------------------
# Device detection
# ---------------------------------------------------------------------------

def detect_devices() -> list[DeviceInfo]:
    """Return a list of mounted removable drives suitable for audio syncing.

    Detection is best-effort and platform-specific:

    * **macOS** – scans ``/Volumes``, skips the boot volume (*Macintosh HD*)
      and any hidden (dot-prefixed) volumes.
    * **Linux** – scans ``/media/$USER`` (udisks auto-mount) and ``/mnt``
      (manual mounts).  Skips entries that are not directories or are empty
      mount-stubs (i.e. contain no files/subdirectories beyond ``.``).
    * **Windows** – uses ``wmic logicaldisk`` to find drives with
      ``DriveType=2`` (removable) and maps them to ``Path`` objects.
    * **Fallback** – logs a warning and returns an empty list.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            return _detect_macos()
        elif system == "Linux":
            return _detect_linux()
        elif system == "Windows":
            return _detect_windows()
        else:
            log.warning("device_sync.detect.unsupported " + kv(platform=system))
            return []
    except Exception as exc:  # noqa: BLE001
        log.warning("device_sync.detect.error " + kv(error=exc))
        return []


def _detect_macos() -> list[DeviceInfo]:
    volumes = Path("/Volumes")
    if not volumes.is_dir():
        return []
    results: list[DeviceInfo] = []
    skip_names = {"Macintosh HD", "Macintosh HD - Data"}
    for entry in volumes.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name in skip_names:
            continue
        if not entry.is_dir():
            continue
        info = _stat_device(entry)
        if info is not None:
            results.append(info)
    log.debug("device_sync.detect.macos " + kv(found=len(results)))
    return results


def _detect_linux() -> list[DeviceInfo]:
    candidates: list[Path] = []
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    media_user = Path(f"/media/{user}") if user else None
    if media_user and media_user.is_dir():
        candidates += list(media_user.iterdir())
    mnt = Path("/mnt")
    if mnt.is_dir():
        candidates += list(mnt.iterdir())

    results: list[DeviceInfo] = []
    for entry in candidates:
        if not entry.is_dir():
            continue
        # Skip empty mount stubs — they exist but have nothing mounted.
        try:
            children = list(entry.iterdir())
        except PermissionError:
            continue
        if not children:
            continue
        info = _stat_device(entry)
        if info is not None:
            results.append(info)
    log.debug("device_sync.detect.linux " + kv(found=len(results)))
    return results


def _detect_windows() -> list[DeviceInfo]:
    """Use ``wmic logicaldisk`` to enumerate removable drives (DriveType=2)."""
    try:
        proc = subprocess.run(
            ["wmic", "logicaldisk", "get", "DeviceID,DriveType,VolumeName", "/format:csv"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("device_sync.detect.windows.wmic_failed " + kv(error=exc))
        return []

    results: list[DeviceInfo] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        # CSV header: Node,DeviceID,DriveType,VolumeName
        if len(parts) < 4:
            continue
        try:
            drive_type = int(parts[2].strip())
        except ValueError:
            continue
        if drive_type != 2:  # 2 = removable
            continue
        device_id = parts[1].strip()   # e.g. "E:"
        volume_name = parts[3].strip() or device_id
        if not device_id:
            continue
        path = Path(device_id + "\\")
        if not path.exists():
            continue
        info = _stat_device(path, name=volume_name)
        if info is not None:
            results.append(info)
    log.debug("device_sync.detect.windows " + kv(found=len(results)))
    return results


def _stat_device(path: Path, *, name: str | None = None) -> Optional[DeviceInfo]:
    """Return a DeviceInfo for *path* or None if statvfs fails."""
    try:
        stat = shutil.disk_usage(path)
        return DeviceInfo(
            name=name or path.name,
            path=path,
            free_bytes=stat.free,
            total_bytes=stat.total,
        )
    except (OSError, PermissionError) as exc:
        log.debug("device_sync.stat.failed " + kv(path=path, error=exc))
        return None


# ---------------------------------------------------------------------------
# Syncer
# ---------------------------------------------------------------------------

class DeviceSyncer:
    """Copy a list of Track objects to a removable device.

    Parameters
    ----------
    device:
        The target ``DeviceInfo`` (returned by :func:`detect_devices`).
    on_log:
        Optional callable ``(message: str) -> None`` receiving human-readable
        log lines.  Called from the *worker* thread — if you touch tkinter from
        this callback, marshal through a queue.
    on_progress:
        Optional callable ``(fraction: float, message: str) -> None``.
        *fraction* is ``files_done / total_files`` (0.0 – 1.0).  Called from
        the *worker* thread.
    """

    def __init__(
        self,
        device: DeviceInfo,
        *,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        self._device = device
        self._on_log = on_log
        self._on_progress = on_progress

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(
        self,
        tracks: list,
        *,
        organize: str = "flat",
        convert_to: str | None = None,
    ) -> SyncResult:
        """Sync *tracks* to the device and return a :class:`SyncResult`.

        Parameters
        ----------
        tracks:
            An iterable of objects that have ``local_path``, ``artist``,
            ``album``, and ``title`` attributes (compatible with
            :class:`~sub_scraper.scrapers.base.Track`).
        organize:
            ``"flat"`` – all files land directly in the device root.
            ``"artist_album"`` – files are placed in
            ``<device>/<Artist>/<Album>/<file>``.
        convert_to:
            ``None`` – copy the source file as-is.
            ``"mp3"`` – transcode with ``ffmpeg -i <src> -q:a 0 <dst.mp3>``.
            ``"flac"`` – transcode with ``ffmpeg -i <src> <dst.flac>``.
        """
        # Build a work-list of tracks that have a reachable local file.
        workable = [t for t in tracks if _has_local_file(t)]
        total = len(workable)
        result = SyncResult()

        if total == 0:
            self._log("No tracks with local files to sync.")
            return result

        if not self._device.path.exists():
            self._log(f"Device path {self._device.path} is not accessible.")
            result.failed = len(tracks)
            return result

        for idx, track in enumerate(workable):
            try:
                dest_dir = self._dest_dir(track, organize)
                dest_dir.mkdir(parents=True, exist_ok=True)

                src = Path(track.local_path)  # type: ignore[union-attr]
                dest_name = _dest_filename(src, convert_to)
                dest = dest_dir / dest_name

                label = f"{_track_label(track)}"

                if dest.exists():
                    self._log(f"Skip (already on device): {label}")
                    result.skipped += 1
                elif convert_to is None:
                    self._log(f"Copying {label}")
                    self._emit_progress((idx) / total, f"Copying {label}")
                    shutil.copy2(src, dest)
                    result.bytes_copied += dest.stat().st_size
                    result.synced += 1
                else:
                    self._log(f"Converting {label} -> {convert_to.upper()}")
                    self._emit_progress((idx) / total, f"Converting {label}")
                    self._convert(src, dest, convert_to)
                    result.bytes_copied += dest.stat().st_size
                    result.synced += 1

            except Exception as exc:  # noqa: BLE001 — best-effort, keep going
                self._log(f"Failed: {_track_label(track)} — {exc}")
                log.warning("device_sync.track.failed " + kv(track=_track_label(track), error=exc))
                result.failed += 1

            self._emit_progress((idx + 1) / total, "")

        self._log(
            f"Sync complete: {result.synced} synced, "
            f"{result.skipped} skipped, {result.failed} failed."
        )
        log.info(
            "device_sync.done "
            + kv(synced=result.synced, skipped=result.skipped, failed=result.failed,
                 bytes=result.bytes_copied)
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dest_dir(self, track, organize: str) -> Path:
        base = self._device.path
        if organize == "artist_album":
            artist = _safe_name(getattr(track, "artist", "Unknown Artist") or "Unknown Artist")
            album = _safe_name(getattr(track, "album", "Unknown Album") or "Unknown Album")
            return base / artist / album
        return base

    def _convert(self, src: Path, dest: Path, fmt: str) -> None:
        """Run ffmpeg to transcode *src* to *dest*."""
        if fmt == "mp3":
            cmd = ["ffmpeg", "-i", str(src), "-q:a", "0", str(dest), "-y"]
        elif fmt == "flac":
            cmd = ["ffmpeg", "-i", str(src), str(dest), "-y"]
        else:
            raise ValueError(f"Unsupported conversion format: {fmt!r}")

        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            stderr_snippet = (proc.stderr or "").strip()[-300:]
            raise RuntimeError(
                f"ffmpeg exited with code {proc.returncode}: {stderr_snippet}"
            )

    def _log(self, message: str) -> None:
        if self._on_log is not None:
            try:
                self._on_log(message)
            except Exception:  # noqa: BLE001
                pass

    def _emit_progress(self, fraction: float, message: str) -> None:
        if self._on_progress is not None:
            try:
                self._on_progress(max(0.0, min(1.0, fraction)), message)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_local_file(track) -> bool:
    path = getattr(track, "local_path", None)
    if not path:
        return False
    return Path(path).is_file()


def _track_label(track) -> str:
    artist = getattr(track, "artist", "") or ""
    title = getattr(track, "title", "") or ""
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or str(getattr(track, "id", "unknown"))


def _dest_filename(src: Path, convert_to: str | None) -> str:
    """Return the destination file name, swapping the suffix when converting."""
    if convert_to is None:
        return src.name
    return src.stem + "." + convert_to


def _safe_name(text: str) -> str:
    """Strip filesystem-unsafe characters from a directory component."""
    keep = []
    for ch in text:
        if ch in r'\/:*?"<>|':
            keep.append("_")
        else:
            keep.append(ch)
    return "".join(keep).strip() or "Unknown"


def _human_bytes(n: int) -> str:
    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# Public API re-exports
# ---------------------------------------------------------------------------

__all__ = ["DeviceInfo", "SyncResult", "DeviceSyncer", "detect_devices"]
