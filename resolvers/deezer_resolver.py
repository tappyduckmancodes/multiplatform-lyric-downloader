"""
resolvers/deezer_resolver.py -- Metadata resolver for Deezer URLs.

No credentials needed for public content. ARL required for private playlists.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Generator

import requests

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
DEEZER_GW  = "https://www.deezer.com/ajax/gw-light.php"
TOKEN_CACHE = Path(".cache/deezer_token.json")
TOKEN_TTL   = 3000


class DeezerResolver(BaseResolver):
    NAME = "Deezer"
    INSTALL_REQUIRES = None
    NATIVE_LYRICS_SOURCE = "Deezer"
    URL_PATTERNS = [r"deezer\.com/(([a-z]{2}/)?(track|album|playlist|artist)/\d+)"]
    CONFIG = [
        ResolverConfig(
            "ARL Cookie", "DEEZER_ARL",
            "deezer.com DevTools -> Application -> Cookies -> 'arl' (lasts ~3 months). "
            "Required only for private playlists.",
            required=False,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._arl: str | None = None
        self._api_token: str | None = None
        self._expires_at: float = 0

    def setup(self, config: dict) -> bool:
        self._arl = config.get("DEEZER_ARL", "").strip() or None
        self._enabled = True  # Public API works without ARL
        return True

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        match = re.search(r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist|artist)/(\d+)", url)
        if not match:
            logger.error("Could not parse Deezer URL: %s", url)
            return

        resource_kind, resource_id = match.group(1), match.group(2)

        if resource_kind == "track":
            t = self._single_track(resource_id)
            if t:
                yield t
        elif resource_kind == "album":
            yield from self._album_tracks(resource_id)
        elif resource_kind == "playlist":
            yield from self._playlist_tracks(resource_id)
        elif resource_kind == "artist":
            yield from self._artist_tracks(resource_id)

    def _single_track(self, track_id):
        data = _api_get(f"/track/{track_id}")
        return _to_info(data) if data and not data.get("error") else None

    def _album_tracks(self, album_id):
        album = _api_get(f"/album/{album_id}")
        if not album or album.get("error"):
            return
        logger.info("Deezer album: %s (%d tracks)", album.get("title"), album.get("nb_tracks", 0))
        url = f"{DEEZER_API}/album/{album_id}/tracks"
        while url:
            data = _raw_get(url)
            if not data:
                break
            for item in data.get("data", []):
                full = _api_get(f"/track/{item['id']}")
                if full:
                    yield _to_info(full)
            url = data.get("next")

    def _playlist_tracks(self, playlist_id):
        pl = _api_get(f"/playlist/{playlist_id}")
        if pl and not pl.get("error"):
            logger.info("Deezer playlist: %s", pl.get("title"))
            url = f"{DEEZER_API}/playlist/{playlist_id}/tracks"
            while url:
                data = _raw_get(url)
                if not data:
                    break
                for item in data.get("data", []):
                    full = _api_get(f"/track/{item['id']}")
                    if full:
                        yield _to_info(full)
                url = data.get("next")
            return

        # Private playlist fallback
        token = self._get_api_token()
        if not token:
            logger.error("Deezer playlist %s is private. Add DEEZER_ARL to access it.", playlist_id)
            return

        try:
            resp = requests.get(
                DEEZER_GW,
                params={"method": "deezer.pagePlaylist", "api_version": "1.0",
                        "api_token": token, "playlist_id": playlist_id, "nb": 2000},
                cookies={"arl": self._arl} if self._arl else {},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
            for song in resp.json().get("results", {}).get("SONGS", {}).get("data", []):
                t = _gw_to_info(song)
                if t:
                    yield t
        except Exception as e:
            logger.error("Private playlist fetch failed: %s", e)

    def _artist_tracks(self, artist_id):
        artist = _api_get(f"/artist/{artist_id}")
        if not artist:
            return
        logger.info("Deezer artist: %s", artist.get("name"))
        url, seen = f"{DEEZER_API}/artist/{artist_id}/albums", set()
        albums = []
        while url:
            data = _raw_get(url)
            if not data:
                break
            albums.extend(data.get("data", []))
            url = data.get("next")
        for album in albums:
            name = album.get("title", "").lower()
            if name not in seen:
                seen.add(name)
                yield from self._album_tracks(str(album["id"]))

    def _get_api_token(self) -> str | None:
        if self._api_token and time.time() < self._expires_at:
            return self._api_token
        if TOKEN_CACHE.exists():
            try:
                data = json.loads(TOKEN_CACHE.read_text())
                if time.time() < data.get("expires_at", 0):
                    self._api_token = data["token"]
                    self._expires_at = data["expires_at"]
                    return self._api_token
            except Exception:
                pass
        if not self._arl:
            return None
        try:
            resp = requests.get(
                DEEZER_GW,
                params={"method": "deezer.getUserData", "api_version": "1.0", "api_token": "null"},
                cookies={"arl": self._arl},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            token = resp.json().get("results", {}).get("checkForm")
            if not token or token == "null":
                return None
            self._api_token = token
            self._expires_at = time.time() + TOKEN_TTL
            TOKEN_CACHE.write_text(json.dumps({"token": token, "expires_at": self._expires_at}))
            return token
        except Exception as e:
            logger.debug("Deezer token refresh failed: %s", e)
            return None


def _api_get(path):
    return _raw_get(f"{DEEZER_API}{path}")

def _raw_get(url):
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug("Deezer API error (%s): %s", url, e)
        return None

# Album name patterns that indicate a video/live/compilation release rather
# than the original studio album.  When matched, resolver_confident is set False
# so the enrichment chain in downloader.py will do a Spotify metadata lookup
# to find the real album name.
_COMP_RE = re.compile(
    r"\bvideos?\b|\bclips?\b|\blive\b|\bcollection\b|"
    r"\bgreatest hits\b|\bbest of\b|\bessentials\b|"
    r"\banthology\b|\bcompilation\b|\bvevo\b",
    re.IGNORECASE,
)


def _is_video_compilation(album_name: str) -> bool:
    return bool(_COMP_RE.search(album_name))


def _to_info(data: dict) -> TrackInfo:
    artist = data.get("artist", {})
    album  = data.get("album", {})
    contributors = data.get("contributors", [])
    primary = artist.get("name", "Unknown Artist")
    all_artists = ", ".join(c["name"] for c in contributors) if contributors else primary
    album_name = album.get("title", "Unknown Album")
    return TrackInfo(
        track_id=str(data["id"]),
        title=data.get("title", "Unknown Title"),
        artist=all_artists,
        primary_artist=primary,
        album=album_name,
        duration_ms=data.get("duration", 0) * 1000,
        track_number=data.get("track_position", 0),
        disc_number=data.get("disk_number", 1),
        resolver_name="Deezer",
        resolver_confident=not _is_video_compilation(album_name),
    )

def _gw_to_info(data: dict) -> TrackInfo | None:
    try:
        artists = data.get("ARTISTS", [])
        primary = artists[0]["ART_NAME"] if artists else data.get("ART_NAME", "Unknown")
        gw_album = data.get("ALB_TITLE", "Unknown Album")
        return TrackInfo(
            track_id=str(data["SNG_ID"]),
            title=data.get("SNG_TITLE", "Unknown"),
            artist=", ".join(a["ART_NAME"] for a in artists) if artists else primary,
            primary_artist=primary,
            album=gw_album,
            duration_ms=int(data.get("DURATION", 0)) * 1000,
            track_number=int(data.get("TRACK_NUMBER", 0)),
            disc_number=int(data.get("DISK_NUMBER", 1)),
            resolver_name="Deezer",
            resolver_confident=not _is_video_compilation(gw_album),
        )
    except Exception:
        return None
