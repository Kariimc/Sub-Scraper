import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .base import BaseScraper, Track

_YT_DLP = "yt-dlp"


class SoundCloudScraper(BaseScraper):
    def __init__(self, auth_token: str = "", username: str = "") -> None:
        self.auth_token = auth_token
        self.username = username

    def _auth_args(self) -> list[str]:
        if self.auth_token:
            return ["--add-header", f"Authorization: OAuth {self.auth_token}"]
        return []

    def fetch_library(self, **kwargs) -> list[Track]:
        if not self.username:
            raise ValueError("SoundCloud username is required to fetch likes.")

        url = f"https://soundcloud.com/{self.username}/likes"
        cmd = [_YT_DLP, "--flat-playlist", "-J", url] + self._auth_args()
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "yt-dlp failed to fetch library")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Unexpected yt-dlp output: {exc}") from exc

        entries = data.get("entries", [data] if "entries" not in data else [])
        tracks: list[Track] = []
        for e in entries:
            if not e:
                continue
            dur = e.get("duration") or 0
            tracks.append(Track(
                id=str(e.get("id") or e.get("webpage_url_basename", "")),
                title=e.get("title", "Unknown"),
                artist=e.get("uploader") or e.get("artist", "Unknown"),
                duration_ms=int(dur) * 1000,
                url=e.get("webpage_url") or e.get("url", ""),
                cover_url=e.get("thumbnail", ""),
            ))
        return tracks

    def download(
        self,
        track: Track,
        output_dir: str,
        quality: str,
        fmt: str,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        archive_file = out / "archive.txt"
        template = str(out / "%(uploader)s - %(title)s.%(ext)s")
        cmd = [
            _YT_DLP,
            "--extract-audio",
            "--audio-format", fmt,
            "--audio-quality", quality,
            "--output", template,
            "--no-playlist",
            "--embed-thumbnail",
            "--add-metadata",
            "--download-archive", str(archive_file),
            track.url,
        ] + self._auth_args()

        last_dest: Optional[str] = None
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if stripped:
                if on_log:
                    on_log(f"[yt-dlp] {stripped}")
                if "[ExtractAudio] Destination:" in stripped:
                    last_dest = stripped.split("Destination:", 1)[-1].strip()
        process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"yt-dlp exited with code {process.returncode}")

        if last_dest and Path(last_dest).exists():
            return last_dest

        for ext in (fmt, "mp3", "m4a", "ogg", "opus"):
            newest = sorted(out.glob(f"*.{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
            if newest:
                return str(newest[0])

        raise FileNotFoundError(f"Output file not found for: {track.display_name}")
