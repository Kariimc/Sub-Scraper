import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".sub_scraper" / "config.json"


@dataclass
class Config:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    soundcloud_auth_token: str = ""
    soundcloud_username: str = ""
    gdrive_credentials_path: str = ""
    gdrive_folder_id: str = ""
    download_path: str = str(Path.home() / "Music" / "SubScraper")
    output_format: str = "mp3"
    audio_quality: str = "320k"
    max_concurrent: int = 6
    chunk_size: int = 50
    concurrent_fragments: int = 4
    retry_limit: int = 3
    use_gdrive: bool = False

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))

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
