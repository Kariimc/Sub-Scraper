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

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sub_scraper.core.config import Config
from sub_scraper.core.download_manager import DownloadJob, DownloadManager
from sub_scraper.core.library_index import DownloadIndex
from sub_scraper.scrapers.base import DownloadStatus, Track
from sub_scraper.scrapers.factory import SOUNDCLOUD, SPOTIFY, build_scraper

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


@app.get("/api/config")
async def get_config() -> dict:
    data = _mask_config(_config)
    data["env_locked"] = sorted(_env_locked)
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


@app.post("/api/library/load")
async def load_library(body: dict) -> dict:
    global _library
    source: str = body.get("source", "spotify").lower()
    if source not in (SPOTIFY, SOUNDCLOUD):
        raise HTTPException(status_code=400, detail=f"Unknown source: {source!r}")

    # Friendly credential precheck so first-time users get a clear pointer to
    # Settings instead of a raw library-provider error.
    if source == SPOTIFY and not (_config.spotify_client_id and _config.spotify_client_secret):
        raise HTTPException(
            status_code=400,
            detail="Spotify isn't set up yet. Open Settings and add your Spotify "
                   "Client ID and Client Secret, then Save.",
        )
    if source == SOUNDCLOUD and not (_config.soundcloud_username or _config.soundcloud_auth_token):
        raise HTTPException(
            status_code=400,
            detail="SoundCloud isn't set up yet. Open Settings and add your "
                   "SoundCloud username (and token for playlists), then Save.",
        )

    try:
        scraper = build_scraper(_config, source)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build scraper: {exc}") from exc

    loop = asyncio.get_event_loop()

    def _fetch() -> list[Track]:
        return scraper.fetch_library()

    try:
        tracks: list[Track] = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't load your library: {exc}") from exc

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
