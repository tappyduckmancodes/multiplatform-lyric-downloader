"""
lyrics.py -- Core data types and LRC formatting helpers.

The lyrics waterfall lives in plugin_loader.py and plugins/.
This module is intentionally small: TrackInfo, the LRC header builder,
and shared helpers used by plugins and downloader.py.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    track_id: str
    title: str
    artist: str
    primary_artist: str
    album: str
    duration_ms: int
    track_number: int
    disc_number: int
    resolver_name: str = ""   # which resolver provided this track (for [re:] LRC tag)
    resolver_confident: bool = True  # False when metadata came from uncertain YouTube title parse


def _ms_to_lrc_timestamp(ms: int) -> str:
    total_seconds = ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"[{minutes:02d}:{seconds:05.2f}]"


def _lrc_header(track: TrackInfo, lyrics_source: str = "") -> str:
    """
    Build the LRC metadata header block.

    [ti:] — track title
    [ar:] — artist(s)
    [al:] — album name
    [length:] — track duration MM:SS
    [#:] — track number (omitted if 0)
    [re:] — lyrics source plugin name (Spotify, Deezer, LRCLIB, YouTube, etc.)
            Plugins should pass lyrics_source=self.NAME. If omitted, a placeholder
            is written and downloader.py stamps the real name in after fetching.
    [by:] — tagger credit

    The blank line after [by:] separates the header from the lyric lines.
    """
    duration_s = track.duration_ms / 1000
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    lines = [
        f"[ti:{track.title}]",
        f"[ar:{track.artist}]",
        f"[al:{track.album}]",
        f"[length:{minutes:02d}:{seconds:02d}]",
    ]
    if track.track_number:
        lines.append(f"[#:{track.track_number}]")
    re_value = lyrics_source if lyrics_source else "__LYRICS_SOURCE__"
    lines.append(f"[re:{re_value}]")
    lines.append("[by:multiplatform-lyric-downloader]")
    lines.append("")   # blank line before lyrics body
    return "\n".join(lines) + "\n"


def _stamp_lyrics_source(lrc: str, source: str) -> str:
    """Replace the __LYRICS_SOURCE__ placeholder with the actual source name.
    No-op if the plugin already stamped its name via _lrc_header(track, lyrics_source=NAME)."""
    clean_source = source.split("(")[0].strip()
    return lrc.replace("[re:__LYRICS_SOURCE__]", f"[re:{clean_source}]", 1)


def is_valid_album(name: str, artist: str = "") -> bool:
    """
    Return False for album names that are clearly junk.
    Rejects:
    - Empty / whitespace only
    - Pure punctuation/separator strings ("-", ".", etc.)
    - Single non-digit characters
    - Literal sentinel strings from yt-dlp: "null", "none", "undefined", "n/a"
    - Platform/service names that yt-dlp sometimes leaks into the album field
    Does NOT reject artist=album matches — self-titled albums are legitimate.
    """
    if not name or not name.strip():
        return False
    n = name.strip()
    if n.lower() in ("null", "none", "undefined", "n/a", "na", "unknown"):
        return False
    if n.lower() in ("youtube", "youtube music", "spotify", "soundcloud",
                     "vimeo", "dailymotion", "vevo", "apple music",
                     "tidal", "deezer", "bandcamp"):
        return False
    junk = set(' -_./|,;:!?~')
    if all(c in junk for c in n):
        return False
    if len(n) == 1 and not n.isdigit():
        return False
    return True
