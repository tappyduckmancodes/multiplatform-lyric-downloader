"""
plugins/youtube_plugin.py -- Lyrics from YouTube captions, converted to LRC.

Requires yt-dlp (pip install yt-dlp).

SUBTITLE FETCH STRATEGY (two-stage):

  Stage 1 — URL extraction from info dict:
    Call extract_info(download=False) to get video metadata including subtitle
    URLs. Look for VTT format in automatic_captions['en']. Fetch VTT directly
    with requests. Fast, no temp files.

  Stage 2 — File-writing fallback:
    If Stage 1 finds no URL (yt-dlp version differences, YouTube changes), fall
    back to ydl.download([url]) with skip_download=True + writeautomaticsub=True.
    This mimics `yt-dlp --write-auto-subs --skip-download` exactly.
    Write to a temp dir and read the resulting .vtt file.

VTT PARSING:
    YouTube auto-generated captions use a rolling-window format. Each cue
    contains all the text visible at that moment, growing word by word:
      [02.16s] "Give me everything"
      [05.00s] "Give me everything tonight"   <- same line, still growing
      [10.56s] "Tonight I want all of you"    <- new content -> new line
    Parse: if cue[i] starts with cue[i-1], update current line. When it doesn't,
    emit the completed line.
"""

import logging
import re
import tempfile
import time
from pathlib import Path

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo, _lrc_header

logger = logging.getLogger(__name__)

_VTT_TIMESTAMP_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})\s*-->")
_VTT_TAG_RE        = re.compile(r"<[^>]+>")


class YouTubePlugin(LyricsPlugin):
    NAME = "YouTube"
    PRIORITY = 40
    INSTALL_REQUIRES = "yt-dlp"
    CONFIG = []

    def setup(self, config: dict) -> bool:
        try:
            import yt_dlp  # noqa: F401
            self._enabled = True
            return True
        except ImportError:
            logger.warning(
                "YouTube lyrics plugin disabled -- yt-dlp not installed. "
                "To enable: pip install yt-dlp"
            )
            self._enabled = False
            return False

    def fetch(self, track: TrackInfo) -> str | None:
        try:
            import yt_dlp
        except ImportError:
            return None

        is_native_yt = bool(re.match(r'^[A-Za-z0-9_\-]{11}$', track.track_id))

        if is_native_yt:
            video_url = f"https://www.youtube.com/watch?v={track.track_id}"
            # Manual subtitles only — auto-generated captions are never used.
            result = self._get_captions(video_url, yt_dlp, track,
                                        allow_auto_generated=False)
            if result:
                return result
            logger.debug(
                "YouTube lyrics: no captions on native video '%s', "
                "trying lyric video search...", track.title
            )

        # Lyric video search: only accept MANUAL subtitles.
        # Auto-generated captions on a random lyric video are unreliable —
        # they're usually karaoke-style OCR of on-screen text, not real lyrics.
        query = f"{track.primary_artist} - {track.title} lyrics"
        search_url = self._search(query, yt_dlp)
        if not search_url:
            logger.debug("YouTube lyrics: no search result for '%s'", query)
            return None

        if is_native_yt:
            native = f"https://www.youtube.com/watch?v={track.track_id}"
            if search_url == native:
                logger.debug("YouTube lyrics: search returned same video, giving up")
                return None

        # Verify the search result video title roughly matches the track
        # to avoid grabbing lyrics for a completely different song
        if not self._title_matches(search_url, track, yt_dlp):
            logger.debug(
                "YouTube lyrics: search result title doesn't match '%s' — skipping",
                track.title,
            )
            return None

        result = self._get_captions(search_url, yt_dlp, track,
                                    allow_auto_generated=False)
        if not result:
            logger.debug("YouTube lyrics: no usable captions found for '%s'", track.title)
        return result

    def _search(self, query: str, yt_dlp) -> str | None:
        opts = {
            "quiet": True, "no_warnings": True,
            "extract_flat": True, "default_search": "ytsearch1",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
                entries = info.get("entries", [])
                if entries and entries[0].get("id"):
                    return f"https://www.youtube.com/watch?v={entries[0]['id']}"
        except Exception as e:
            logger.debug("YouTube search failed: %s", e)
        return None

    def _title_matches(self, video_url: str, track: TrackInfo, yt_dlp) -> bool:
        """
        Quick sanity-check: does the found video's title contain both the
        artist name and the track title (case-insensitive)?
        Prevents cross-song lyric grabs when the search finds an unrelated video.
        """
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                                   "extract_flat": True}) as ydl:
                info = ydl.extract_info(video_url, download=False)
            title = (info.get("title") or "").lower()
            # Normalise: strip punctuation for comparison
            import re as _re
            def normalise(s):
                return _re.sub(r"[^a-z0-9 ]", " ", s.lower())
            vid_title = normalise(title)
            expected_title = normalise(track.title)
            expected_artist = normalise(track.primary_artist)
            # Accept if video title contains key words from the track title
            # Use longest word in track title (avoids matching on "the", "a" etc.)
            title_words = [w for w in expected_title.split() if len(w) > 3]
            artist_words = [w for w in expected_artist.split() if len(w) > 2]
            title_match = all(w in vid_title for w in title_words[:3]) if title_words else True
            artist_match = any(w in vid_title for w in artist_words) if artist_words else True
            return title_match and artist_match
        except Exception as e:
            logger.debug("Title match check failed: %s", e)
            return True  # assume ok on error

    def _get_captions(self, url: str, yt_dlp, track: TrackInfo,
                      allow_auto_generated: bool = True) -> str | None:
        """
        Two-stage subtitle fetch.
        allow_auto_generated: if False, only manual/human subtitles are accepted.
        Stage 1: extract subtitle URLs from info dict, fetch VTT with requests.
        Stage 2: fall back to yt-dlp file-writing (mirrors --write-auto-subs CLI).
        """
        vtt = self._stage1_url_fetch(url, yt_dlp,
                                     allow_auto_generated=allow_auto_generated)
        if vtt:
            logger.debug("YouTube lyrics: got VTT via URL extraction")
        else:
            vtt = self._stage2_file_write(url, yt_dlp,
                                          allow_auto_generated=allow_auto_generated)
            if vtt:
                logger.debug("YouTube lyrics: got VTT via file-write fallback")

        if not vtt:
            return None

        lrc_body = _vtt_to_lrc(vtt)
        if not lrc_body:
            logger.debug("YouTube lyrics: VTT parsed but no usable lines")
            return None

        return _lrc_header(track, lyrics_source=self.NAME) + lrc_body

    # ── Stage 1: extract URL from info dict ────────────────────────────────────

    def _stage1_url_fetch(self, url: str, yt_dlp,
                          allow_auto_generated: bool = True) -> str | None:
        """
        Get subtitle URLs from yt-dlp info dict, fetch VTT with requests.
        Works when yt-dlp populates automatic_captions with format URLs.
        allow_auto_generated: if False, skip automatic_captions entirely.
        """
        import requests

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.debug("Stage1 info extraction failed: %s", e)
            return None

        if not info:
            return None

        # Manual subtitles first (human-curated, always preferred)
        sub_url = _pick_subtitle_url(info.get("subtitles") or {})
        if not sub_url and allow_auto_generated:
            sub_url = _pick_subtitle_url(info.get("automatic_captions") or {})

        if not sub_url:
            logger.debug("Stage1: no subtitle URL in info dict for %s", url)
            return None

        # Fetch VTT content
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)
            try:
                resp = requests.get(
                    sub_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                if resp.status_code == 429:
                    continue
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                logger.debug("Stage1 VTT fetch attempt %d: %s", attempt + 1, e)

        return None

    # ── Stage 2: file-writing fallback ─────────────────────────────────────────

    def _stage2_file_write(self, url: str, yt_dlp,
                           allow_auto_generated: bool = True) -> str | None:
        """
        Mirrors `yt-dlp --write-auto-subs --skip-download` exactly.
        allow_auto_generated: if False, only manual subtitles are written.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writeautomaticsub": allow_auto_generated,  # only if allowed
                "writesubtitles": True,  # always try manual subs
                "subtitleslangs": ["en", "en-orig", "en-US"],
                "subtitlesformat": "vtt/json3/srv3/best",
                "outtmpl": str(Path(tmpdir) / "%(id)s.%(ext)s"),
                "ignoreerrors": True,
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

                # Find any subtitle file (prefer .vtt)
                all_subs = list(Path(tmpdir).glob("*.vtt")) + \
                           list(Path(tmpdir).glob("*.json3")) + \
                           list(Path(tmpdir).glob("*.srv3")) + \
                           list(Path(tmpdir).glob("*.ttml"))
                if not all_subs:
                    logger.debug("Stage2: no subtitle files written to %s", tmpdir)
                    return None

                # Prefer en-orig > .en. > anything
                all_subs.sort(key=lambda f: (
                    0 if "en-orig" in f.name else
                    1 if ".en." in f.name else 2
                ))
                chosen = all_subs[0]
                logger.debug("Stage2: reading %s", chosen.name)
                content = chosen.read_text(encoding="utf-8", errors="replace")

                # If it's json3 format, convert to VTT-parseable text
                if chosen.suffix == ".json3":
                    content = _json3_to_vtt(content)

                return content

            except Exception as e:
                logger.debug("Stage2 subtitle download failed: %s", e)
                return None


# ── Subtitle URL helpers ────────────────────────────────────────────────────────

def _pick_subtitle_url(subs: dict) -> str | None:
    """
    Pick the best subtitle URL from a yt-dlp subtitles/automatic_captions dict.
    Prefers: en-orig > en > en-US > any English variant.
    Format preference: vtt > any (we'll handle other formats too).
    """
    lang_order = ["en-orig", "en", "en-US", "en-GB"]
    # First pass: preferred languages
    for lang in lang_order:
        if lang in subs:
            url = _best_url_from_formats(subs[lang])
            if url:
                return url
    # Second pass: any 'en*' language
    for lang, fmts in subs.items():
        if lang.startswith("en"):
            url = _best_url_from_formats(fmts)
            if url:
                return url
    return None


def _best_url_from_formats(fmts: list) -> str | None:
    """
    From a list of format dicts, return the best URL.
    Prefers vtt, then json3/srv3 (convertible), then any.
    """
    if not fmts:
        return None
    preferred_exts = ["vtt", "json3", "srv3", "ttml", "srv2", "srv1"]
    for ext in preferred_exts:
        for f in fmts:
            if isinstance(f, dict) and f.get("ext") == ext and f.get("url"):
                return f["url"]
    # Last resort: first URL we find
    for f in fmts:
        if isinstance(f, dict) and f.get("url"):
            return f["url"]
    return None


def _json3_to_vtt(json3_text: str) -> str:
    """Convert YouTube's json3 subtitle format to VTT for our parser."""
    import json
    try:
        data = json.loads(json3_text)
    except Exception:
        return json3_text  # hope for the best

    lines = ["WEBVTT", ""]
    for event in data.get("events", []):
        if not event.get("segs"):
            continue
        start_ms = event.get("tStartMs", 0)
        dur_ms = event.get("dDurationMs", 2000)
        end_ms = start_ms + dur_ms
        import html as _html
        text = _html.unescape("".join(s.get("utf8", "") for s in event["segs"]).strip())
        if not text or text == "\n":
            continue

        def ms_to_vtt(ms):
            h = ms // 3600000
            m = (ms % 3600000) // 60000
            s = (ms % 60000) // 1000
            frac = ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d}.{frac:03d}"

        lines.append(f"{ms_to_vtt(start_ms)} --> {ms_to_vtt(end_ms)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


# ── VTT → LRC ──────────────────────────────────────────────────────────────────

def _parse_vtt_ms(line: str) -> int | None:
    m = _VTT_TIMESTAMP_RE.match(line.strip())
    if not m:
        return None
    h, mn, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return ((h * 3600) + (mn * 60) + s) * 1000 + ms


def _vtt_to_lrc(vtt: str) -> str | None:
    """
    Convert WebVTT to LRC lines (no header).
    Collapses rolling-window duplicates: if cue[i] starts with cue[i-1],
    it's the same line still growing. When it doesn't, emit the completed line.
    """
    lines = vtt.splitlines()
    raw: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        ts = _parse_vtt_ms(lines[i])
        if ts is not None:
            parts: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                import html as _html
                clean = _html.unescape(_VTT_TAG_RE.sub("", lines[i])).strip()
                if clean:
                    parts.append(clean)
                i += 1
            text = " ".join(parts).strip()
            if text:
                raw.append((ts, text))
        else:
            i += 1

    if not raw:
        return None

    # Collapse rolling-window
    lyric_lines: list[tuple[int, str]] = []
    line_start, line_text = raw[0]
    for j in range(1, len(raw)):
        curr_ms, curr_text = raw[j]
        prev_text = raw[j - 1][1]
        if curr_text.startswith(prev_text):
            line_text = curr_text
        else:
            if line_text:
                lyric_lines.append((line_start, line_text))
            line_start, line_text = curr_ms, curr_text
    if line_text:
        lyric_lines.append((line_start, line_text))

    if not lyric_lines:
        return None

    lrc: list[str] = []
    for ms, text in lyric_lines:
        total_s = ms / 1000
        lrc.append(f"[{int(total_s//60):02d}:{total_s%60:05.2f}]{text}")
    return "\n".join(lrc)
