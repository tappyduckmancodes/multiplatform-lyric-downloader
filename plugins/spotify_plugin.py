"""
plugins/spotify_plugin.py -- Spotify internal lyrics API.

Uses the spclient color-lyrics endpoint via a Bearer token.
Auth is handled externally by SpotifyAuth and passed in via setup().
"""

import logging
import time
import requests
from pathlib import Path

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo, _lrc_header, _ms_to_lrc_timestamp, is_valid_album

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = Path(__file__).parent.parent / ".cache" / "spotify_token.json"

LYRICS_URL = (
    "https://spclient.wg.spotify.com/color-lyrics/v2/track/{track_id}"
    "?format=json&vocalRemoval=false&market=from_token"
)


class SpotifyPlugin(LyricsPlugin):
    NAME = "Spotify"
    PRIORITY = 10
    _rate_limited_until: float = 0.0  # class-level: shared across all instances in a run
    CONFIG = [
        PluginConfig(
            name="Spotify Bearer Token",
            env_key="SPOTIFY_AUTH_TOKEN",
            description="Bearer token from DevTools -> Network -> color-lyrics request headers",
            required=False,
        ),
        PluginConfig(
            name="Spotify sp_dc Cookie",
            env_key="SPOTIFY_SP_DC",
            description="sp_dc cookie from DevTools -> Application -> Cookies (lasts weeks)",
            required=False,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._auth_headers: dict = {}
        self._auth: object = None   # SpotifyAuth instance, injected by downloader
        self._search_fn = None      # search fn, injected by downloader

    def setup(self, config: dict) -> bool:
        # Auth headers are injected by the downloader after SpotifyAuth resolves them.
        # This plugin is always enabled — SpotifyAuth handles the fallback/prompt logic.
        self._enabled = True
        return True

    def inject_headers(self, headers: dict, auth=None):
        """Called by downloader.py after SpotifyAuth resolves a token."""
        self._auth_headers = headers
        self._auth = auth  # stored so we can call notify_401() on 401 mid-run

    def inject_search_fn(self, fn):
        """
        Optionally inject a search function for cross-service lookups.
        fn(title, artist) -> spotify_track_id (str) or None.
        Used when track came from a non-Spotify source (Deezer, YouTube, etc.)
        and the user explicitly requests Spotify lyrics via -source spotify.
        """
        self._search_fn = fn

    def fetch(self, track: TrackInfo) -> str | None:
        # Global 429 cooldown: if we were rate-limited recently, skip immediately
        now = time.time()
        if now < SpotifyPlugin._rate_limited_until:
            remaining = int(SpotifyPlugin._rate_limited_until - now)
            logger.debug(
                "Spotify lyrics: skipping '%s' — rate limited, %ds cooldown remaining",
                track.title, remaining,
            )
            return None

        if not self._auth_headers:
            logger.warning("Spotify lyrics: no auth headers — token missing or not injected")
            return None

        # Spotify track IDs are always exactly 22 base62 characters.
        # Deezer IDs are pure digits (~10 chars), YouTube IDs are 11 alphanumeric chars.
        track_id = track.track_id
        if len(track_id) != 22 or not track_id.isalnum():
            # Non-Spotify track — try to find the Spotify ID via search if available
            if self._search_fn:
                # _search_fn may be search_track_id (str) or search_track (dict)
                # Try the richer dict version first if available
                search_track_fn = getattr(
                    getattr(self._search_fn, '__self__', None), 'search_track', None
                )
                if search_track_fn:
                    item = search_track_fn(track.title, track.primary_artist)
                    if item:
                        found_id = item["id"]
                        album_obj     = item.get("album", {}) or {}
                        album_name    = (album_obj.get("name") or "").strip()
                        album_type    = album_obj.get("album_type", "")
                        album_artists = album_obj.get("artists", [])
                        track_artists = item.get("artists", [])
                        # Update primary_artist from Spotify's album_artists so folder
                        # uses the album owner, not a comma-joined list of featured artists
                        new_primary = (
                            album_artists[0]["name"] if album_artists
                            else (track_artists[0]["name"] if track_artists else "")
                        )
                        if new_primary:
                            track.primary_artist = new_primary
                            if track_artists:
                                track.artist = ", ".join(a["name"] for a in track_artists)
                        if album_name and is_valid_album(album_name, track.primary_artist):
                            current_invalid = (
                                track.album in ("Unknown Album", "")
                                or not is_valid_album(track.album, track.primary_artist)
                            )
                            # Always upgrade to a proper album (album_type="album") even
                            # if the current album name is technically valid — e.g. a track
                            # saved as its single name should move to the studio album folder
                            spotify_is_album = album_type == "album"
                            if current_invalid or spotify_is_album:
                                track.album = album_name
                        track_num = item.get("track_number") or track.track_number
                        if track_num:
                            track.track_number = track_num
                        logger.info(
                            "  [Spotify] Matched '%s' → %s / %s (track #%s) [id:%s]",
                            track.title,
                            track.primary_artist,
                            album_name or "Unknown Album",
                            item.get("track_number", "?"),
                            found_id,
                        )
                        logger.debug(
                            "Spotify lyrics: resolved '%s' to ID %s (album: %s)",
                            track.title, found_id, album_name or "n/a",
                        )
                    else:
                        found_id = None
                else:
                    found_id = self._search_fn(track.title, track.primary_artist)

                if found_id:
                    track_id = found_id
                else:
                    logger.info(
                        "  [Spotify] No match found for '%s' by %s",
                        track.title, track.primary_artist,
                    )
                    return None
            else:
                logger.debug(
                    "Spotify lyrics: skipping '%s' — '%s' is not a Spotify ID "
                    "and no search function available",
                    track.title, track.track_id,
                )
                return None

        url = LYRICS_URL.format(track_id=track_id)
        try:
            resp = requests.get(url, headers=self._auth_headers, timeout=10)
            if resp.status_code == 401:
                if self._auth is not None:
                    # Re-prompt mid-run so remaining tracks in this run still work
                    self._auth.notify_401()
                    # Update our local headers with the fresh token
                    self._auth_headers = self._auth.session_headers
                    # Retry the request once with the new token
                    try:
                        resp2 = requests.get(url, headers=self._auth_headers, timeout=10)
                        if resp2.status_code == 200:
                            resp = resp2  # continue processing below
                        else:
                            return None
                    except Exception:
                        return None
                else:
                    logger.warning(
                        "Spotify lyrics: 401 — token expired. Restart to re-authenticate."
                    )
                    TOKEN_CACHE_FILE.unlink(missing_ok=True)
                    return None
            if resp.status_code == 403:
                logger.warning(
                    "Spotify lyrics: 403 Forbidden — token may be expired or account "
                    "doesn't have access. Try refreshing your SPOTIFY_AUTH_TOKEN."
                )
                return None
            if resp.status_code == 404:
                logger.debug("Spotify lyrics: 404 — no lyrics available for '%s'", track.title)
                return None
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                SpotifyPlugin._rate_limited_until = time.time() + retry_after
                logger.warning(
                    "Spotify lyrics: 429 rate limited — skipping remaining tracks for %ds",
                    retry_after,
                )
                return None
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning("Spotify lyrics: request failed for '%s': %s", track.title, e)
            return None

        try:
            lyrics_obj = data.get("lyrics", {})
            sync_type = lyrics_obj.get("syncType", "UNSYNCED")
            lines = lyrics_obj.get("lines", [])
            if not lines:
                logger.debug("Spotify lyrics: empty lines array for '%s'", track.title)
                return None
            if sync_type == "LINE_SYNCED":
                header = _lrc_header(track, lyrics_source=self.NAME)
                lrc_lines = [
                    f"{_ms_to_lrc_timestamp(int(l.get('startTimeMs', 0)))}{l.get('words', '')}"
                    for l in lines
                ]
                return header + "\n".join(lrc_lines)
            else:
                return _lrc_header(track, lyrics_source=self.NAME) + "\n".join(l.get("words", "") for l in lines)
        except (KeyError, TypeError) as e:
            logger.debug("Couldn't parse Spotify lyrics response: %s", e)
            return None
