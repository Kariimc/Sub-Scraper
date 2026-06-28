#!/usr/bin/env python3
"""Headless tests for the download engine, networking, index and resilience.

Run from the repo root:  python3 tests/test_core.py

These exercise the real async engine end-to-end with a fake scraper that drives
tiny shell commands (no network, no yt-dlp), a local aiohttp server for the
streaming downloader, and the persistence/resilience primitives directly.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sub_scraper.core.config import Config
from sub_scraper.core.download_manager import (
    DownloadJob, DownloadManager, _is_direct_media, _safe_filename,
)
from sub_scraper.core.library_index import DownloadIndex
from sub_scraper.core.logging_config import configure_logging
from sub_scraper.core.resilience import CircuitBreaker, CircuitOpen, backoff_delay
from sub_scraper.scrapers.base import BuildCmd, DownloadStatus, Track

configure_logging(level=logging.WARNING)  # keep test output clean

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            fn()
            _RESULTS.append((name, True, ""))
            print(f"  PASS  {name}")
        except AssertionError as exc:
            _RESULTS.append((name, False, str(exc)))
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            _RESULTS.append((name, False, repr(exc)))
            print(f"  ERROR {name}: {exc!r}")
        return fn
    return deco


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeScraper:
    """A scraper whose download_command runs a small shell script template."""

    log_prefix = "[fake]"

    def __init__(self, script: str) -> None:
        self._script = script

    def fetch_library(self, **kwargs):
        return []

    def download_command(self, track: Track, output_dir: str, quality: str, fmt: str) -> BuildCmd:
        script = self._script

        def build(tmp: Path) -> list:
            return ["sh", "-c", script.format(tmp=tmp, fmt=fmt, id=track.id)]

        return build


def make_track(i: int = 0) -> Track:
    return Track(id=f"t{i}", title=f"Title {i}", artist="Artist",
                 url=f"https://example.test/page/{i}")


def run_job(manager: DownloadManager, source: str, track: Track, out: str) -> Track:
    logs: list[str] = []
    job = DownloadJob(track, source, out, "320k", "mp3", on_log=logs.append)
    manager.submit(job).result(timeout=30)
    return track


# ---------------------------------------------------------------------------
# Engine tests (async subprocess orchestration on a background loop)
# ---------------------------------------------------------------------------

WRITE_OK = 'head -c 8192 /dev/zero > "{tmp}/song_{id}.{fmt}"'


@check("engine: successful download verifies size + checksum and records index")
def _t_success():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        index = DownloadIndex(Path(d) / "idx.json")
        mgr = DownloadManager(max_workers=4, retry_limit=2)
        mgr.configure_spotify(FakeScraper(WRITE_OK))
        mgr.configure_index(index)
        mgr.start()
        try:
            t = run_job(mgr, "spotify", make_track(1), out)
        finally:
            mgr.stop()
        assert t.status == DownloadStatus.COMPLETE, t.error
        assert t.local_path and Path(t.local_path).exists(), "file missing"
        assert t.size_bytes == 8192, t.size_bytes
        assert t.checksum and len(t.checksum) == 64, t.checksum
        assert index.contains("spotify", "t1"), "not recorded in index"


@check("engine: concurrent batch all complete")
def _t_batch():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        mgr = DownloadManager(max_workers=4, retry_limit=1)
        mgr.configure_spotify(FakeScraper(WRITE_OK))
        mgr.start()
        try:
            tracks = [make_track(i) for i in range(10)]
            logs: list[str] = []
            jobs = [DownloadJob(t, "spotify", out, "320k", "mp3", on_log=logs.append) for t in tracks]
            futs = [mgr.submit(j) for j in jobs]
            for f in futs:
                f.result(timeout=30)
        finally:
            mgr.stop()
        assert all(t.status == DownloadStatus.COMPLETE for t in tracks), \
            [t.error for t in tracks if t.error]
        assert len({Path(t.local_path).name for t in tracks}) == 10, "expected 10 distinct files"


@check("engine: retries transient failures then succeeds")
def _t_retry():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        counter = Path(d) / "count"
        script = (
            'n=$(cat "%s" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "%s"; '
            'if [ "$n" -le 2 ]; then echo "transient $n" >&2; exit 1; fi; '
            'head -c 8192 /dev/zero > "{tmp}/song.{fmt}"'
        ) % (counter, counter)
        mgr = DownloadManager(max_workers=1, retry_limit=4, retry_base_delay=0.01, retry_max_delay=0.05)
        mgr.configure_spotify(FakeScraper(script))
        mgr.start()
        try:
            t = run_job(mgr, "spotify", make_track(2), out)
        finally:
            mgr.stop()
        assert t.status == DownloadStatus.COMPLETE, t.error
        assert int(counter.read_text()) == 3, "expected 2 failures then success"


@check("engine: circuit breaker trips and fast-fails further work")
def _t_breaker():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        mgr = DownloadManager(
            max_workers=1, retry_limit=1,
            breaker_threshold=2, breaker_cooldown=60.0,
        )
        mgr.configure_spotify(FakeScraper('echo boom >&2; exit 1'))
        mgr.start()
        try:
            results = []
            for i in range(5):
                t = run_job(mgr, "spotify", make_track(100 + i), out)
                results.append(t)
        finally:
            mgr.stop()
        assert all(t.status == DownloadStatus.FAILED for t in results)
        circuit_failures = [t for t in results if "circuit open" in (t.error or "")]
        assert circuit_failures, f"breaker never tripped: {[t.error for t in results]}"


@check("engine: rejects corrupt (too-small) output")
def _t_corrupt():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        mgr = DownloadManager(max_workers=1, retry_limit=1)
        mgr.configure_spotify(FakeScraper('head -c 10 /dev/zero > "{tmp}/song.{fmt}"'))
        mgr.start()
        try:
            t = run_job(mgr, "spotify", make_track(3), out)
        finally:
            mgr.stop()
        assert t.status == DownloadStatus.FAILED
        assert "corrupt" in (t.error or "").lower(), t.error


@check("engine: fails cleanly when no audio is produced")
def _t_no_audio():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        mgr = DownloadManager(max_workers=1, retry_limit=1)
        mgr.configure_spotify(FakeScraper('echo nothing; exit 0'))
        mgr.start()
        try:
            t = run_job(mgr, "spotify", make_track(4), out)
        finally:
            mgr.stop()
        assert t.status == DownloadStatus.FAILED
        assert "no audio" in (t.error or "").lower(), t.error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@check("helpers: direct-media detection + safe filename")
def _t_helpers():
    assert _is_direct_media("https://cdn.example.com/a/b.mp3")
    assert _is_direct_media("https://cdn.example.com/x.flac?token=1")
    assert not _is_direct_media("https://open.spotify.com/track/123")
    assert not _is_direct_media("")
    name = _safe_filename(Track(id="x", title="Hi/There:!", artist="A*B"), "mp3")
    assert name.endswith(".mp3") and "/" not in name and ":" not in name, name


# ---------------------------------------------------------------------------
# Download index
# ---------------------------------------------------------------------------

@check("index: record, contains, prune-missing, persist, clear")
def _t_index():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "idx.json"
        f = Path(d) / "song.mp3"
        f.write_bytes(b"x" * 2048)
        idx = DownloadIndex(path)
        t = Track(id="abc", title="T", artist="A", local_path=str(f),
                  size_bytes=2048, checksum="deadbeef")
        idx.record("spotify", t)
        assert idx.contains("spotify", "abc")
        assert not idx.contains("spotify", "missing")
        # Reload from disk -> persistence works.
        assert DownloadIndex(path).contains("spotify", "abc")
        # Delete file -> pruned and reported as not downloaded.
        f.unlink()
        assert not idx.contains("spotify", "abc")
        assert not DownloadIndex(path).contains("spotify", "abc"), "prune not persisted"
        # Clear.
        idx.record("spotify", Track(id="z", title="T", artist="A", local_path=str(path)))
        assert len(idx) == 1
        assert idx.clear() == 1
        assert len(idx) == 0


@check("index: filesystem fallback matches pre-existing files")
def _t_index_fs():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "Artist - Title 5.mp3").write_bytes(b"x" * 4096)
        idx = DownloadIndex(Path(d) / "idx.json")
        t = Track(id="t5", title="Title 5", artist="Artist")
        assert idx.file_exists_for(d, t) is not None
        assert idx.file_exists_for(d, Track(id="t6", title="Nope", artist="X")) is None


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------

@check("resilience: backoff grows, is bounded, and is jittered")
def _t_backoff():
    d1 = [backoff_delay(1, base=1, cap=30) for _ in range(50)]
    d4 = [backoff_delay(4, base=1, cap=30) for _ in range(50)]
    assert all(0 < x <= 1 for x in d1), (min(d1), max(d1))     # raw=1 -> [0.5,1]
    assert all(4 <= x <= 8 for x in d4), (min(d4), max(d4))    # raw=8 -> [4,8]
    assert len(set(d1)) > 1, "no jitter"
    assert backoff_delay(20, base=1, cap=30, jitter=False) == 30  # capped


@check("resilience: circuit breaker trips, blocks, and resets")
def _t_circuit():
    cb = CircuitBreaker(threshold=3, cooldown=0.2, name="x")
    assert not cb.record(False)
    assert not cb.record(False)
    assert cb.record(False) is True       # third failure trips it
    assert cb.is_open
    try:
        cb.before()
        raise AssertionError("expected CircuitOpen")
    except CircuitOpen:
        pass
    time.sleep(0.25)
    cb.before()                            # cooled down -> allowed again
    assert not cb.is_open
    cb.record(False)
    cb.reset()
    assert not cb.is_open


# ---------------------------------------------------------------------------
# Networking (aiohttp streaming downloader against a local server)
# ---------------------------------------------------------------------------

@check("net: streams to disk with size + sha256, honours 429, detects truncation")
def _t_net():
    import hashlib
    from aiohttp import web
    from sub_scraper.core.net import HttpClient
    from sub_scraper.core.resilience import CircuitBreaker

    payload = bytes(range(256)) * 1024  # 256 KiB, deterministic
    expected_sha = hashlib.sha256(payload).hexdigest()
    state = {"429_hits": 0}

    async def ok(_req):
        return web.Response(body=payload, content_type="application/octet-stream")

    async def flaky(_req):
        state["429_hits"] += 1
        if state["429_hits"] == 1:
            return web.Response(status=429, headers={"Retry-After": "0"})
        return web.Response(body=payload)

    async def scenario():
        app = web.Application()
        app.add_routes([web.get("/ok", ok), web.get("/flaky", flaky)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            with tempfile.TemporaryDirectory() as d:
                async with HttpClient(retry_limit=2, chunk_size=64 * 1024, timeout=5) as client:
                    # 1) happy path
                    dest = Path(d) / "a.bin"
                    res = await client.stream_download(base + "/ok", dest)
                    assert res.size == len(payload), res.size
                    assert res.sha256 == expected_sha, "checksum mismatch"
                    assert dest.exists() and not dest.with_name(dest.name + ".part").exists()

                    # 2) 429 then success (retry honoured)
                    res2 = await client.stream_download(base + "/flaky", Path(d) / "b.bin")
                    assert res2.sha256 == expected_sha
                    assert state["429_hits"] == 2, state

                    # 3) size integrity check: a declared/actual mismatch must
                    #    be caught and leave no partial file behind.
                    cb = CircuitBreaker(threshold=10, cooldown=1)
                    raised = False
                    try:
                        await client.stream_download(
                            base + "/ok", Path(d) / "c.bin",
                            expected_size=len(payload) + 1, breaker=cb,
                        )
                    except Exception as exc:  # noqa: BLE001
                        raised = "mismatch" in str(exc) or "size" in str(exc)
                    assert raised, "size mismatch not detected"
                    assert not (Path(d) / "c.bin").exists(), "partial left on disk"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Logo generation (PIL)
# ---------------------------------------------------------------------------

@check("logo: renders a square RGBA image at the requested size")
def _t_logo():
    from sub_scraper.gui.logo import make_logo_image
    img = make_logo_image(160)
    assert img.size == (160, 160), img.size
    assert img.mode == "RGBA", img.mode
    # Not a blank image: there must be opaque, coloured pixels.
    colors = img.getcolors(maxcolors=100000)
    assert colors and len(colors) > 5, "logo looks empty"


# ---------------------------------------------------------------------------
# Artwork loader (off-thread fetch + decode + disk cache, against local server)
# ---------------------------------------------------------------------------

@check("artwork: fetches off-thread, decodes square+rounded, caches to disk")
def _t_artwork():
    import http.server
    import io
    import socketserver
    import threading
    from PIL import Image
    import sub_scraper.gui.artwork as art
    from sub_scraper.scrapers.spotify import _thumb_url

    # Placeholder is a real, non-blank square RGBA image.
    ph = art.make_placeholder(92)
    assert ph is not None and ph.size == (92, 92) and ph.mode == "RGBA"
    assert len(ph.getcolors(maxcolors=100000)) > 5, "placeholder looks blank"

    # Spotify picks the smallest cover >= 64px.
    assert _thumb_url([{"url": "a", "width": 640}, {"url": "b", "width": 64}]) == "b"
    assert _thumb_url([]) == ""

    # Serve a real JPEG over localhost and load it end-to-end.
    buf = io.BytesIO()
    Image.new("RGB", (500, 300), (200, 90, 30)).save(buf, "JPEG")
    payload = buf.getvalue()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/cover.jpg"

    with tempfile.TemporaryDirectory() as d:
        art._CACHE_DIR = Path(d)
        done = threading.Event()
        got: dict = {}

        def on_ready(u, img):
            got["url"], got["img"] = u, img
            done.set()

        loader = art.ArtworkLoader(store_size=92, workers=2)
        try:
            loader.request("", on_ready)          # empty url is a no-op
            loader.request(url, on_ready)
            assert done.wait(10), "loader never called back"
            img = got["img"]
            assert got["url"] == url
            assert img.size == (92, 92) and img.mode == "RGBA"
            # Centre-cropped + rounded: transparent corner, opaque centre.
            assert img.getpixel((0, 0))[3] == 0, "corner not transparent"
            assert img.getpixel((46, 46))[3] == 255, "centre not opaque"
            assert len(list(Path(d).glob("*.png"))) == 1, "disk cache not written"
        finally:
            loader.close()
            srv.shutdown()

        # A fresh loader serves the same URL from the disk cache (no network).
        loader2 = art.ArtworkLoader(store_size=92, workers=2)
        done2 = threading.Event()
        try:
            loader2.request(url, lambda u, i: done2.set())
            assert done2.wait(10), "disk-cache reload failed"
        finally:
            loader2.close()


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------

@check("config: new fields present with sane defaults")
def _t_config():
    c = Config()
    for field in ("max_concurrent", "retry_limit", "breaker_threshold",
                  "breaker_cooldown", "io_chunk_bytes", "hide_downloaded",
                  "verify_downloads", "request_timeout",
                  "auto_update_ytdlp", "autosync", "autosync_interval_hours"):
        assert hasattr(c, field), field
    assert c.hide_downloaded is True
    assert 64 * 1024 <= c.io_chunk_bytes <= 256 * 1024
    assert c.auto_update_ytdlp is True
    assert c.autosync == {} and c.autosync_interval_hours == 6.0
    # Two instances must not share the same mutable autosync dict.
    assert Config().autosync is not Config().autosync


@check("config: environment variables override stored credentials + report locked")
def _t_config_env_override():
    import os

    fields = {
        "SPOTIFY_CLIENT_ID": "spotify_client_id",
        "SPOTIFY_CLIENT_SECRET": "spotify_client_secret",
        "SOUNDCLOUD_USERNAME": "soundcloud_username",
        "SOUNDCLOUD_AUTH_TOKEN": "soundcloud_auth_token",
    }
    saved = {env: os.environ.get(env) for env in fields}
    try:
        for env in fields:
            os.environ[env] = f"env-{env.lower()}"
        cfg = Config.load()
        for env, attr in fields.items():
            assert getattr(cfg, attr) == f"env-{env.lower()}", attr
        # Every env-supplied field is reported as locked so the UI/server can
        # protect it from being overwritten or cleared.
        assert Config.env_locked_fields() == set(fields.values())
    finally:
        for env, prev in saved.items():
            if prev is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = prev


@check("config: .env parser handles comments, blanks, quotes")
def _t_config_dotenv_parse():
    from sub_scraper.core.config import _parse_env_file

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".env"
        p.write_text(
            "# a comment\n"
            "\n"
            "SPOTIFY_CLIENT_ID=plain_id\n"
            'SPOTIFY_CLIENT_SECRET="quoted secret"\n'
            "SOUNDCLOUD_USERNAME = spaced_user \n"
            "MALFORMED_NO_EQUALS\n"
        )
        parsed = _parse_env_file(p)
    assert parsed["SPOTIFY_CLIENT_ID"] == "plain_id", parsed
    assert parsed["SPOTIFY_CLIENT_SECRET"] == "quoted secret", parsed
    assert parsed["SOUNDCLOUD_USERNAME"] == "spaced_user", parsed
    assert "MALFORMED_NO_EQUALS" not in parsed, parsed
    assert _parse_env_file(Path(d) / "does-not-exist.env") == {}


# ---------------------------------------------------------------------------
# Progress parsing (pure + end-to-end through the engine)
# ---------------------------------------------------------------------------

@check("progress: parses yt-dlp/spotdl lines and ignores unrelated text")
def _t_progress_parse():
    from sub_scraper.core.progress import parse_progress

    i = parse_progress("[download]  45.2% of 5.00MiB at 1.20MiB/s ETA 00:03")
    assert i is not None and abs(i.fraction - 0.452) < 1e-6, i
    assert i.speed == "1.20MiB/s" and i.eta == "00:03", i
    assert parse_progress("[download] 100% of 5.00MiB in 00:04").fraction == 1.0
    # A speed token alone (no [download]) still counts as progress.
    assert parse_progress('Downloading: 78% at 900KiB/s') is not None
    # A bare percentage in unrelated text is NOT progress.
    assert parse_progress("Found 100% match for track") is None
    assert parse_progress("just a log line") is None
    assert parse_progress("") is None


@check("progress: engine forwards live progress from downloader stdout")
def _t_engine_progress():
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        script = (
            'echo "[download]   0.0% of 1.00MiB at 100KiB/s ETA 00:10"; '
            'echo "[download]  50.0% of 1.00MiB at 200KiB/s ETA 00:05"; '
            'echo "[download] 100% of 1.00MiB in 00:05"; '
            'head -c 8192 /dev/zero > "{tmp}/song.{fmt}"'
        )
        mgr = DownloadManager(max_workers=1, retry_limit=1)
        mgr.configure_spotify(FakeScraper(script))
        mgr.start()
        seen: list[float] = []
        try:
            t = make_track(77)
            job = DownloadJob(t, "spotify", out, "320k", "mp3",
                              on_progress=lambda tr: seen.append(tr.progress))
            mgr.submit(job).result(timeout=30)
        finally:
            mgr.stop()
        assert t.status == DownloadStatus.COMPLETE, t.error
        assert any(abs(x - 0.5) < 1e-6 for x in seen), seen   # mid-download tick
        assert t.progress == 1.0, t.progress                  # finalised


# ---------------------------------------------------------------------------
# Desktop integration (pure command builders)
# ---------------------------------------------------------------------------

@check("desktop: builds correct per-platform reveal/open/notify commands")
def _t_desktop():
    from sub_scraper.core import desktop as d

    assert d.reveal_command("/m/x.mp3", "darwin") == ["open", "-R", "/m/x.mp3"]
    assert d.reveal_command("/m/x.mp3", "linux") == ["xdg-open", "/m"]
    assert d.reveal_command("C:/m/x.mp3", "win")[0] == "explorer"
    assert "/select," in d.reveal_command("C:/m/x.mp3", "win")[1]
    assert d.reveal_command("", "linux") is None

    assert d.open_command("/m/x.mp3", "linux") == ["xdg-open", "/m/x.mp3"]
    assert d.open_command("/m/x.mp3", "darwin") == ["open", "/m/x.mp3"]
    assert d.open_command("/m/x.mp3", "win") is None  # uses os.startfile instead

    assert d.notify_command("T", "M", "linux")[0] == "notify-send"
    assert d.notify_command("T", "M", "win") is None
    cmd = d.notify_command('Ti"tle', "msg", "darwin")
    assert cmd[0] == "osascript" and '\\"' in cmd[2], cmd  # quotes escaped


# ---------------------------------------------------------------------------
# Updater (pure pip-output classifier)
# ---------------------------------------------------------------------------

@check("updater: classifies pip output as updated/current/failed")
def _t_updater():
    from sub_scraper.core.updater import classify_pip_output, CURRENT, FAILED, UPDATED

    assert classify_pip_output(0, "Successfully installed yt-dlp-2024.1.1") == UPDATED
    assert classify_pip_output(0, "Requirement already satisfied: yt-dlp") == CURRENT
    assert classify_pip_output(1, "ERROR: could not install") == FAILED


# ---------------------------------------------------------------------------
# Download stats
# ---------------------------------------------------------------------------

@check("index: stats counts total, today, and bytes on disk")
def _t_index_stats():
    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        f1 = d / "a.mp3"; f1.write_bytes(b"x" * 2048)
        f2 = d / "b.mp3"; f2.write_bytes(b"y" * 4096)
        idx = DownloadIndex(d / "idx.json")
        idx.record("spotify", Track(id="a", title="A", artist="X",
                                    local_path=str(f1), size_bytes=2048))
        idx.record("spotify", Track(id="b", title="B", artist="Y",
                                    local_path=str(f2), size_bytes=4096))
        s = idx.stats()
        assert s["total"] == 2, s
        assert s["today"] == 2, s            # just recorded -> counts as today
        assert s["bytes"] == 2048 + 4096, s


# ---------------------------------------------------------------------------
# Auto-sync (filtering logic with an injected fake scraper/manager)
# ---------------------------------------------------------------------------

@check("autosync: queues only tracks not already on disk; membership reads config")
def _t_autosync():
    from sub_scraper.core.autosync import AutoSyncManager, SyncEntry

    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        cfg = Config()
        cfg.download_path = str(d / "music")
        Path(cfg.download_path).mkdir(parents=True)
        idx = DownloadIndex(d / "idx.json")

        # t0 is already downloaded (recorded with an existing file).
        existing = Path(cfg.download_path) / "x.mp3"
        existing.write_bytes(b"z" * 2048)
        idx.record("spotify", Track(id="t0", title="T0", artist="A",
                                    local_path=str(existing), size_bytes=2048))

        submitted: list = []

        class FakeMgr:
            def configure_spotify(self, s): pass
            def configure_soundcloud(self, s): pass
            def submit_batch(self, jobs, chunk_size=0): submitted.extend(jobs)

        class FakeScraper2:
            def fetch_playlist_tracks(self, pid):
                return [make_track(0), make_track(1), make_track(2)]  # t0,t1,t2

        mgr = AutoSyncManager(cfg, FakeMgr(), idx,
                              scraper_factory=lambda c, s: FakeScraper2())
        n = mgr.sync_now(SyncEntry("spotify", "pl1", "My PL"))
        assert n == 2, n
        assert {j.track.id for j in submitted} == {"t1", "t2"}, submitted

        # Membership is read straight from config (no disk write needed here).
        cfg.autosync = {"spotify:plY": {"source": "spotify",
                                        "playlist_id": "plY", "name": "Y"}}
        mgr2 = AutoSyncManager(cfg, FakeMgr(), idx,
                               scraper_factory=lambda c, s: FakeScraper2())
        assert mgr2.is_synced("spotify", "plY")
        assert [e.name for e in mgr2.entries()] == ["Y"]
        assert mgr2.interval_seconds() == 6.0 * 3600


@check("web: submit wires the source's scraper into the manager (real downloads work)")
def _t_web_manager_wiring():
    # Regression guard: the web server used to never call configure_spotify/
    # configure_soundcloud, so every real (non-demo) download failed with
    # "scraper not configured". _ensure_manager_scraper must wire it.
    import sub_scraper.web.server as srv
    from sub_scraper.scrapers.spotify import SpotifyScraper
    from sub_scraper.scrapers.soundcloud import SoundCloudScraper

    srv._config.spotify_client_id = "cid"
    srv._config.spotify_client_secret = "secret"
    srv._config.soundcloud_username = "someuser"

    mgr = DownloadManager.from_config(srv._config)
    # A freshly built manager has no scrapers — downloads would fail here.
    assert mgr._spotify is None and mgr._soundcloud is None

    s1 = srv._ensure_manager_scraper(mgr, "spotify")
    assert isinstance(s1, SpotifyScraper) and mgr._spotify is s1, "spotify not wired"
    s2 = srv._ensure_manager_scraper(mgr, "soundcloud")
    assert isinstance(s2, SoundCloudScraper) and mgr._soundcloud is s2, "soundcloud not wired"


@check("web: end-to-end — a submitted track actually downloads via the wired scraper")
def _t_web_download_e2e():
    # Proves the blocker fix end to end: the web wiring helper + a scraper must
    # carry a real track all the way to COMPLETE. Before the fix the manager had
    # no scraper and this path raised "scraper not configured".
    import sub_scraper.web.server as srv
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "music")
        idx = DownloadIndex(Path(d) / "idx.json")
        mgr = DownloadManager(max_workers=2, retry_limit=1)
        mgr.configure_index(idx)
        mgr.start()
        # Swap the scraper factory the web server uses so no network is hit.
        orig = srv.build_scraper
        srv.build_scraper = lambda cfg, source: FakeScraper(WRITE_OK)
        try:
            srv._ensure_manager_scraper(mgr, "spotify")
            t = run_job(mgr, "spotify", make_track(42), out)
        finally:
            srv.build_scraper = orig
            mgr.stop()
        assert t.status == DownloadStatus.COMPLETE, t.error
        assert t.local_path and Path(t.local_path).exists(), "downloaded file missing"


def main() -> int:
    print("Running Sub-Scraper core tests\n")
    _t_success(); _t_batch(); _t_retry(); _t_breaker(); _t_corrupt(); _t_no_audio()
    _t_helpers()
    _t_index(); _t_index_fs()
    _t_backoff(); _t_circuit()
    _t_net()
    _t_artwork()
    _t_progress_parse(); _t_engine_progress()
    _t_desktop(); _t_updater()
    _t_index_stats(); _t_autosync()
    _t_config()
    _t_web_manager_wiring()
    _t_web_download_e2e()
    _t_logo()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} passed")
    failures = [(n, m) for n, ok, m in _RESULTS if not ok]
    for name, msg in failures:
        print(f"  - {name}: {msg}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
