from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

# Final audio container extensions we expect a download to produce.
_AUDIO_EXTS = {"mp3", "m4a", "ogg", "opus", "wav", "flac", "aac"}

# Smallest plausible real audio file; anything below is treated as corrupt.
_MIN_AUDIO_BYTES = 1024

BuildCmd = Callable[[Path], list]


def ytdlp_perf_flags(concurrent_fragments: int = 4, use_aria2c: bool = True) -> list[str]:
    """yt-dlp flags that maximise throughput and resilience: parallel fragment
    downloads, native retries, and aria2c (16 parallel connections) as the
    external downloader when it is installed."""
    flags = [
        "-N", str(max(1, concurrent_fragments)),
        "--retries", "10",
        "--fragment-retries", "10",
        "--file-access-retries", "5",
    ]
    if use_aria2c and shutil.which("aria2c"):
        flags += ["--downloader", "aria2c",
                  "--downloader-args", "aria2c:-x16 -s16 -k1M"]
    return flags


def ytdlp_perf_args_str(concurrent_fragments: int = 4) -> str:
    """The throughput/resilience flags as one string, for tools that forward
    args to yt-dlp (e.g. spotdl's --yt-dlp-args)."""
    return f"-N {max(1, concurrent_fragments)} --retries 10 --fragment-retries 10"


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    url: str = ""
    cover_url: str = ""
    status: DownloadStatus = DownloadStatus.PENDING
    local_path: Optional[str] = None
    error: Optional[str] = None
    # Populated by post-download integrity verification.
    size_bytes: int = 0
    checksum: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}"


class BaseScraper(ABC):
    #: Prefix used when forwarding the downloader's stdout to the log.
    log_prefix: str = "[download]"

    @abstractmethod
    def fetch_library(self, **kwargs) -> list[Track]: ...

    @abstractmethod
    def download_command(
        self, track: Track, output_dir: str, quality: str, fmt: str
    ) -> BuildCmd:
        """Return a ``build_cmd(tmpdir)`` callable producing the argv to run.

        The command must write its output into the temp directory it is given.
        The engine runs it (sync or async) and finalises the result."""

    def download(
        self,
        track: Track,
        output_dir: str,
        quality: str,
        fmt: str,
        on_log: Optional[Callable[[str], None]] = None,
        *,
        verify: bool = True,
    ) -> str:
        """Synchronous convenience wrapper around the command + finaliser."""
        return run_isolated_download(
            self.download_command(track, output_dir, quality, fmt),
            output_dir, track, self.log_prefix, on_log, verify=verify,
        )


# ---------------------------------------------------------------------------
# Integrity verification + finalisation (shared by sync and async runners)
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streamed SHA-256 — hashes in 1 MiB blocks, never loading the whole file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _finalize_download(tmpdir: Path, out: Path, track: Track, *, verify: bool) -> str:
    """Pick the produced audio file, validate it, move it into ``out``.

    Validation: a file must exist, carry an audio extension, and clear the
    minimum-size floor. When ``verify`` is set we also record size + SHA-256 so
    integrity is provable downstream.
    """
    audio = [
        p for p in tmpdir.rglob("*")
        if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_EXTS
    ]
    if not audio:
        raise FileNotFoundError(f"No audio file produced for: {track.display_name}")

    # The audio track is the largest matching file (ignores stray artwork).
    src = max(audio, key=lambda p: p.stat().st_size)
    size = src.stat().st_size
    if size < _MIN_AUDIO_BYTES:
        raise RuntimeError(f"Download produced a corrupt/empty file for: {track.display_name}")

    dest = out / src.name
    shutil.move(str(src), str(dest))

    track.size_bytes = size
    if verify:
        track.checksum = sha256_file(dest)
    return str(dest)


def run_isolated_download(
    build_cmd: BuildCmd,
    output_dir: str,
    track: Track,
    log_prefix: str,
    on_log: Optional[Callable[[str], None]],
    *,
    verify: bool = True,
) -> str:
    """Run a download command in a private temp directory (synchronously) so the
    produced file is unambiguous, then move it into ``output_dir``.

    Downloading straight into the shared output folder forced us to guess which
    file belonged to this track ("newest file"), which picked the wrong file
    when a download was skipped — re-uploading unrelated songs as duplicates.
    An isolated directory removes the guesswork entirely.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(dir=out, prefix=".dl_"))
    try:
        with subprocess.Popen(
            [str(c) for c in build_cmd(tmpdir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                stripped = line.strip()
                if stripped and on_log:
                    on_log(f"{log_prefix} {stripped}")
            process.wait()
            if process.returncode != 0:
                raise RuntimeError(f"{log_prefix} exited with code {process.returncode}")

        return _finalize_download(tmpdir, out, track, verify=verify)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def run_isolated_download_async(
    build_cmd: BuildCmd,
    output_dir: str,
    track: Track,
    log_prefix: str,
    on_log: Optional[Callable[[str], None]],
    *,
    verify: bool = True,
) -> str:
    """Async twin of :func:`run_isolated_download`.

    Spawns the downloader with ``asyncio.create_subprocess_exec`` and streams
    its stdout line-by-line without blocking the event loop, so a single loop
    thread can supervise many concurrent downloads. The subprocess is reaped via
    ``wait()`` and its temp directory is always cleaned up.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(dir=out, prefix=".dl_"))
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        cmd = [str(c) for c in build_cmd(tmpdir)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            stripped = raw.decode("utf-8", "replace").strip()
            if stripped and on_log:
                on_log(f"{log_prefix} {stripped}")
        returncode = await proc.wait()
        proc = None
        if returncode != 0:
            raise RuntimeError(f"{log_prefix} exited with code {returncode}")

        # Finalisation is fast local I/O; run it off the loop to stay responsive.
        return await asyncio.to_thread(_finalize_download, tmpdir, out, track, verify=verify)
    finally:
        if proc is not None and proc.returncode is None:
            # Cancelled mid-flight: terminate the child so it can't leak.
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass
        await asyncio.to_thread(shutil.rmtree, tmpdir, True)
