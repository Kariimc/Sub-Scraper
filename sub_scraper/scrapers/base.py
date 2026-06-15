from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    url: str = ""
    cover_url: str = ""
    status: DownloadStatus = DownloadStatus.PENDING
    local_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}"


class BaseScraper(ABC):
    @abstractmethod
    def fetch_library(self, **kwargs) -> list[Track]: ...

    @abstractmethod
    def download(
        self,
        track: Track,
        output_dir: str,
        quality: str,
        fmt: str,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str: ...
