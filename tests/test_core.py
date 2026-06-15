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
# Config round-trip
# ---------------------------------------------------------------------------

@check("config: new fields present with sane defaults")
def _t_config():
    c = Config()
    for field in ("max_concurrent", "retry_limit", "breaker_threshold",
                  "breaker_cooldown", "io_chunk_bytes", "hide_downloaded",
                  "verify_downloads", "request_timeout"):
        assert hasattr(c, field), field
    assert c.hide_downloaded is True
    assert 64 * 1024 <= c.io_chunk_bytes <= 256 * 1024


def main() -> int:
    print("Running Sub-Scraper core tests\n")
    _t_success(); _t_batch(); _t_retry(); _t_breaker(); _t_corrupt(); _t_no_audio()
    _t_helpers()
    _t_index(); _t_index_fs()
    _t_backoff(); _t_circuit()
    _t_net()
    _t_config()
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
