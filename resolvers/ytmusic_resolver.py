"""
resolvers/ytmusic_resolver.py -- Metadata resolver for YouTube Music URLs.

Uses ytmusicapi (pip install ytmusicapi).
Works unauthenticated for public content. Auth needed for private playlists.
"""

import logging
import re
from typing import Generator

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig

logger = logging.getLogger(__name__)

# YouTube Music URL patterns
YTM_URL_RE = re.compile(
    r"music\.youtube\.com/(?:watch\?v=|browse/|playlist\?list=)"
    r"([A-Za-z0-9_\-]+)"
)


class YTMusicResolver(BaseResolver):
    NAME = "YouTube Music"
    INSTALL_REQUIRES = "ytmusicapi"
    NATIVE_LYRICS_SOURCE = "YouTube"
    URL_PATTERNS = [r"music\.youtube\.com"]
    CONFIG = [
        ResolverConfig(
            "YouTube Music Auth Headers", "YTMUSIC_AUTH_HEADERS",
            "Path to ytmusicapi auth headers file (run: ytmusicapi browser). "
            "Leave blank for unauthenticated access (public content only).",
            required=False, secret=False,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._ytm = None

    def setup(self, config: dict) -> bool:
        try:
            from ytmusicapi import YTMusic
        except ImportError:
            logger.warning(
                "YouTube Music resolver disabled -- ytmusicapi not installed. "
                "To enable: pip install ytmusicapi  |  "
                "Or run: python setup_wizard.py -> Install optional sources"
            )
            self._enabled = False
            return False

        auth_path = config.get("YTMUSIC_AUTH_HEADERS", "").strip() or None
        try:
            self._ytm = YTMusic(auth_path) if auth_path else YTMusic()
            self._enabled = True
            return True
        except Exception as e:
            logger.warning("YTMusic setup failed: %s", e)
            self._enabled = False
            return False

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        # Detect resource type from URL shape
        if "watch?v=" in url:
            video_id = re.search(r"watch\?v=([A-Za-z0-9_\-]+)", url)
            if video_id:
                t = self._single_track(video_id.group(1))
                if t:
                    yield t
            return

        if "playlist?list=" in url or "/playlist/" in url:
            pl_id = re.search(r"(?:playlist\?list=|/playlist/)([A-Za-z0-9_\-]+)", url)
            if pl_id:
                yield from self._playlist_tracks(pl_id.group(1))
            return

        if "/browse/" in url:
            browse_id = re.search(r"/browse/([A-Za-z0-9_\-]+)", url)
            if not browse_id:
                return
            bid = browse_id.group(1)
            # MPxx = playlist/mix, MPRExx = album, UCxx = artist/channel
            if bid.startswith("MPRB") or bid.startswith("MPREb"):
                yield from self._album_tracks(bid)
            elif bid.startswith("UC"):
                yield from self._artist_tracks(bid)
            else:
                yield from self._playlist_tracks(bid)

    def _single_track(self, video_id: str) -> TrackInfo | None:
        try:
            data = self._ytm.get_song(video_id)
            details = data.get("videoDetails", {})
            title = details.get("title", "Unknown Title")
            artist = details.get("author", "Unknown Artist")
            duration_s = int(details.get("lengthSeconds", 0))
            return TrackInfo(
                track_id=video_id,
                title=title,
                artist=artist,
                primary_artist=artist,
                album="Unknown Album",
                duration_ms=duration_s * 1000,
                track_number=0,
                disc_number=1,
            resolver_name="YouTube Music",
        )
        except Exception as e:
            logger.debug("YTMusic single track failed: %s", e)
            return None

    def _album_tracks(self, browse_id: str):
        try:
            album = self._ytm.get_album(browse_id)
            album_name = album.get("title", "Unknown Album")
            artist_name = album.get("artists", [{}])[0].get("name", "Unknown Artist")
            logger.info("YTMusic album: %s", album_name)
            for i, track in enumerate(album.get("tracks", []), 1):
                artists = track.get("artists", [])
                primary = artist_name
                all_a = ", ".join(a["name"] for a in artists) if artists else primary
                yield TrackInfo(
                    track_id=track.get("videoId", str(i)),
                    title=track.get("title", "Unknown"),
                    artist=all_a,
                    primary_artist=primary,
                    album=album_name,
                    duration_ms=track.get("duration_seconds", 0) * 1000,
                    track_number=i,
                    disc_number=1,
            resolver_name="YouTube Music",
        )
        except Exception as e:
            logger.error("YTMusic album fetch failed: %s", e)

    def _playlist_tracks(self, playlist_id: str):
        try:
            pl = self._ytm.get_playlist(playlist_id, limit=None)
            logger.info("YTMusic playlist: %s", pl.get("title", playlist_id))
            for track in pl.get("tracks", []):
                artists = track.get("artists", [])
                primary = artists[0]["name"] if artists else "Unknown Artist"
                all_a = ", ".join(a["name"] for a in artists) if artists else primary
                yield TrackInfo(
                    track_id=track.get("videoId", ""),
                    title=track.get("title", "Unknown"),
                    artist=all_a,
                    primary_artist=primary,
                    album=track.get("album", {}).get("name", "Unknown Album") if track.get("album") else "Unknown Album",
                    duration_ms=track.get("duration_seconds", 0) * 1000,
                    track_number=0,
                    disc_number=1,
            resolver_name="YouTube Music",
        )
        except Exception as e:
            logger.error("YTMusic playlist fetch failed: %s", e)

    def _artist_tracks(self, channel_id: str):
        try:
            artist = self._ytm.get_artist(channel_id)
            logger.info("YTMusic artist: %s", artist.get("name"))
            albums_data = self._ytm.get_artist_albums(channel_id, artist.get("albums", {}).get("params", ""))
            for album in albums_data:
                yield from self._album_tracks(album["browseId"])
        except Exception as e:
            logger.error("YTMusic artist fetch failed: %s", e)
