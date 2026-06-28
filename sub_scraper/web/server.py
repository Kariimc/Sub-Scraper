"""Sub-Scraper web server — FastAPI backend wrapping the existing core."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Generator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sub_scraper.core.config import Config
from sub_scraper.core.download_manager import DownloadJob, DownloadManager
from sub_scraper.core.library_index import DownloadIndex
from sub_scraper.scrapers.base import DownloadStatus, Track
from sub_scraper.scrapers.factory import SOUNDCLOUD, SPOTIFY, build_scraper
from sub_scraper.scrapers.spotify import (
    build_oauth as _spotify_build_oauth,
    clear_cached_token as _spotify_clear_token,
    has_cached_token as _spotify_has_token,
)

import spotipy
from spotipy.oauth2 import SpotifyOauthError

# Spotify's login can't pop a desktop browser here, and its OAuth redirect must
# return to this server — signal the scraper to run headless and use the token
# the /api/spotify/callback route caches.
os.environ.setdefault("SUBSCRAPER_NO_BROWSER", "1")

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_config: Config = Config.load()
_env_locked: frozenset[str] = frozenset(Config.env_locked_fields())
_index: DownloadIndex = DownloadIndex()
_manager: DownloadManager | None = None
_library: list[Track] = []
_events: queue.SimpleQueue = queue.SimpleQueue()

_SECRET_FIELDS = {
    "spotify_client_secret",
    "soundcloud_auth_token",
    "gdrive_credentials_path",
}

_STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Sub-Scraper", version="2.2")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track_to_dict(track: Track) -> dict[str, Any]:
    """Serialise a Track to a plain dict suitable for JSON."""
    d = asdict(track)
    # Convert DownloadStatus enum to its string value name
    status = d.get("status")
    if hasattr(status, "value"):
        d["status"] = status.value
    elif isinstance(status, str):
        pass  # already a string
    else:
        d["status"] = str(status) if status is not None else "pending"
    # Ensure duration_str is present (derived from duration_ms)
    ms: int = d.get("duration_ms", 0)
    total_s = ms // 1000
    d["duration_str"] = f"{total_s // 60}:{total_s % 60:02d}" if total_s else "--:--"
    return d


def _mask_config(cfg: Config) -> dict[str, Any]:
    """Return config as dict with secret fields masked."""
    d = asdict(cfg)
    for field in _SECRET_FIELDS:
        if d.get(field):
            d[field] = "••••••••"
    return d


def _on_log(msg: str) -> None:
    _events.put({"type": "log", "message": msg})


def _make_progress_callback(track: Track):
    """Return an on_progress callback that pushes events to _events."""

    def _on_progress(t: Track) -> None:
        status_val = t.status
        if hasattr(status_val, "value"):
            status_str = status_val.value
        else:
            status_str = str(status_val)

        _events.put(
            {
                "type": "progress",
                "track_id": t.id,
                "fraction": t.progress,
                "speed": t.speed,
                "eta": t.eta,
                "status": status_str,
            }
        )
        if t.status in (DownloadStatus.COMPLETE, DownloadStatus.FAILED):
            _events.put(
                {
                    "type": "status",
                    "track_id": t.id,
                    "status": status_str,
                    "error": t.error or "",
                }
            )

    return _on_progress


def _get_or_init_manager() -> DownloadManager:
    global _manager
    if _manager is None:
        _manager = DownloadManager.from_config(_config)
        _manager.configure_index(_index)
        _manager.start()
    return _manager


def _ensure_manager_scraper(mgr: DownloadManager, source: str):
    """Wire the scraper for ``source`` into the manager using the CURRENT saved
    credentials, returning it.

    The manager starts with no per-source scraper (both default to None). Unless
    this runs before a batch is submitted, every real (non-demo) download fails
    with "scraper not configured". Building it at submit time also means the
    latest Settings/env-var credentials are always used."""
    scraper = build_scraper(_config, source)
    if source == SPOTIFY:
        mgr.configure_spotify(scraper)
    else:
        mgr.configure_soundcloud(scraper)
    return scraper


# --- Spotify OAuth (web login flow) ----------------------------------------

_SPOTIFY_CALLBACK_PATH = "/api/spotify/callback"


def _public_base_url(request: Request) -> str:
    """Public base URL (scheme://host) for the OAuth redirect.

    This value must be byte-for-byte identical at login time, at callback time,
    and in the URI shown to the user to register in Spotify — otherwise Spotify
    rejects the exchange. So prefer a fixed platform URL, then proxy headers,
    and force https for any non-local host (Spotify only allows http on
    loopback, and a TLS-terminating proxy reports the inner request as http)."""
    env_url = os.environ.get("SUBSCRAPER_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if env_url:
        return env_url.rstrip("/")
    # Proxy headers can be comma-separated lists ("client, proxy"); take the first.
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc or "localhost")
    host = host.split(",")[0].strip()
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
    proto = proto.split(",")[0].strip()
    # Spotify only permits http for loopback; anything else must be https.
    if host.split(":")[0] not in ("localhost", "127.0.0.1", "[::1]"):
        proto = "https"
    return f"{proto}://{host}".rstrip("/")


def _spotify_redirect_uri(request: Request) -> str:
    return _public_base_url(request) + _SPOTIFY_CALLBACK_PATH


def _friendly_library_error(source: str, exc: Exception) -> str:
    """Turn a raw scraper/Spotify exception into plain, do-this-next guidance.

    The library-load failure shows verbatim in the UI, so a non-technical user
    needs a sentence that says what's wrong AND how to fix it — not a stack-trace
    fragment like 'http status: 403'."""
    if source == SPOTIFY:
        # A bad/expired login: clear the cached token so the UI flips back to
        # "Not connected" and the next step is obviously to reconnect.
        if isinstance(exc, SpotifyOauthError):
            _spotify_clear_token()
            return ("Your Spotify login couldn't be refreshed. Open Settings, click "
                    "Disconnect, then Connect Spotify again to log in fresh.")
        if isinstance(exc, spotipy.SpotifyException):
            status = getattr(exc, "http_status", None)
            if status == 401:
                _spotify_clear_token()
                return ("Your Spotify login expired. Open Settings, click Disconnect, "
                        "then Connect Spotify again.")
            if status == 403:
                return ("Spotify blocked access to your library. New Spotify apps start "
                        "in “Development mode”, which only allows accounts you "
                        "approve. Fix it in 30 seconds: open your app on the "
                        "Spotify Developer Dashboard → Settings → User "
                        "Management, then add your own name and the email on your "
                        "Spotify account. Save, then try Load Library again.")
            if status == 429:
                return ("Spotify is rate-limiting requests right now. Wait a minute, "
                        "then click Load Library again.")
            msg = getattr(exc, "msg", "") or str(exc)
            return f"Spotify returned an error ({status}): {msg}"
    if source == SOUNDCLOUD:
        text = str(exc).lower()
        if "401" in text or "403" in text or "oauth" in text:
            return ("SoundCloud rejected the request. If you're loading playlists or "
                    "private likes, your token may have expired — grab a fresh one "
                    "(Settings explains how). Public likes only need your username.")
        if "404" in text or "not found" in text:
            return ("That SoundCloud username wasn't found. Double-check it in "
                    "Settings — it's the part after soundcloud.com/ in your profile link.")
        if "too long" in text:
            return str(exc)
        return ("Couldn't load your SoundCloud likes. Check your username in Settings, "
                "and make sure your likes are set to public.")
    # Network / unknown — keep it short but honest.
    text = str(exc).strip() or exc.__class__.__name__
    return f"Couldn't load your library: {text}"


# IDs in the no-setup demo library all carry this prefix so the download path
# can recognise and politely refuse them.
_DEMO_PREFIX = "demo-"


def _demo_library() -> list[Track]:
    """A fixed sample library so a first-time visitor can explore the whole UI
    with zero credentials. These tracks are *not* downloadable — submitting one
    returns a friendly nudge to add real keys (see :func:`submit_downloads`)."""
    # (title, artist, album, minutes, seconds, already_downloaded?)
    raw = [
        ("Midnight City", "M83", "Hurry Up, We're Dreaming", 4, 3, True),
        ("Redbone", "Childish Gambino", "Awaken, My Love!", 5, 27, True),
        ("Dreams", "Fleetwood Mac", "Rumours", 4, 14, False),
        ("Tame", "Pixies", "Doolittle", 1, 55, False),
        ("Nightcall", "Kavinsky", "OutRun", 4, 18, False),
        ("Bohemian Rhapsody", "Queen", "A Night at the Opera", 5, 55, False),
        ("Electric Feel", "MGMT", "Oracular Spectacular", 3, 49, False),
        ("Teardrop", "Massive Attack", "Mezzanine", 5, 30, False),
        ("Tighten Up", "The Black Keys", "Brothers", 3, 30, False),
        ("Feel Good Inc.", "Gorillaz", "Demon Days", 3, 41, False),
        ("Solo Dance", "Martin Jensen", "Solo Dance", 2, 53, False),
        ("Instant Crush", "Daft Punk", "Random Access Memories", 5, 37, False),
    ]
    tracks: list[Track] = []
    for i, (title, artist, album, mm, ss, done) in enumerate(raw, start=1):
        tracks.append(
            Track(
                id=f"{_DEMO_PREFIX}{i}",
                title=title,
                artist=artist,
                album=album,
                duration_ms=(mm * 60 + ss) * 1000,
                status=DownloadStatus.COMPLETE if done else DownloadStatus.PENDING,
            )
        )
    return tracks


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    global _manager
    _manager = DownloadManager.from_config(_config)
    _manager.configure_index(_index)
    _manager.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _manager
    if _manager is not None:
        _manager.stop()
        _manager = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "2.2"}


# True on a hosted platform whose free tier wipes the disk on sleep/redeploy —
# used to warn that Settings-entered keys won't persist (env vars should be used).
_HOSTED_EPHEMERAL = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or os.environ.get("RAILWAY_ENVIRONMENT")
)


@app.get("/api/config")
async def get_config() -> dict:
    data = _mask_config(_config)
    data["env_locked"] = sorted(_env_locked)
    data["hosted_ephemeral"] = _HOSTED_EPHEMERAL
    return data


@app.post("/api/config")
async def post_config(body: dict) -> dict:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(_config)}
    for key, value in body.items():
        if key not in field_names:
            continue
        # Env-locked fields are read from env vars at startup — never overwrite.
        if key in _env_locked:
            continue
        # Don't overwrite secrets if the client sent back the mask placeholder.
        if key in _SECRET_FIELDS and value == "••••••••":
            continue
        setattr(_config, key, value)
    _config.save()
    return {"ok": True}


@app.post("/api/config/test")
async def test_connection(body: dict) -> dict:
    """Pre-flight credential check so users can confirm setup before loading."""
    source: str = body.get("source", "spotify").lower()
    if source not in (SPOTIFY, SOUNDCLOUD):
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")

    loop = asyncio.get_event_loop()

    def _probe() -> "tuple[bool, str]":
        try:
            scraper = build_scraper(_config, source)
            return scraper.test_credentials()
        except Exception as exc:  # noqa: BLE001
            return False, f"Couldn't check {source}: {exc}"

    ok, message = await loop.run_in_executor(None, _probe)
    return {"ok": ok, "message": message}


@app.get("/api/spotify/status")
async def spotify_status(request: Request) -> dict:
    """Whether the one-time Spotify login is done here, plus the exact redirect
    URI the user must register in their Spotify app for it to work."""
    return {
        "has_keys": bool(_config.spotify_client_id and _config.spotify_client_secret),
        "connected": _spotify_has_token(_config.spotify_client_id, _config.spotify_client_secret),
        "redirect_uri": _spotify_redirect_uri(request),
    }


@app.get("/api/spotify/login")
async def spotify_login(request: Request):
    """Start Spotify's login by redirecting the browser to Spotify's consent
    page; the user returns to the callback below with an authorization code.

    Always bounces back into the app (never a raw error page) so the user can't
    get stranded outside Sub-Scraper."""
    if not (_config.spotify_client_id and _config.spotify_client_secret):
        return RedirectResponse(url="/?spotify=nokeys")
    try:
        oauth = _spotify_build_oauth(
            _config.spotify_client_id, _config.spotify_client_secret,
            redirect_uri=_spotify_redirect_uri(request), open_browser=False,
        )
        return RedirectResponse(url=oauth.get_authorize_url())
    except Exception:
        return RedirectResponse(url="/?spotify=error")


@app.get("/api/spotify/callback")
async def spotify_callback(request: Request, code: str = "", error: str = ""):
    """Spotify redirects here after consent. Exchange the code for a token,
    confirm it actually cached, then bounce back into the app.

    Every path returns a redirect to "/" so the user always lands back in
    Sub-Scraper — success, denial, or unexpected failure alike."""
    if error or not code:
        return RedirectResponse(url="/?spotify=denied")

    loop = asyncio.get_event_loop()

    def _exchange() -> bool:
        try:
            oauth = _spotify_build_oauth(
                _config.spotify_client_id, _config.spotify_client_secret,
                redirect_uri=_spotify_redirect_uri(request), open_browser=False,
            )
            oauth.get_access_token(code, as_dict=False, check_cache=False)
            # Confirm the token genuinely persisted — a failed cache write would
            # otherwise look like success and trap the user in a connect loop.
            return _spotify_has_token(_config.spotify_client_id, _config.spotify_client_secret)
        except Exception:
            return False

    ok = await loop.run_in_executor(None, _exchange)
    return RedirectResponse(url="/?spotify=" + ("connected" if ok else "error"))


@app.post("/api/spotify/disconnect")
async def spotify_disconnect() -> dict:
    _spotify_clear_token()
    return {"ok": True}


def _check_source_ready(source: str, *, need_playlists: bool) -> None:
    """Raise a friendly HTTP 400 if the credentials for this action are missing.

    ``need_playlists`` flags a playlist action: SoundCloud playlists require an
    auth token, whereas SoundCloud liked songs only need a username."""
    if source == SPOTIFY:
        if not (_config.spotify_client_id and _config.spotify_client_secret):
            raise HTTPException(
                status_code=400,
                detail="Spotify isn't set up yet. Open Settings and add your Spotify "
                       "Client ID and Client Secret, then Save.",
            )
        if not _spotify_has_token(_config.spotify_client_id, _config.spotify_client_secret):
            raise HTTPException(
                status_code=400,
                detail="Connect your Spotify account first — open Settings and click "
                       "“Connect Spotify”. Spotify needs a one-time login in your browser.",
            )
        return
    # SoundCloud
    if need_playlists:
        if not _config.soundcloud_auth_token:
            raise HTTPException(
                status_code=400,
                detail="SoundCloud playlists need an Auth Token. Open Settings, add "
                       "your SoundCloud token (the “How do I get this token?” guide "
                       "shows how), then try again.",
            )
    elif not _config.soundcloud_username:
        raise HTTPException(
            status_code=400,
            detail="Add your SoundCloud username in Settings to load your liked "
                   "songs. (The token alone isn't enough — it's only needed on top "
                   "of the username, for playlists and private likes.)",
        )


@app.post("/api/library/playlists")
async def load_playlists(body: dict) -> dict:
    """List the user's playlists for a source so the UI can show them alongside
    liked songs. Spotify needs its one-time login; SoundCloud needs an auth token."""
    source: str = body.get("source", "spotify").lower()
    if source not in (SPOTIFY, SOUNDCLOUD):
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")
    _check_source_ready(source, need_playlists=True)

    try:
        scraper = build_scraper(_config, source)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build scraper: {exc}") from exc

    loop = asyncio.get_event_loop()
    try:
        playlists = await loop.run_in_executor(None, scraper.fetch_playlists)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_friendly_library_error(source, exc)) from exc
    return {"playlists": playlists}


@app.post("/api/library/load")
async def load_library(body: dict) -> dict:
    global _library
    source: str = body.get("source", "spotify").lower()
    if source not in (SPOTIFY, SOUNDCLOUD):
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")

    # A playlist id/url loads that playlist's tracks; otherwise it's liked songs.
    playlist_id = (body.get("playlist_id") or "").strip()

    # Friendly credential precheck so first-time users get a clear pointer to
    # Settings instead of a raw library-provider error.
    _check_source_ready(source, need_playlists=bool(playlist_id))

    try:
        scraper = build_scraper(_config, source)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build scraper: {exc}") from exc

    loop = asyncio.get_event_loop()

    def _fetch() -> list[Track]:
        if playlist_id:
            return scraper.fetch_playlist_tracks(playlist_id)
        return scraper.fetch_library()

    try:
        tracks: list[Track] = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_friendly_library_error(source, exc)) from exc

    # Annotate tracks that are already downloaded
    for track in tracks:
        existing = _index.file_exists_for(_config.download_path, track)
        if existing:
            track.status = DownloadStatus.COMPLETE
            track.local_path = existing

    _library = tracks
    return {"tracks": [_track_to_dict(t) for t in tracks]}


@app.post("/api/library/demo")
async def load_demo_library() -> dict:
    """Load a no-credentials sample library so visitors can explore instantly."""
    global _library
    _library = _demo_library()
    return {"tracks": [_track_to_dict(t) for t in _library], "demo": True}


@app.get("/api/library")
async def get_library() -> dict:
    return {"tracks": [_track_to_dict(t) for t in _library]}


@app.post("/api/downloads/submit")
async def submit_downloads(body: dict) -> dict:
    track_ids: list[str] = body.get("track_ids", [])
    source: str = body.get("source", "spotify").lower()

    if not track_ids:
        raise HTTPException(status_code=400, detail="track_ids is required")
    if source not in (SPOTIFY, SOUNDCLOUD):
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")

    id_set = set(track_ids)
    selected = [t for t in _library if t.id in id_set]
    if not selected:
        raise HTTPException(status_code=400, detail="No matching tracks found in library")

    # The sample library is for exploring only — never actually download it.
    if any(t.id.startswith(_DEMO_PREFIX) for t in selected):
        raise HTTPException(
            status_code=400,
            detail="This is the demo library. Open Settings and add your own free "
                   "Spotify or SoundCloud credentials to download real tracks.",
        )

    mgr = _get_or_init_manager()

    # Without this the manager has no scraper for this source and every track
    # fails with "scraper not configured".
    try:
        _ensure_manager_scraper(mgr, source)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Couldn't set up the {source} downloader: {exc}",
        ) from exc

    jobs: list[DownloadJob] = []
    for track in selected:
        job = DownloadJob(
            track=track,
            source=source,
            output_dir=_config.download_path,
            quality=_config.audio_quality,
            fmt=_config.output_format,
            on_progress=_make_progress_callback(track),
            on_log=_on_log,
        )
        jobs.append(job)

    mgr.submit_batch(jobs)
    return {"queued": len(jobs)}


def _sse_generator() -> Generator[str, None, None]:
    """Yield SSE-formatted events from _events queue."""
    deadline = time.monotonic() + 120.0
    # Send an initial keepalive comment
    yield ": keepalive\n\n"
    while True:
        now = time.monotonic()
        if now >= deadline:
            yield "data: {\"type\": \"timeout\"}\n\n"
            return
        try:
            event = _events.get(timeout=0.5)
            deadline = time.monotonic() + 120.0  # reset on activity
            yield f"data: {json.dumps(event)}\n\n"
        except queue.Empty:
            # Send a keepalive comment every ~5s of silence
            yield ": keepalive\n\n"


@app.get("/api/downloads/stream")
async def downloads_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/downloads/status")
async def downloads_status() -> dict:
    return {"tracks": [_track_to_dict(t) for t in _library]}


@app.post("/api/downloads/cancel")
async def cancel_downloads() -> dict:
    mgr = _get_or_init_manager()
    for track in _library:
        if track.status == DownloadStatus.DOWNLOADING:
            try:
                mgr.cancel(track.id)
            except Exception:
                pass
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static files (must be mounted last so it doesn't shadow API routes)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
