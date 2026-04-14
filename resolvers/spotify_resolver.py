"""
resolvers/spotify_resolver.py -- Metadata resolver for Spotify URLs.

Handles: open.spotify.com URLs, spotify: URIs, --playing, --liked
"""

import logging
import re
from typing import Generator

import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig

logger = logging.getLogger(__name__)

SCOPE = "user-library-read user-read-currently-playing user-read-playback-state"
SPOTIFY_URL_RE = re.compile(
    r"(?:https://open\.spotify\.com/|spotify:)"
    r"(track|album|playlist|artist)[/:]([A-Za-z0-9]+)"
)


class SpotifyResolver(BaseResolver):
    NAME = "Spotify"
    INSTALL_REQUIRES = None
    NATIVE_LYRICS_SOURCE = "Spotify"
    URL_PATTERNS = [
        r"open\.spotify\.com",
        r"spotify:(track|album|playlist|artist):",
    ]
    CONFIG = [
        ResolverConfig("Client ID",     "SPOTIFY_CLIENT_ID",     "From https://developer.spotify.com/dashboard", secret=False),
        ResolverConfig("Client Secret", "SPOTIFY_CLIENT_SECRET", "From https://developer.spotify.com/dashboard", secret=True),
        ResolverConfig("Redirect URI",  "SPOTIFY_REDIRECT_URI",  "Must match your Spotify app settings",          secret=False, required=False),
    ]

    def __init__(self):
        super().__init__()
        self._sp_cc: spotipy.Spotify | None = None     # Client Credentials — catalog search
        self._sp_oauth: spotipy.Spotify | None = None  # OAuth — -playing only

    def setup(self, config: dict) -> bool:
        client_id     = config.get("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = config.get("SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri  = config.get("SPOTIFY_REDIRECT_URI", "").strip()

        if not client_id or not client_secret:
            logger.debug("Spotify resolver: no client credentials, disabled.")
            self._enabled = False
            return False

        self._client_id     = client_id
        self._client_secret = client_secret
        self._redirect_uri  = redirect_uri
        self._enabled = True
        return True

    def get_client(self) -> spotipy.Spotify:
        """
        Silent catalog client using Client Credentials flow.
        No browser, no redirect URI, no user interaction.
        Used for all track/album/playlist search and metadata.
        """
        if self._sp_cc is None:
            auth = SpotifyClientCredentials(
                client_id=self._client_id,
                client_secret=self._client_secret,
                cache_handler=spotipy.cache_handler.CacheFileHandler(
                    cache_path=".cache/spotify_cc.json"
                ),
            )
            self._sp_cc = spotipy.Spotify(auth_manager=auth)
        return self._sp_cc

    def get_oauth_client(self) -> spotipy.Spotify:
        """
        OAuth client — only used for -playing (requires user login + redirect URI).
        Triggers the browser flow on first call; caches token in .cache/spotipy.
        """
        if self._sp_oauth is None:
            auth = SpotifyOAuth(
                client_id=self._client_id,
                client_secret=self._client_secret,
                redirect_uri=self._redirect_uri,
                scope=SCOPE,
                cache_path=".cache/spotipy",
                open_browser=True,
            )
            self._sp_oauth = spotipy.Spotify(auth_manager=auth)
        return self._sp_oauth

    def search_track(self, title: str, artist: str) -> dict | None:
        """
        Search Spotify for a track. Tries progressively broader queries so
        tracks with unusual titles (live shows, non-"Artist - Title" YouTube
        videos) can still be found. Prefers album versions over singles.
        """
        import re

        def _strip_noise(s: str) -> str:
            """Strip feat., video suffixes, and other noise from a string."""
            s = re.sub(r"[\(\[][Ff]ea?t\.?\s[^\)\]]+[\)\]]", "", s).strip()
            s = re.sub(r"\s+[Ff]t\.?\s.+$", "", s).strip()
            vid = [
                r"[\(\[]\s*(?:official\s*)?(?:music\s*)?(?:lyric(?:s)?\s*)?video\s*[\)\]]",
                r"[\(\[]\s*(?:video\s*)?oficial\s*[\)\]]",
                r"[\(\[]\s*videoclip(?:\s*oficial)?\s*[\)\]]",
                r"[\(\[]\s*(?:official\s*)?audio\s*[\)\]]",
                r"[\(\[]\s*(?:official\s*)?(?:mv|pv|clip)\s*[\)\]]",
                r"[\(\[]\s*visualizer\s*[\)\]]",
                r"[\(\[]\s*(?:hd|4k|hq)\s*[\)\]]",
                r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]",
                r"\s+official\s+(?:music\s+)?video$",
                r"\s+official\s+audio$",
                r"\s+video\s+oficial$",
            ]
            for p in vid:
                s = re.sub(p, "", s, flags=re.IGNORECASE).strip()
            return s or title

        clean_title  = _strip_noise(title)
        clean_artist = artist.strip() if artist else ""

        # Build queries from most specific to most broad.
        # The key addition: fallbacks with no artist constraint, and
        # attempts to extract an artist name from the title itself
        # (handles cases like "Bad Bunny's Apple Music Super Bowl Halftime Show"
        # where the "artist" field is a channel name like "NFL").
        queries = []

        if clean_artist:
            if clean_title != title:
                queries.append(f"track:{clean_title} artist:{clean_artist}")
            queries.append(f"track:{title} artist:{clean_artist}")
            queries.append(f"{clean_artist} {clean_title}")

        # Title-only (no artist filter) — catches channel-name-as-artist issues
        queries.append(f"track:{clean_title}")
        if clean_title != title:
            queries.append(f"track:{title}")

        # Broad keyword search — last resort
        queries.append(clean_title)
        if clean_artist:
            queries.append(f"{clean_title} {clean_artist}")

        # Dedupe preserving order
        seen: set = set()
        queries = [q for q in queries if q and not (q in seen or seen.add(q))]

        def _rank(it):
            t = it.get("album", {}).get("album_type", "")
            return 0 if t == "album" else (1 if t == "single" else 2)

        try:
            sp = self.get_client()
            for q in queries:
                results = sp.search(q=q, type="track", limit=5)
                items = results.get("tracks", {}).get("items", [])
                if not items:
                    continue
                return sorted(items, key=_rank)[0]
        except Exception as e:
            logger.debug("Spotify track search failed for '%s': %s", title, e)
        return None

    def search_track_candidates(self, title: str, artist: str,
                                 limit: int = 5) -> list[dict]:
        """
        Return up to `limit` candidate tracks for a given title+artist.
        Used by the interactive selection menu when metadata is uncertain.
        """
        import re

        def _strip(s):
            s = re.sub(r"[\(\[][Ff]ea?t\.?\s[^\)\]]+[\)\]]", "", s).strip()
            vid = [
                r"[\(\[]\s*(?:official\s*)?(?:music\s*)?(?:lyric(?:s)?\s*)?video\s*[\)\]]",
                r"[\(\[]\s*(?:video\s*)?oficial\s*[\)\]]",
                r"[\(\[]\s*(?:official\s*)?audio\s*[\)\]]",
                r"[\(\[]\s*(?:hd|4k|hq)\s*[\)\]]",
                r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]",
            ]
            for p in vid:
                s = re.sub(p, "", s, flags=re.IGNORECASE).strip()
            return s or title

        clean = _strip(title)
        queries = [f"track:{clean}", clean, f"{clean} {artist}".strip()]
        seen: set = set()
        queries = [q for q in queries if q and not (q in seen or seen.add(q))]

        # Words that indicate a cover/karaoke/tribute — always exclude these from
        # the selection menu so the user only sees genuine original recordings
        _junk = re.compile(
            r"karaoke|instrumental|backing.?track|cover.?version|"
            r"made.popular.by|originally.performed|tribute|"
            r"in.the.style.of|as.made.famous|ringtone|re-?record",
            re.IGNORECASE,
        )

        def _is_junk(item: dict) -> bool:
            name  = item.get("name", "")
            album = item.get("album", {}).get("name", "")
            return bool(_junk.search(name) or _junk.search(album))

        candidates = []
        seen_ids: set = set()
        try:
            sp = self.get_client()
            # Fetch more than limit so we have room to filter out karaoke results
            fetch_limit = limit * 3
            for q in queries:
                results = sp.search(q=q, type="track", limit=fetch_limit)
                for item in results.get("tracks", {}).get("items", []):
                    if item["id"] not in seen_ids and not _is_junk(item):
                        seen_ids.add(item["id"])
                        candidates.append(item)
                if len(candidates) >= limit:
                    break
        except Exception as e:
            logger.debug("Spotify candidate search failed: %s", e)
        return candidates[:limit]


    def search_track_id(self, title: str, artist: str) -> str | None:
        """Compatibility wrapper — returns just the Spotify track ID."""
        item = self.search_track(title, artist)
        return item["id"] if item else None

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        # -playing requires OAuth (needs user's playback state scope)
        if kind == "playing":
            sp = self.get_oauth_client()
            track = self._currently_playing(sp)
            if track:
                yield track
            return

        # Everything else (track/album/playlist/artist lookup) uses silent CC auth
        sp = self.get_client()

        if kind == "liked":
            yield from self._liked_songs(sp)
            return

        match = SPOTIFY_URL_RE.search(url)
        if not match:
            logger.error("Could not parse Spotify URL: %s", url)
            return

        resource_kind, resource_id = match.group(1), match.group(2)

        if resource_kind == "track":
            t = self._single_track(sp, resource_id)
            if t:
                yield t
        elif resource_kind == "album":
            yield from self._album_tracks(sp, resource_id)
        elif resource_kind == "playlist":
            yield from self._playlist_tracks(sp, resource_id)
        elif resource_kind == "artist":
            yield from self._artist_tracks(sp, resource_id)

    # ── Spotify API calls ────────────────────────────────────────────────────

    def _single_track(self, sp, track_id):
        try:
            return _to_info(sp.track(track_id))
        except Exception as e:
            logger.error("Failed to fetch track %s: %s", track_id, e)
            return None

    def _currently_playing(self, sp):
        """Get the currently playing Spotify track via spotipy OAuth."""
        try:
            pb = sp.currently_playing()
            if not pb or not pb.get("item"):
                logger.info("Nothing is currently playing on Spotify.")
                return None
            return _to_info(pb["item"])
        except Exception as e:
            logger.error("Failed to get currently playing: %s", e)
            return None

    def _album_tracks(self, sp, album_id):
        try:
            album = sp.album(album_id)
            album_name = album["name"]
            logger.info("Spotify album: %s (%d tracks)", album_name, album["total_tracks"])
            results = sp.album_tracks(album_id, limit=50)
            while results:
                for item in results["items"]:
                    item["album"] = {"name": album_name, "artists": album.get("artists", [])}
                    yield _to_info(item)
                results = sp.next(results) if results["next"] else None
        except Exception as e:
            logger.error("Album fetch failed: %s", e)

    def _playlist_tracks(self, sp, playlist_id):
        try:
            pl = sp.playlist(playlist_id, fields="name")
            logger.info("Spotify playlist: %s", pl["name"])
            results = sp.playlist_items(
                playlist_id,
                fields="items(track(id,name,artists,album,duration_ms,track_number,disc_number)),next",
                limit=100,
            )
            while results:
                for item in results["items"]:
                    t = item.get("track")
                    if t and t.get("id"):
                        yield _to_info(t)
                results = sp.next(results) if results["next"] else None
        except Exception as e:
            logger.error("Playlist fetch failed: %s", e)

    def _artist_tracks(self, sp, artist_id):
        try:
            artist = sp.artist(artist_id)
            logger.info("Spotify artist: %s", artist["name"])
            albums, seen = [], set()
            results = sp.artist_albums(artist_id, album_type="album,single", limit=50)
            while results:
                albums.extend(results["items"])
                results = sp.next(results) if results["next"] else None
            for album in albums:
                name = album["name"].lower()
                if name not in seen:
                    seen.add(name)
                    yield from self._album_tracks(sp, album["id"])
        except Exception as e:
            logger.error("Artist fetch failed: %s", e)

    def _liked_songs(self, sp):
        logger.info("Fetching Liked Songs...")
        try:
            results = sp.current_user_saved_tracks(limit=50)
            while results:
                for item in results["items"]:
                    t = item.get("track")
                    if t:
                        yield _to_info(t)
                results = sp.next(results) if results["next"] else None
        except Exception as e:
            logger.error("Liked songs fetch failed: %s", e)


def _to_info(data: dict) -> TrackInfo:
    from lyrics import is_valid_album
    artists = data.get("artists", [])
    all_artists = ", ".join(a["name"] for a in artists) if artists else "Unknown Artist"
    album = data.get("album", {})
    album_artists = album.get("artists", []) if isinstance(album, dict) else []
    primary = (album_artists[0]["name"] if album_artists
                else (artists[0]["name"] if artists else "Unknown Artist"))
    raw_album = (album.get("name", "") if isinstance(album, dict) else "").strip()
    clean_album = raw_album if is_valid_album(raw_album, primary) else "Unknown Album"
    return TrackInfo(
        track_id=data["id"],
        title=data.get("name", "Unknown Title"),
        artist=all_artists,
        primary_artist=primary,
        album=clean_album,
        duration_ms=data.get("duration_ms", 0),
        track_number=data.get("track_number", 0),
        disc_number=data.get("disc_number", 1),
        resolver_name="Spotify",
    )
