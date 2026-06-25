import os
from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .base import BaseScraper, BuildCmd, Track, ytdlp_perf_args_str

_SCOPE = "user-library-read playlist-read-private"
_CACHE = str(Path.home() / ".sub_scraper" / ".spotify_cache")

# Desktop logs in via a browser pop-up that redirects to a tiny local server.
# The web server can't pop a browser and the redirect must return to the server
# instead, so it overrides both via these env vars (see web/server.py).
_DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


def build_oauth(client_id: str, client_secret: str,
                redirect_uri: str = _DEFAULT_REDIRECT_URI,
                open_browser: bool = True) -> SpotifyOAuth:
    """Construct a SpotifyOAuth sharing the app's scope and on-disk token cache.

    Shared by the desktop pop-up flow and the web login/callback routes so a
    token obtained either way lands in the same cache and is reused afterwards.
    """
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=_SCOPE,
        cache_path=_CACHE,
        open_browser=open_browser,
    )


def has_cached_token(client_id: str, client_secret: str) -> bool:
    """True if a usable Spotify token is already cached — i.e. the user has
    completed the login at least once and the refresh token still works."""
    if not (client_id and client_secret):
        return False
    try:
        return build_oauth(client_id, client_secret, open_browser=False).get_cached_token() is not None
    except Exception:
        return False


def clear_cached_token() -> None:
    """Forget the cached Spotify token (used by 'Disconnect')."""
    try:
        os.remove(_CACHE)
    except OSError:
        pass


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
            # The web server sets these so the same scraper runs headless: no
            # browser pop-up, and the redirect points back at the server. On
            # desktop they're unset, so the original loopback pop-up flow runs.
            redirect_uri = os.environ.get("SUBSCRAPER_REDIRECT_URI", _DEFAULT_REDIRECT_URI)
            open_browser = os.environ.get("SUBSCRAPER_NO_BROWSER") != "1"
            auth = build_oauth(
                self.client_id, self.client_secret,
                redirect_uri=redirect_uri, open_browser=open_browser,
            )
            self._sp = spotipy.Spotify(auth_manager=auth)
        return self._sp

    def test_credentials(self) -> "tuple[bool, str]":
        """Validate the Client ID/Secret via the client-credentials flow — this
        confirms the keys are real without needing the one-time user login."""
        if not (self.client_id and self.client_secret):
            return False, "Add your Spotify Client ID and Client Secret."
        try:
            from spotipy.oauth2 import SpotifyClientCredentials
            cc = SpotifyClientCredentials(
                client_id=self.client_id, client_secret=self.client_secret
            )
            probe = spotipy.Spotify(auth_manager=cc)
            probe.search(q="test", type="track", limit=1)
            return True, "Spotify keys are valid — you'll log in once on first load."
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            msg = str(exc)
            if "invalid_client" in msg.lower():
                return False, "Spotify rejected these keys. Double-check the Client ID and Secret."
            return False, f"Spotify check failed: {msg}"

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
            preview_url=t.get("preview_url") or "",
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
