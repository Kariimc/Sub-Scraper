from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .base import BaseScraper, BuildCmd, Track, ytdlp_perf_args_str

_SCOPE = "user-library-read playlist-read-private"
_CACHE = str(Path.home() / ".sub_scraper" / ".spotify_cache")


def _thumb_url(images: list[dict]) -> str:
    """Pick the smallest cover image >= 64px (Spotify returns them largest-first).

    The library view only needs a ~46px thumbnail, so grabbing the 640x640 art
    would waste bandwidth and memory across a big library; the smallest variant
    is plenty."""
    if not images:
        return ""
    sized = [im for im in images if im.get("url") and im.get("width")]
    if not sized:
        return images[0].get("url", "")
    big_enough = [im for im in sized if im["width"] >= 64]
    pick = min(big_enough or sized, key=lambda im: im["width"])
    return pick.get("url", "")


class SpotifyScraper(BaseScraper):
    log_prefix = "[spotDL]"

    def __init__(
        self, client_id: str, client_secret: str, concurrent_fragments: int = 4
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.concurrent_fragments = concurrent_fragments
        self._sp: Optional[spotipy.Spotify] = None

    @property
    def sp(self) -> spotipy.Spotify:
        if self._sp is None:
            auth = SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri="http://127.0.0.1:8888/callback",
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
        return Track(
            id=t["id"],
            title=t["name"],
            artist=artists,
            album=t.get("album", {}).get("name", ""),
            duration_ms=t.get("duration_ms", 0),
            url=t.get("external_urls", {}).get("spotify", ""),
            cover_url=_thumb_url(t.get("album", {}).get("images") or []),
        )

    def download_command(
        self, track: Track, output_dir: str, quality: str, fmt: str
    ) -> BuildCmd:
        def build_cmd(tmp: Path) -> list:
            template = str(tmp / "{artist} - {title}.{output-ext}")
            return [
                "spotdl", "download", track.url,
                "--client-id", self.client_id,
                "--client-secret", self.client_secret,
                "--output", template,
                "--format", fmt,
                "--bitrate", quality,
                "--no-cache",
                # Forward parallel-fragment + retry flags to the underlying
                # yt-dlp that spotdl drives.
                "--yt-dlp-args", ytdlp_perf_args_str(self.concurrent_fragments),
            ]

        return build_cmd
