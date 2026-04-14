"""
utils.py -- File path helpers, LRC saving logic, and shared enrichment helpers.
"""

import logging
import re
import requests
from pathlib import Path

from lyrics import TrackInfo

logger = logging.getLogger(__name__)

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|*\x00-\x1f]')
_TRAILING = re.compile(r"[\s.]+$")

# Map illegal Windows filename characters to visually similar Unicode equivalents
# so "Love?" → "Love？" and "AC/DC" → "AC⧸DC" rather than "Love_" / "AC_DC"
_CHAR_MAP = str.maketrans({
    "?": "？",   # U+FF1F fullwidth question mark
    "*": "✶",   # U+2736 six pointed black star
    "|": "｜",   # U+FF5C fullwidth vertical line
    "<": "﹤",   # U+FE64 small less-than sign
    ">": "﹥",   # U+FE65 small greater-than sign
    ":": "꞉",   # U+A789 modifier letter colon (used by many taggers)
    '"': "\u201C",  # " left double quotation mark
    "/": "⧸",   # U+29F8 big solidus
    "\\": "⧹",  # U+29F9 big reverse solidus
})


def sanitize(name: str, max_length: int = 100) -> str:
    name = name.translate(_CHAR_MAP)
    name = _TRAILING.sub("", name)
    return name[:max_length].strip() or "Unknown"


def build_lrc_path(output_dir: Path, track: TrackInfo) -> Path:
    artist_dir = sanitize(track.primary_artist)
    album_dir = sanitize(track.album)
    num = f"{track.track_number:02d}" if track.track_number else "00"
    filename = sanitize(f"{num} {track.title}") + ".lrc"

    # Multi-disc: use Plex/deemix standard "Disc N" subdirectory
    if track.disc_number and track.disc_number > 1:
        return output_dir / artist_dir / album_dir / f"Disc {track.disc_number}" / filename
    else:
        return output_dir / artist_dir / album_dir / filename


def save_lrc(path: Path, content: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig = UTF-8 with BOM — broader compatibility with media players
        # (MusicBee, Plex, Kodi) especially for CJK / Arabic / non-Latin scripts
        path.write_text(content, encoding="utf-8-sig")
        return True
    except OSError as e:
        logger.error("Failed to write %s: %s", path, e)
        return False


def lrc_exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def enrich_from_lrclib(artist: str, title: str) -> dict:
    """
    Look up a track on LRCLIB to get album name and track number.
    Returns {'album': str, 'track_number': int}. Empty strings/0 on failure.

    Rejects album names that equal the artist name — LRCLIB sometimes returns
    artist as album for singles (e.g. artist="Pitbull", album="Pitbull").
    """
    result: dict = {"album": "", "track_number": 0}
    if not artist or not title:
        return result
    try:
        resp = requests.get(
            "https://lrclib.net/api/search",
            params={"artist_name": artist, "track_name": title},
            headers={"User-Agent": "multiplatform-lyric-downloader/2 (github.com/tappyduckmancodes)"},
            timeout=6,
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            return result
        best = items[0]
        album = (best.get("albumName") or "").strip()
        track_num = best.get("trackNum") or 0
        if album and album.lower() != artist.lower():
            result["album"] = album
            result["track_number"] = track_num
    except Exception:
        pass
    return result
