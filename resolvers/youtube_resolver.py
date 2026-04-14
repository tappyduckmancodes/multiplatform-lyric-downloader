"""
resolvers/youtube_resolver.py -- Metadata resolver for regular YouTube URLs.

Handles youtube.com/watch?v=... and youtu.be/... links.
Uses yt-dlp to extract title/artist metadata, then the lyrics waterfall
finds lyrics as usual.

Requires yt-dlp (pip install yt-dlp) -- same dep as the YouTube lyrics plugin.

Note: YouTube Music URLs (music.youtube.com) are handled by ytmusic_resolver.py.
This resolver is for standard youtube.com and youtu.be links.
"""

import logging
import os
import re
from typing import Generator

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig
from utils import enrich_from_lrclib

logger = logging.getLogger(__name__)

# Matches:
#   https://www.youtube.com/watch?v=VIDEO_ID
#   https://youtu.be/VIDEO_ID
#   https://youtube.com/watch?v=VIDEO_ID
YT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)"
    r"([A-Za-z0-9_\-]{11})"
)
# Matches youtube.com/playlist?list=PL... (true playlists, not radio/watch mixes)
YT_PLAYLIST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/playlist\?.*list=([A-Za-z0-9_\-]{10,})"
)


def _clean_yt_url(url: str) -> str:
    """
    Extract just the video ID and return a clean watch URL.
    Strips &list=, &start_radio=, ?si=, and other tracking/playlist params
    that cause bash to split the URL when unquoted.
    """
    match = YT_URL_RE.search(url)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return url


class YouTubeResolver(BaseResolver):
    NAME = "YouTube"
    URL_PATTERNS = [
        r"(?:www\.)?youtube\.com/watch",
        r"(?:www\.)?youtube\.com/playlist",
        r"youtu\.be/[A-Za-z0-9_\-]{11}",
    ]
    CONFIG = []
    INSTALL_REQUIRES = "yt-dlp"
    NATIVE_LYRICS_SOURCE = "YouTube"

    def __init__(self):
        super().__init__()
        self._yt_dlp = None

    def setup(self, config: dict) -> bool:
        try:
            import yt_dlp
            self._yt_dlp = yt_dlp
            self._enabled = True
            return True
        except ImportError:
            logger.warning(
                "YouTube resolver disabled -- yt-dlp not installed. "
                "To enable: pip install yt-dlp  |  "
                "Or run: python setup_wizard.py -> Install optional sources"
            )
            self._enabled = False
            return False

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        # Handle YouTube playlist URLs (youtube.com/playlist?list=PL...)
        # Both explicit -playlist flag and auto-detected playlist URLs
        playlist_match = YT_PLAYLIST_RE.search(url)
        if playlist_match or kind == "playlist":
            yield from self._resolve_playlist(url)
            return

        clean_url = _clean_yt_url(url)
        if not YT_URL_RE.search(clean_url):
            logger.error(
                "Could not parse YouTube URL: %s\n"
                "  TIP: If your URL contains & (playlist params), wrap it in quotes:\n"
                "       python downloader.py -track \"https://youtube.com/watch?v=ID&list=...\"\n"
                "  Or use just the video URL without playlist params:\n"
                "       python downloader.py -track https://youtube.com/watch?v=%s",
                url,
                url.split("v=")[-1].split("&")[0] if "v=" in url else "VIDEO_ID",
            )
            return

        video_id = YT_URL_RE.search(clean_url).group(1)

        info = self._extract_info(clean_url)
        if not info:
            return

        track = _info_to_trackinfo(video_id, info)
        if track:
            yield track

    def _resolve_playlist(self, url: str) -> Generator[TrackInfo, None, None]:
        """
        Expand a YouTube playlist URL into individual tracks using yt-dlp.
        Only works for real playlists (youtube.com/playlist?list=PL...).
        Watch-page mixes (&list=RD...) are not playlists — only the single
        video is extracted for those.
        """
        try:
            import yt_dlp
        except ImportError:
            logger.error("YouTube playlist resolution requires yt-dlp: pip install yt-dlp")
            return

        # Reject radio/mix lists (RD, RDEM, etc.) — these aren't real playlists
        import re as _re
        list_match = _re.search(r"list=([A-Za-z0-9_\-]+)", url)
        if list_match:
            list_id = list_match.group(1)
            if list_id.startswith(("RD", "RDMM", "RDCLAK", "RDEM")):
                logger.warning(
                    "URL looks like a YouTube mix/radio, not a playlist. "
                    "Extracting single video only."
                )
                clean = _clean_yt_url(url)
                info = self._extract_info(clean)
                if info:
                    video_id = YT_URL_RE.search(clean)
                    if video_id:
                        track = _info_to_trackinfo(video_id.group(1), info)
                        if track:
                            yield track
                return

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,  # don't fetch each video's full info yet
            "ignoreerrors": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error("YouTube playlist extraction failed: %s", e)
            return

        if not info:
            return

        entries = info.get("entries") or []
        if not entries:
            logger.warning("YouTube playlist appears empty: %s", url)
            return

        logger.info("YouTube playlist: %s (%d tracks)", info.get("title", "untitled"), len(entries))

        for entry in entries:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            full_info = self._extract_info(video_url)
            if full_info:
                track = _info_to_trackinfo(video_id, full_info)
                if track:
                    yield track

    def _extract_info(self, url: str) -> dict | None:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            # Skip client-side age-gate check for metadata extraction.
            # We only need title/artist/album — no video stream required.
            "age_limit": 99,
        }
        # Optional cookie auth for age-restricted / private videos
        # Set YTDLP_BROWSER=chrome (or firefox/edge/brave/opera/chromium) in .env
        # OR set YTDLP_COOKIES_FILE=/path/to/cookies.txt in .env
        browser = os.environ.get("YTDLP_BROWSER", "").strip().lower()
        cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
        elif cookies_file:
            opts["cookiefile"] = cookies_file
        try:
            with self._yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error("YouTube metadata extraction failed: %s", e)
            return None


def _info_to_trackinfo(video_id: str, info: dict) -> TrackInfo | None:
    """
    Convert yt-dlp info dict to TrackInfo.

    Field priority:
    1. yt-dlp 'artist' + 'track' — set by YouTube Music API for official uploads
    2. 'creator' + 'alt_title' — alternative music metadata fields
    3. Parse 'Artist - Title' from video title
    4. Channel name as artist, raw title as title (last resort)
    """
    raw_title = info.get("title", "Unknown")
    channel   = (info.get("channel") or info.get("uploader") or "").removesuffix(" - Topic").strip()

    # Priority 1: explicit music metadata (YouTube Music API uploads)
    if info.get("artist") and info.get("track"):
        raw_album_p1 = (info.get("album") or "").strip()
        from lyrics import is_valid_album as _iva
        clean_album_p1 = raw_album_p1 if _iva(raw_album_p1) else "Unknown Album"
        # info["artist"] may be comma-separated ("Baby Keem, Kendrick Lamar")
        # Use the first name for the folder; keep full string in [ar:] tag
        _p1_primary = info["artist"].split(",")[0].strip()
        # Sanity-check: does the video title actually contain the track name?
        # If not, the YouTube Music API metadata is probably wrong (wrong song tagged)
        # Set resolver_confident=False so the selection menu appears
        import re as _re2
        def _norm(s):
            return _re2.sub(r"[^a-z0-9]", "", s.lower())
        _track_norm = _norm(info["track"])
        _title_norm = _norm(raw_title)
        _p1_confident = len(_track_norm) >= 3 and _track_norm in _title_norm
        return TrackInfo(
            track_id=video_id,
            title=_clean_title(info["track"]),
            artist=info["artist"],
            primary_artist=_p1_primary,
            album=clean_album_p1,
            duration_ms=int((info.get("duration") or 0) * 1000),
            track_number=0,
            disc_number=1,
            resolver_name="YouTube",
            resolver_confident=_p1_confident,
        )

    # Priority 2: creator + alt_title
    if info.get("creator") and info.get("alt_title"):
        raw_album_p2 = (info.get("album") or "").strip()
        from lyrics import is_valid_album as _iva2
        clean_album_p2 = raw_album_p2 if _iva2(raw_album_p2) else "Unknown Album"
        _p2_primary = info["creator"].split(",")[0].strip()
        return TrackInfo(
            track_id=video_id,
            title=_clean_title(info["alt_title"]),
            artist=info["creator"],
            primary_artist=_p2_primary,
            album=clean_album_p2,
            duration_ms=int((info.get("duration") or 0) * 1000),
            track_number=0,
            disc_number=1,
            resolver_name="YouTube",
        )

    # Priority 3: parse "Artist - Title" from video title — confident (has clear split)
    parsed_artist, parsed_title = _parse_yt_title(raw_title)
    if parsed_artist:
        primary = parsed_artist.split(",")[0].strip()
        album = info.get("album") or ""
        track_number = 0
        if not album:
            enriched = enrich_from_lrclib(primary, parsed_title)
            album = enriched.get("album", "") or ""
            track_number = enriched.get("track_number", 0) or 0
        return TrackInfo(
            track_id=video_id,
            title=parsed_title,
            artist=parsed_artist,
            primary_artist=primary,
            album=album or "Unknown Album",
            duration_ms=int((info.get("duration") or 0) * 1000),
            track_number=track_number,
            disc_number=1,
            resolver_name="YouTube",
            resolver_confident=True,
        )

    # Priority 4: channel name + cleaned title (last resort) — NOT confident
    cleaned_title = _parse_yt_title(raw_title)[1]  # at least strip suffixes
    artist = channel or "Unknown Artist"
    primary_artist_p4 = artist.split(",")[0].strip()
    album = info.get("album") or ""
    track_number = 0

    if primary_artist_p4 and cleaned_title and not album:
        enriched = enrich_from_lrclib(primary_artist_p4, cleaned_title)
        album = enriched.get("album", "") or ""
        track_number = enriched.get("track_number", 0) or 0

    return TrackInfo(
        track_id=video_id,
        title=_clean_title(cleaned_title),
        artist=artist,
        primary_artist=primary_artist_p4,
        album=album or "Unknown Album",
        duration_ms=int((info.get("duration") or 0) * 1000),
        track_number=track_number,
        disc_number=1,
        resolver_name="YouTube",
        resolver_confident=False,  # channel name as artist — needs user confirmation
    )



def _clean_title(title: str) -> str:
    """
    Strip video-type suffixes from a track title.
    Handles English and multilingual variants that yt-dlp may leave in the
    'track' metadata field (e.g. yt-dlp sometimes includes "(Video Oficial)"
    in the track name returned from YouTube Music API metadata).
    """
    import re as _re
    t = title.strip()
    # Bracketed/parenthesised variants
    patterns = [
        r"\s*[\(\[]\s*(?:official\s*)?(?:music\s*)?(?:lyric(?:s)?\s*)?video\s*[\)\]]",
        r"\s*[\(\[]\s*(?:official\s*)?audio\s*[\)\]]",
        r"\s*[\(\[]\s*official\s*[\)\]]",
        r"\s*[\(\[]\s*lyrics?\s*[\)\]]",
        r"\s*[\(\[]\s*(?:hd|4k|hq)\s*[\)\]]",
        r"\s*[\(\[]\s*visualizer\s*[\)\]]",
        r"\s*[\(\[]\s*(?:video\s*)?oficial\s*[\)\]]",
        r"\s*[\(\[]\s*videoclip(?:\s*oficial)?\s*[\)\]]",
        r"\s*[\(\[]\s*video\s*musical\s*[\)\]]",
        r"\s*[\(\[]\s*(?:vid[eé]o|clip)\s*(?:officiel(?:le)?)?\s*[\)\]]",
        r"\s*[\(\[]\s*(?:official\s*)?(?:mv|pv|clip)\s*[\)\]]",
        r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]",
    ]
    for p in patterns:
        t = _re.sub(p, "", t, flags=_re.IGNORECASE).strip()
    # Bare suffix variants (no brackets)
    bare = [
        r"\s+official\s+music\s+video$",
        r"\s+official\s+lyric\s+video$",
        r"\s+official\s+video$",
        r"\s+official\s+audio$",
        r"\s+music\s+video$",
        r"\s+lyric\s+video$",
        r"\s+official\s+clip$",
        r"\s+video\s+oficial$",
        r"\s+videoclip(?:\s+oficial)?$",
        r"\s+official\s+mv$",
    ]
    for p in bare:
        t = _re.sub(p, "", t, flags=_re.IGNORECASE).strip()
    return t or title  # never return empty



def _parse_yt_title(title: str) -> tuple[str, str]:
    """
    Try to split 'Artist - Title (Official Video)' into (artist, clean_title).
    Returns ('', cleaned_title) if no 'Artist - ' separator found.

    Delegates video-suffix stripping to _clean_title to avoid duplication.
    """
    import re as _re
    # Strip pipe-separated suffixes first (channel names, labels, etc.)
    # e.g. "Title | Artist | Label" — must happen before _clean_title
    clean = _re.sub(r"\s*\|.*$", "", title.strip()).strip()

    # Delegate bracket + bare suffix stripping to _clean_title
    clean = _clean_title(clean)

    # Remove surrounding quotes if the whole title is quoted
    if (clean.startswith('"') and clean.endswith('"')) or \
       (clean.startswith("'") and clean.endswith("'")):
        clean = clean[1:-1].strip()

    # Split on " - " (with spaces, avoids splitting hyphenated words)
    if " - " in clean:
        # If there's a label prefix like "Fueled By Ramen: Panic! At The Disco - Title",
        # strip everything up to and including the last ": " before the " - " split
        if ": " in clean.split(" - ")[0]:
            clean = clean.split(": ", 1)[-1].strip()
        artist, _, song = clean.partition(" - ")
        return artist.strip(), song.strip()

    # No " - " found — try ": " as separator (VEVO/label format: "Artist: Title")
    # Accept only when the part before ": " looks like an artist name:
    #   ≤ 35 characters AND ≤ 5 words (filters out descriptive sentences with colons)
    if ": " in clean:
        pre, _, post = clean.partition(": ")
        pre_s, post_s = pre.strip(), post.strip()
        if pre_s and post_s and len(pre_s) <= 35 and len(pre_s.split()) <= 5:
            return pre_s, post_s

    return "", clean
