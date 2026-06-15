import re
import subprocess
from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .base import BaseScraper, DownloadStatus, Track

_SCOPE = "user-library-read playlist-read-private"
_CACHE = str(Path.home() / ".sub_scraper" / ".spotify_cache")


class SpotifyScraper(BaseScraper):
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._sp: Optional[spotipy.Spotify] = None

    @property
    def sp(self) -> spotipy.Spotify:
        if self._sp is None:
            auth = SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri="http://localhost:8888/callback",
                scope=_SCOPE,
                cache_path=_CACHE,
                open_browser=True,
            )
            self._sp = spotipy.Spotify(auth_manager=auth)
        return self._sp

    def fetch_library(self, **kwargs) -> list[Track]:
        tracks: list[Track] = []
        offset = 0
        while True:
            page = self.sp.current_user_saved_tracks(limit=50, offset=offset)
            items = page.get("items", [])
            if not items:
                break
            for item in items:
                t = item.get("track")
                if t and t.get("id"):
                    tracks.append(self._parse(t))
            offset += len(items)
            if not page.get("next"):
                break
        return tracks

    def fetch_playlists(self) -> list[dict]:
        playlists: list[dict] = []
        page = self.sp.current_user_playlists()
        while page:
            for p in page.get("items", []):
                playlists.append({"id": p["id"], "name": p["name"], "total": p["tracks"]["total"]})
            page = self.sp.next(page) if page.get("next") else None
        return playlists

    def fetch_playlist_tracks(self, playlist_id: str) -> list[Track]:
        tracks: list[Track] = []
        page = self.sp.playlist_items(playlist_id)
        while page:
            for item in page.get("items", []):
                t = item.get("track")
                if t and t.get("id"):
                    tracks.append(self._parse(t))
            page = self.sp.next(page) if page.get("next") else None
        return tracks

    def _parse(self, t: dict) -> Track:
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        images = t.get("album", {}).get("images") or []
        return Track(
            id=t["id"],
            title=t["name"],
            artist=artists,
            album=t.get("album", {}).get("name", ""),
            duration_ms=t.get("duration_ms", 0),
            url=t.get("external_urls", {}).get("spotify", ""),
            cover_url=images[0].get("url", "") if images else "",
        )

    def download(self, track: Track, output_dir: str, quality: str, fmt: str) -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        template = str(out / "{artist} - {title}.{output-ext}")
        result = subprocess.run(
            [
                "spotdl", "download", track.url,
                "--client-id", self.client_id,
                "--client-secret", self.client_secret,
                "--output", template,
                "--format", fmt,
                "--bitrate", quality,
                "--no-cache",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "spotdl exited non-zero")

        safe_artist = re.sub(r'[<>:"/\\|?*]', "_", track.artist)
        safe_title = re.sub(r'[<>:"/\\|?*]', "_", track.title)
        candidate = out / f"{safe_artist} - {safe_title}.{fmt}"
        if candidate.exists():
            return str(candidate)

        newest = sorted(out.glob(f"*.{fmt}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if newest:
            return str(newest[0])

        raise FileNotFoundError(f"Output file not found for: {track.display_name}")
