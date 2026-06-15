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

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}"


class BaseScraper(ABC):
    @abstractmethod
    def fetch_library(self, **kwargs) -> list[Track]: ...

    @abstractmethod
    def download(
        self,
        track: Track,
        output_dir: str,
        quality: str,
        fmt: str,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str: ...


def run_isolated_download(
    build_cmd: Callable[[Path], list],
    output_dir: str,
    track: Track,
    log_prefix: str,
    on_log: Optional[Callable[[str], None]],
) -> str:
    """Run a download command in a private temp directory so the produced file
    is unambiguous, then move it into output_dir and return its path.

    Downloading straight into the shared output folder forced us to guess which
    file belonged to this track ("newest file"), which picked the wrong file
    when a download was skipped — re-uploading unrelated songs as duplicates.
    An isolated directory removes the guesswork entirely.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(dir=out, prefix=".dl_"))
    try:
        process = subprocess.Popen(
            build_cmd(tmpdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if stripped and on_log:
                on_log(f"{log_prefix} {stripped}")
        process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"{log_prefix} exited with code {process.returncode}")

        audio = [
            p for p in tmpdir.rglob("*")
            if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_EXTS
        ]
        if not audio:
            raise FileNotFoundError(f"No audio file produced for: {track.display_name}")

        # The audio track is the largest matching file (ignores stray artwork).
        src = max(audio, key=lambda p: p.stat().st_size)
        if src.stat().st_size == 0:
            raise RuntimeError(f"Download produced an empty file for: {track.display_name}")
        dest = out / src.name
        shutil.move(str(src), str(dest))
        return str(dest)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
