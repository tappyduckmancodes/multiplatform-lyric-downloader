"""
plugins/lrclib_plugin.py -- LRCLIB open lyrics database.

Free, no auth required, good community-sourced coverage.
Used as last-resort fallback when Spotify and Deezer both miss.
https://lrclib.net
"""

import logging
import requests

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo, _lrc_header

logger = logging.getLogger(__name__)

LRCLIB_URL = "https://lrclib.net/api/get"


class LRCLIBPlugin(LyricsPlugin):
    NAME = "LRCLIB"
    PRIORITY = 30
    CONFIG = []  # No auth needed

    def setup(self, config: dict) -> bool:
        self._enabled = True
        return True

    def fetch(self, track: TrackInfo) -> str | None:
        params = {
            "artist_name": track.primary_artist,
            "track_name": track.title,
            "album_name": track.album,
            "duration": track.duration_ms // 1000,
        }
        try:
            resp = requests.get(LRCLIB_URL, params=params, timeout=10)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.debug("LRCLIB request failed: %s", e)
            return None

        synced = data.get("syncedLyrics")
        plain = data.get("plainLyrics")
        if synced:
            return _lrc_header(track, lyrics_source=self.NAME) + synced
        if plain:
            return _lrc_header(track, lyrics_source=self.NAME) + plain
        return None
