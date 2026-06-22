import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".sub_scraper" / "config.json"

# Credential fields overridable via environment variables.
# Set these in the Render/Railway dashboard to pre-authenticate a personal
# instance without ever committing secrets to the repo.
_ENV_CREDENTIAL_MAP: dict[str, str] = {
    "spotify_client_id":     "SPOTIFY_CLIENT_ID",
    "spotify_client_secret": "SPOTIFY_CLIENT_SECRET",
    "soundcloud_username":   "SOUNDCLOUD_USERNAME",
    "soundcloud_auth_token": "SOUNDCLOUD_AUTH_TOKEN",
}

# Optional ".env" support: drop the keys in a .env file once and they load
# automatically at startup — no shell config, no dashboard, no UI entry. The app
# opens already "logged in" and stays that way. A real environment variable
# always wins over a .env value.
_DOTENV_LOADED = False


def _parse_env_file(path: Path) -> "dict[str, str]":
    """Parse ``KEY=VALUE`` lines from a .env file.

    Ignores blank lines and ``#`` comments, and strips one layer of optional
    surrounding quotes from the value. Returns ``{}`` if the file is unreadable.
    """
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _load_dotenv() -> None:
    """Populate os.environ from the first .env file found, if any.

    Searched in order (earlier file wins; a real env var beats any file):
      1. ~/.sub_scraper/.env   — lives with your config; ideal for a personal box
      2. <project root>/.env   — handy for local runs and Docker images
      3. ./.env                — current working directory
    Tiny zero-dependency parser so the slim server image needs nothing extra.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    candidates = [
        CONFIG_PATH.parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path.cwd() / ".env",
    ]
    seen: set[Path] = set()
    for env_file in candidates:
        if env_file in seen or not env_file.is_file():
            continue
        seen.add(env_file)
        for key, value in _parse_env_file(env_file).items():
            # Don't clobber a real env var or an earlier .env file's value.
            if key not in os.environ:
                os.environ[key] = value


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
        _load_dotenv()
        if not CONFIG_PATH.exists():
            cfg = cls()
        else:
            try:
                data = json.loads(CONFIG_PATH.read_text())
                valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                cfg = cls(**valid)
            except (json.JSONDecodeError, TypeError):
                cfg = cls()
        # Env vars take precedence over stored credentials so a personal hosted
        # instance stays authenticated across container restarts without Settings.
        for field_name, env_var in _ENV_CREDENTIAL_MAP.items():
            val = os.environ.get(env_var, "").strip()
            if val:
                setattr(cfg, field_name, val)
        return cfg

    @staticmethod
    def env_locked_fields() -> set[str]:
        """Return field names whose value is pinned to an environment variable."""
        _load_dotenv()
        return {
            field_name
            for field_name, env_var in _ENV_CREDENTIAL_MAP.items()
            if os.environ.get(env_var, "").strip()
        }
