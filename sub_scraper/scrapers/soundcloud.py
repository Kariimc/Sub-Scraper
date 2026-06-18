import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import BaseScraper, BuildCmd, Track, ytdlp_perf_flags

if TYPE_CHECKING:
    import requests as _requests

_YT_DLP = "yt-dlp"
_API = "https://api-v2.soundcloud.com"


class SoundCloudScraper(BaseScraper):
    log_prefix = "[yt-dlp]"

    def __init__(
        self, auth_token: str = "", username: str = "", concurrent_fragments: int = 4
    ) -> None:
        self.auth_token = auth_token
        self.username = username
        self.concurrent_fragments = concurrent_fragments
        self._client_id = ""
        self._session: "Optional[_requests.Session]" = None

    @property
    def session(self) -> "_requests.Session":
        """A persistent, pooled requests session for metadata API calls.

        Reusing one session keeps the TCP/TLS connection to SoundCloud's API
        alive across the dozens of calls a playlist sweep makes, instead of
        re-handshaking each time."""
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter

            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=2)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            sess.headers.update({
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://soundcloud.com",
                "Referer": "https://soundcloud.com/",
            })
            self._session = sess
        return self._session

    def test_credentials(self) -> "tuple[bool, str]":
        """Confirm we can reach SoundCloud and that the username resolves / token
        is accepted."""
        if not (self.username or self.auth_token):
            return False, "Add your SoundCloud username (or a token for playlists)."
        try:
            client_id = self._get_client_id()
            if not client_id:
                return False, "Couldn't reach SoundCloud (no client_id found)."
            if self.username:
                resp = self.session.get(
                    f"{_API}/resolve",
                    params={"url": f"https://soundcloud.com/{self.username}",
                            "client_id": client_id},
                    timeout=15,
                )
                if resp.status_code == 404:
                    return False, f"Username '{self.username}' not found on SoundCloud."
                resp.raise_for_status()
                return True, f"Found SoundCloud user '{self.username}'."
            # Token-only: validate it against the authenticated /me endpoint.
            resp = self.session.get(
                f"{_API}/me",
                params={"client_id": client_id},
                headers={"Authorization": f"OAuth {self.auth_token}"},
                timeout=15,
            )
            if resp.status_code in (401, 403):
                return False, "SoundCloud token rejected — it may have expired."
            resp.raise_for_status()
            return True, "SoundCloud token is valid."
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            return False, f"SoundCloud check failed: {exc}"

    def _auth_args(self) -> list[str]:
        if self.auth_token:
            return ["--add-header", f"Authorization: OAuth {self.auth_token}"]
        return []

    def _get_client_id(self) -> str:
        """Scrape a public client_id from SoundCloud's web assets, the same way
        yt-dlp does. The API v2 rejects requests (403) without one even when an
        OAuth token is supplied."""
        if self._client_id:
            return self._client_id

        ua = {"User-Agent": "Mozilla/5.0"}
        home = self.session.get("https://soundcloud.com/", timeout=15, headers=ua)
        home.raise_for_status()
        script_urls = re.findall(r'<script[^>]+src="([^"]+)"', home.text)
        # The client_id lives in one of the JS bundles, usually a later one.
        # Match both the JS object form (client_id:"…") and the URL form.
        patterns = [
            re.compile(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"'),
            re.compile(r'client_id=([0-9a-zA-Z]{32})'),
        ]
        for url in reversed(script_urls):
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http"):
                continue
            try:
                js = self.session.get(url, timeout=15, headers=ua).text
            except Exception:
                continue
            for pat in patterns:
                m = pat.search(js)
                if m:
                    self._client_id = m.group(1)
                    return self._client_id
        raise RuntimeError(
            f"Could not extract a SoundCloud client_id "
            f"(checked {len(script_urls)} scripts)."
        )

    def _api_get(self, path: str, **params) -> dict:
        params["client_id"] = self._get_client_id()
        # The session already carries browser-style headers (User-Agent/Origin/
        # Referer) that SoundCloud's API v2 requires; add auth per-call.
        headers = {"Authorization": f"OAuth {self.auth_token}"} if self.auth_token else None
        url = path if path.startswith("http") else f"{_API}{path}"
        r = self.session.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()


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
        Requires auth_token; sees private/secret sets that yt-dlp cannot.

        Pulls from the user's full library (/me/library/all), which contains
        both playlists they created and playlists they liked/saved — each
        library item carries the playlist under a "playlist" key."""
        if not self.auth_token:
            raise ValueError(
                "SoundCloud auth token is required to list playlists. Add it in Settings."
            )

        playlists: list[dict] = []
        seen: set[str] = set()
        next_url: Optional[str] = "/me/library/all?limit=200"
        while next_url:
            data = self._api_get(next_url)
            for item in data.get("collection", []):
                # "playlist" = real playlists the user created/liked.
                # "system_playlist" = SoundCloud's auto-generated mixes
                # (Your Mix, Weekly Wave, …). The user opted to include both.
                pl = item.get("playlist") or item.get("system_playlist")
                if not pl:
                    continue
                pid = pl.get("permalink_url")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                playlists.append({
                    "id": pid,
                    "name": pl.get("title", "Unknown"),
                    "total": pl.get("track_count", 0),
                })
            next_url = data.get("next_href")

        # Also include playlists the user created themselves (these live under a
        # separate endpoint and are not part of the library feed).
        user_id = self._api_get("/me")["id"]
        next_url = f"/users/{user_id}/playlists?representation=mini&limit=200"
        while next_url:
            data = self._api_get(next_url)
            for p in data.get("collection", []):
                pid = p.get("permalink_url")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                playlists.append({
                    "id": pid,
                    "name": p.get("title", "Unknown"),
                    "total": p.get("track_count", 0),
                })
            next_url = data.get("next_href")

        return playlists

    def fetch_playlist_tracks(self, playlist_url: str) -> list[Track]:
        # Prefer the API when authenticated: it returns full, reliable track
        # metadata. yt-dlp's --flat-playlist often omits titles/uploaders for
        # SoundCloud sets, which left rows blank. Auto-generated mixes use
        # special URLs the /resolve endpoint can't handle, so fall back to
        # yt-dlp for those.
        if self.auth_token:
            try:
                tracks = self._fetch_playlist_tracks_api(playlist_url)
                if tracks:
                    return tracks
            except Exception:
                pass
        return self._fetch_playlist_tracks_ytdlp(playlist_url)

    def _fetch_playlist_tracks_api(self, playlist_url: str) -> list[Track]:
        pl = self._api_get("/resolve", url=playlist_url)
        raw = pl.get("tracks", [])

        # The playlist payload returns the first batch of tracks fully hydrated
        # and the rest as bare {"id": ...} stubs. Fetch the stubs in batches.
        full = [t for t in raw if t.get("title")]
        stub_ids = [str(t["id"]) for t in raw if not t.get("title") and t.get("id")]
        for i in range(0, len(stub_ids), 50):
            chunk = ",".join(stub_ids[i:i + 50])
            more = self._api_get("/tracks", ids=chunk)
            full.extend(more if isinstance(more, list) else more.get("collection", []))

        # Preserve the playlist's original order.
        by_id = {str(t.get("id")): t for t in full}
        ordered = [by_id[str(t["id"])] for t in raw if str(t.get("id")) in by_id]

        tracks: list[Track] = []
        for t in ordered:
            tracks.append(Track(
                id=str(t.get("id", "")),
                title=t.get("title", "Unknown"),
                artist=(t.get("user") or {}).get("username", "Unknown"),
                duration_ms=t.get("duration", 0),
                url=t.get("permalink_url", ""),
                cover_url=t.get("artwork_url", "") or "",
            ))
        return tracks

    def _fetch_playlist_tracks_ytdlp(self, playlist_url: str) -> list[Track]:
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

    def download_command(
        self, track: Track, output_dir: str, quality: str, fmt: str
    ) -> BuildCmd:
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
            ] + ytdlp_perf_flags(self.concurrent_fragments) + self._auth_args()

        return build_cmd
