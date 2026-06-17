"""Build a configured scraper for a source from :class:`Config`.

Centralised so the library panel and the auto-sync scheduler construct scrapers
the same way (same credentials, same fragment count) instead of duplicating the
wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseScraper
from .soundcloud import SoundCloudScraper
from .spotify import SpotifyScraper

if TYPE_CHECKING:
    from ..core.config import Config

#: Canonical source keys used across the app + index.
SPOTIFY = "spotify"
SOUNDCLOUD = "soundcloud"


def build_scraper(config: "Config", source: str) -> BaseScraper:
    """Return a scraper for ``source`` ("spotify" / "soundcloud")."""
    try:
        frags = int(config.concurrent_fragments)
    except (ValueError, TypeError):
        frags = 4

    if source == SPOTIFY:
        return SpotifyScraper(
            config.spotify_client_id,
            config.spotify_client_secret,
            concurrent_fragments=frags,
        )
    if source == SOUNDCLOUD:
        return SoundCloudScraper(
            auth_token=config.soundcloud_auth_token,
            username=config.soundcloud_username,
            concurrent_fragments=frags,
        )
    raise ValueError(f"unknown source: {source!r}")
