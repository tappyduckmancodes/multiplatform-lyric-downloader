"""
resolvers/tidal_resolver.py -- Metadata resolver for Tidal URLs.

Requires tidalapi (pip install tidalapi).
Uses OAuth device flow — opens a browser link once, then caches the session.
"""

import logging
from pathlib import Path
from typing import Generator

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig

logger = logging.getLogger(__name__)

SESSION_CACHE = Path(".cache/tidal_session.json")


class TidalResolver(BaseResolver):
    NAME = "Tidal"
    INSTALL_REQUIRES = "tidalapi"
    NATIVE_LYRICS_SOURCE = None  # no native Tidal lyrics plugin
    URL_PATTERNS = [r"tidal\.com/(browse/)?(track|album|playlist|artist|mix)/"]
    CONFIG = [
        ResolverConfig(
            "Tidal Client ID", "TIDAL_CLIENT_ID",
            "From https://developer.tidal.com — create a free app to get credentials",
            required=False, secret=False,
        ),
        ResolverConfig(
            "Tidal Client Secret", "TIDAL_CLIENT_SECRET",
            "From your Tidal developer app",
            required=False, secret=True,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._session = None

    def setup(self, config: dict) -> bool:
        try:
            import tidalapi
        except ImportError:
            logger.warning(
                "Tidal resolver disabled -- tidalapi not installed. "
                "To enable: pip install tidalapi  |  "
                "Or run: python setup_wizard.py -> Install optional sources"
            )
            self._enabled = False
            return False

        try:
            session = tidalapi.Session()
            # Try loading cached session first
            if SESSION_CACHE.exists():
                try:
                    if session.load_oauth_session(*self._load_session_cache()):
                        self._session = session
                        self._enabled = True
                        logger.debug("Tidal: loaded cached session")
                        return True
                except Exception:
                    pass

            # OAuth device flow — opens a browser link
            logger.info("Tidal: opening browser for login...")
            session.login_oauth_simple()
            self._save_session_cache(session)
            self._session = session
            self._enabled = True
            return True
        except Exception as e:
            logger.warning("Tidal setup failed: %s", e)
            self._enabled = False
            return False

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        import re, tidalapi
        match = re.search(r"tidal\.com/(?:browse/)?(track|album|playlist|artist|mix)/([A-Za-z0-9\-]+)", url)
        if not match:
            logger.error("Could not parse Tidal URL: %s", url)
            return

        resource_kind, resource_id = match.group(1), match.group(2)

        try:
            if resource_kind == "track":
                t = self._session.track(int(resource_id))
                info = _track_to_info(t)
                if info:
                    yield info

            elif resource_kind == "album":
                album = self._session.album(int(resource_id))
                logger.info("Tidal album: %s", album.name)
                for t in album.tracks():
                    info = _track_to_info(t, album)
                    if info:
                        yield info

            elif resource_kind in ("playlist", "mix"):
                pl = self._session.playlist(resource_id)
                logger.info("Tidal playlist: %s", pl.name)
                for t in pl.tracks():
                    info = _track_to_info(t)
                    if info:
                        yield info

            elif resource_kind == "artist":
                artist = self._session.artist(int(resource_id))
                logger.info("Tidal artist: %s", artist.name)
                seen = set()
                for album in artist.get_albums():
                    name = album.name.lower()
                    if name not in seen:
                        seen.add(name)
                        for t in album.tracks():
                            info = _track_to_info(t, album)
                            if info:
                                yield info

        except Exception as e:
            logger.error("Tidal resolve failed: %s", e)

    def _load_session_cache(self):
        import json
        data = json.loads(SESSION_CACHE.read_text())
        return data["token_type"], data["access_token"], data["refresh_token"], data["expiry_time"]

    def _save_session_cache(self, session):
        import json
        SESSION_CACHE.write_text(json.dumps({
            "token_type":    session.token_type,
            "access_token":  session.access_token,
            "refresh_token": session.refresh_token,
            "expiry_time":   str(session.expiry_time),
        }))


def _track_to_info(track, album=None) -> TrackInfo | None:
    try:
        al = album or track.album
        artists = getattr(track, "artists", None)
        primary = track.artist.name if track.artist else "Unknown Artist"
        all_artists = ", ".join(a.name for a in artists) if artists else primary
        return TrackInfo(
            track_id=str(track.id),
            title=track.name,
            artist=all_artists,
            primary_artist=primary,
            album=al.name if al else "Unknown Album",
            duration_ms=(track.duration or 0) * 1000,
            track_number=getattr(track, "track_num", 0) or 0,
            disc_number=getattr(track, "volume_num", 1) or 1,
        )
    except Exception as e:
        logger.debug("Failed to convert Tidal track: %s", e)
        return None
