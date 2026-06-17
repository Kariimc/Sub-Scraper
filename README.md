<div align="center">
  <img src="assets/logo.png" width="120" alt="Sub-Scraper logo"/>

  # Sub-Scraper

  **A clean desktop app for pulling your Spotify & SoundCloud libraries down to local audio — fast, resiliently, and without re-downloading what you already have.**
</div>

---

## Features

- 🎧 **Spotify & SoundCloud** — liked songs and playlists, with a guided first-run setup.
- 🚀 **High-throughput async engine** — many downloads in parallel, parallel
  fragments per track, and `aria2c` (16-way) when present.
- 🛡️ **Resilient by design** — jittered exponential-backoff retries, per-source
  circuit breakers, and post-download size + checksum verification.
- 📊 **Live progress** — per-track progress bars with speed + ETA, parsed
  straight from the downloader, plus a batch progress strip.
- ▶️ **30-second previews** — audition a track before downloading (uses the
  `ffplay`/`mpv` already on your system).
- ✅ **Multi-select** — click to (de)select, **shift-click** for a range, then
  download just the selection.
- 🔄 **Playlist auto-sync** — flag a playlist and Sub-Scraper periodically grabs
  only the newly-added tracks in the background.
- 🔔 **Finish notifications** — a native OS notification (and an in-app toast)
  when a batch completes, so you can walk away.
- 📂 **Right-click a track** — reveal it in your file manager, play the file, or
  copy "Artist - Title".
- 🩹 **Self-healing yt-dlp** — silently updates yt-dlp on launch so extraction
  doesn't break when YouTube/SoundCloud change.
- 📈 **Library stats** — total downloaded, how many today, and bytes on disk.
- 🙈 **Hides what you've already got** — downloaded tracks drop out of the list
  by default (toggle "Show downloaded" to reveal them).
- 🖼️ **Track artwork** — cover art loads lazily off the UI thread (memory + disk
  cached, keyed by URL) so even a 1000-track library stays smooth.
- ☁️ **Optional Google Drive sync** — see [`GDRIVE_SETUP.md`](GDRIVE_SETUP.md).
- 🎨 **Professional blue / orange / white UI** with a custom brand mark.

## Install & run

```bash
./setup.sh        # creates a venv and installs requirements (Windows: setup.bat)
./run.sh          # launches the app   (Windows: run.bat)
```

External tools used for extraction: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)
and [`spotdl`](https://github.com/spotDL/spotify-downloader) (installed via
`requirements.txt`); `ffmpeg` is required for audio conversion and `aria2c` is
an optional accelerator.

## Download-engine architecture

The engine (`sub_scraper/core/`) is intentionally modular:

| Module | Responsibility |
| --- | --- |
| `download_manager.py` | Async orchestration: an `asyncio` loop on a daemon thread, a concurrency semaphore, retry/backoff, and per-source circuit breakers. |
| `net.py` | A persistent, **pooled** `aiohttp` session and a **chunked streaming** downloader (64–256 KiB) with integrity checks — for direct media URLs. |
| `resilience.py` | `CircuitBreaker` + jittered `backoff_delay` (shared by both download paths). |
| `library_index.py` | JSON-backed record of completed downloads (powers "hide downloaded"). |
| `logging_config.py` | Structured `subscraper` logger; INFO+ is mirrored into the in-app log. |
| `scrapers/base.py` | Sync **and** async isolated-temp-dir runners + integrity finalisation. |

**Throughput.** Work is non-blocking: subprocess downloaders are spawned with
`asyncio.create_subprocess_exec` and their stdout is streamed asynchronously, so
a single loop thread supervises many concurrent downloads. An
`asyncio.Semaphore` caps in-flight work; within a track, parallel fragments and
`aria2c` push toward line speed. Direct media URLs are streamed by the pooled
`HttpClient`, reusing TCP/TLS connections instead of re-handshaking.

**Stability.** Transient failures (drops, `429`s) are retried with exponential
backoff + equal jitter (honouring `Retry-After`). If one source keeps failing,
its **circuit breaker** trips and pauses requests to it for a cooldown instead
of hammering a dead host. File handles and sockets are always closed via context
managers, and temp directories are cleaned up even on cancellation.

**Integrity.** Every file is checked for a sane minimum size and a streamed
SHA-256 before it counts as done; direct transfers also verify Content-Length.

## Configuration

Settings live in `~/.sub_scraper/config.json` (most are editable in the
**Settings** tab). Tune these to your network and the target servers:

| Variable | Default | What it does |
| --- | --- | --- |
| `max_concurrent` | `6` | **MAX_CONCURRENT_DOWNLOADS** — parallel downloads (semaphore size). |
| `concurrent_fragments` | `4` | Parallel fragment connections within one track (yt-dlp `-N`). |
| `io_chunk_bytes` | `131072` | Stream chunk for direct downloads (64–256 KiB). |
| `retry_limit` | `3` | **RETRY_LIMIT** — attempts per track before giving up. |
| `retry_base_delay` | `1.0` | Backoff base, seconds. |
| `retry_max_delay` | `30.0` | Backoff cap, seconds. |
| `breaker_threshold` | `6` | Consecutive failures that trip a source's circuit breaker. |
| `breaker_cooldown` | `30.0` | Seconds a tripped breaker pauses that source. |
| `request_timeout` | `30.0` | Per-operation socket timeout, seconds. |
| `verify_downloads` | `true` | Post-download size + SHA-256 verification. |
| `hide_downloaded` | `true` | Hide already-downloaded tracks from the library. |
| `output_format` / `audio_quality` | `mp3` / `320k` | Output container and bitrate. |
| `auto_update_ytdlp` | `true` | Upgrade yt-dlp in the background on launch. |
| `autosync_interval_hours` | `6.0` | How often auto-synced playlists are re-checked. |
| `autosync` | `{}` | Playlists kept in sync (managed from the Library tab). |

## Tests

A headless suite exercises the real async engine (against tiny shell commands),
the streaming downloader (against a local `aiohttp` server), the index, and the
resilience primitives — no GUI required:

```bash
python3 tests/test_core.py
```
