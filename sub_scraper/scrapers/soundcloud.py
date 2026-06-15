import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .base import BaseScraper, Track, run_isolated_download

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

    def fetch_playlists(self) -> list[dict]:
        """List the authenticated user's playlists via the SoundCloud API v2.
        Requires auth_token; sees private/secret sets that yt-dlp cannot."""
        if not self.auth_token:
            raise ValueError(
                "SoundCloud auth token is required to list playlists. Add it in Settings."
            )
        import requests
        headers = {"Authorization": f"OAuth {self.auth_token}"}

        me = requests.get("https://api-v2.soundcloud.com/me", headers=headers, timeout=15)
        me.raise_for_status()
        user_id = me.json()["id"]

        playlists: list[dict] = []
        next_url: str | None = (
            f"https://api-v2.soundcloud.com/users/{user_id}/playlists"
            "?representation=mini&limit=200"
        )
        while next_url:
            r = requests.get(next_url, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            for p in data.get("collection", []):
                playlists.append({
                    "id": p["permalink_url"],
                    "name": p["title"],
                    "total": p.get("track_count", 0),
                })
            next_url = data.get("next_href")

        return playlists

    def fetch_playlist_tracks(self, playlist_url: str) -> list[Track]:
        cmd = [_YT_DLP, "--flat-playlist", "-J", playlist_url] + self._auth_args()
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "yt-dlp failed to fetch playlist tracks")
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
        def build_cmd(tmp: Path) -> list:
            template = str(tmp / "%(uploader)s - %(title)s.%(ext)s")
            return [
                _YT_DLP,
                "--extract-audio",
                "--audio-format", fmt,
                "--audio-quality", quality,
                "--output", template,
                "--no-playlist",
                "--embed-thumbnail",
                "--add-metadata",
                track.url,
            ] + self._auth_args()

        return run_isolated_download(build_cmd, output_dir, track, "[yt-dlp]", on_log)
