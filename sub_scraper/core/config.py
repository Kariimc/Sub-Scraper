import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".sub_scraper" / "config.json"


@dataclass
class Config:
    # --- Credentials -----------------------------------------------------
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    soundcloud_auth_token: str = ""
    soundcloud_username: str = ""
    gdrive_credentials_path: str = ""
    gdrive_folder_id: str = ""

    # --- Output ----------------------------------------------------------
    download_path: str = str(Path.home() / "Music" / "SubScraper")
    output_format: str = "mp3"
    audio_quality: str = "320k"

    # --- Throughput / concurrency ---------------------------------------
    # MAX_CONCURRENT_DOWNLOADS: how many tracks download in parallel. The async
    # engine caps in-flight work to this via a semaphore.
    max_concurrent: int = 6
    # Parallel fragment connections *within* a single track (yt-dlp -N).
    concurrent_fragments: int = 4
    # Legacy queue chunk size (kept for compatibility; concurrency is now the
    # semaphore's job, so this is no longer surfaced in the UI).
    chunk_size: int = 50
    # Stream chunk for direct HTTP downloads, in bytes (64-256 KiB recommended).
    io_chunk_bytes: int = 1 << 17  # 128 KiB

    # --- Resilience ------------------------------------------------------
    # RETRY_LIMIT: attempts per track before giving up.
    retry_limit: int = 3
    retry_base_delay: float = 1.0   # backoff base (seconds)
    retry_max_delay: float = 30.0   # backoff cap (seconds)
    # Circuit breaker: trip after N consecutive failures for one source, then
    # pause requests to it for `breaker_cooldown` seconds.
    breaker_threshold: int = 6
    breaker_cooldown: float = 30.0
    request_timeout: float = 30.0   # per-operation socket timeout (seconds)
    verify_downloads: bool = True   # post-download size + checksum validation

    # --- Library view ----------------------------------------------------
    hide_downloaded: bool = True    # hide tracks already downloaded

    # --- Maintenance -----------------------------------------------------
    # Upgrade yt-dlp on launch (in the background) so extraction stays working.
    auto_update_ytdlp: bool = True

    # --- Auto-sync -------------------------------------------------------
    # Playlists kept in sync, keyed by "<source>:<playlist_id>" -> metadata.
    autosync: dict = field(default_factory=dict)
    # How often the background scheduler re-checks synced playlists.
    autosync_interval_hours: float = 6.0

    # --- Integrations ----------------------------------------------------
    use_gdrive: bool = False

    def save(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file then rename so a crash mid-write never corrupts.
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2))
            tmp.replace(CONFIG_PATH)
        except OSError:
            pass  # best-effort; never crash the app over a config save failure

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            data = json.loads(CONFIG_PATH.read_text())
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**valid)
        except (json.JSONDecodeError, TypeError):
            return cls()
