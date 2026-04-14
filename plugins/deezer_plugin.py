"""
plugins/deezer_plugin.py -- Deezer lyrics via ARL cookie + gw-light API.

Uses a persistent requests.Session with the ARL cookie set on the session jar.
The CSRF checkForm token is session-scoped.

LYRICS API STRATEGY:
  Primary:  deezer.pageTrack  — returns richer data including nested LYRICS object.
            results.LYRICS.LYRICS_SYNC_JSON — highest coverage for synced lyrics.
  Fallback: song.getLyrics    — simpler, but many tracks return empty LYRICS_SYNC_JSON
            even when synced lyrics exist (Deezer's coverage varies by method).

  sng_id must be sent as INTEGER in JSON body, not string.
  lrc_timestamp field may be None for blank/instrumental lines — use `or ""` not `.get(k, "")`.
"""

import json
import logging
import time
from pathlib import Path

import requests

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo, _lrc_header

logger = logging.getLogger(__name__)

DEEZER_GW_URL     = "https://www.deezer.com/ajax/gw-light.php"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
CACHE_FILE = Path(__file__).parent.parent / ".cache" / "deezer_token.json"
TOKEN_TTL  = 2700  # 45 min

GW_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.deezer.com",
    "Referer": "https://www.deezer.com/",
}


class DeezerPlugin(LyricsPlugin):
    NAME = "Deezer"
    PRIORITY = 20
    CONFIG = [
        PluginConfig(
            name="Deezer ARL Cookie",
            env_key="DEEZER_ARL",
            description="arl cookie from deezer.com DevTools -> Application -> Cookies (~3 months)",
            required=True,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._arl: str | None = None
        self._api_token: str | None = None
        self._expires_at: float = 0
        self._session: requests.Session | None = None

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(GW_HEADERS)
        if self._arl:
            s.cookies.set("arl", self._arl, domain=".deezer.com")
        return s

    def setup(self, config: dict) -> bool:
        arl = config.get("DEEZER_ARL", "").strip()
        if not arl:
            logger.debug("Deezer: no ARL provided.")
            self._enabled = False
            return False

        self._arl = arl
        self._session = self._make_session()

        try:
            data = self._gw_post("deezer.getUserData", {}, token="null")
            if not data:
                raise ValueError("Empty response")
            user  = data.get("results", {}).get("USER", {})
            token = data.get("results", {}).get("checkForm")
            if user.get("USER_ID", 0) != 0 and token and token != "null":
                name = user.get("BLOG_NAME") or user.get("EMAIL") or "unknown"
                plan = data.get("results", {}).get("OFFER_NAME", "")
                logger.info(
                    "Deezer lyrics: signed in as %s%s",
                    name, f"  ({plan})" if plan else "",
                )
                self._api_token = token
                self._expires_at = time.time() + TOKEN_TTL
                self._save_cache(token)
                self._enabled = True
                return True
            else:
                logger.warning(
                    "Deezer lyrics: ARL rejected or expired. "
                    "Re-run setup_wizard.py to update it."
                )
                self._enabled = False
                return False
        except Exception as e:
            logger.warning("Deezer lyrics: connectivity check failed: %s", e)
            self._enabled = False
            return False

    def fetch(self, track: TrackInfo) -> str | None:
        """Fetch synced lyrics. Returns None if no timestamps found — lets waterfall continue."""
        token = self._get_token()
        if not token:
            return None

        if track.track_id.isdigit():
            deezer_id = track.track_id
            logger.debug("Deezer lyrics: using native ID %s", deezer_id)
        else:
            deezer_id = self._search_id(track)
            if not deezer_id:
                logger.debug("Deezer lyrics: no search match for '%s'", track.title)
                return None

        # Try pageTrack first (higher synced-lyrics coverage), fall back to song.getLyrics
        lrc = self._try_page_track(deezer_id, token, track)
        if lrc:
            return lrc
        lrc = self._try_get_lyrics(deezer_id, token, track)
        return lrc  # None if no synced lyrics found

    def fetch_plain(self, track: TrackInfo) -> str | None:
        """
        Plain-text fallback — called by waterfall after all synced sources fail.
        Tries both methods and accepts plain text.
        """
        token = self._get_token()
        if not token:
            return None

        if track.track_id.isdigit():
            deezer_id = track.track_id
        else:
            deezer_id = self._search_id(track)
            if not deezer_id:
                return None

        # Try pageTrack first
        raw = self._fetch_page_track_raw(deezer_id, token)
        if raw:
            result = self._parse_lyrics(raw, track, accept_plain=True)
            if result:
                return result

        # Fall back to song.getLyrics
        raw2 = self._fetch_get_lyrics_raw(deezer_id, token)
        if raw2:
            return self._parse_lyrics(raw2, track, accept_plain=True)

        return None

    # ── Primary method: deezer.pageTrack ──────────────────────────────────────

    def _try_page_track(self, deezer_id: str, token: str, track: TrackInfo) -> str | None:
        raw = self._fetch_page_track_raw(deezer_id, token)
        if not raw:
            return None
        return self._parse_lyrics(raw, track, accept_plain=False)

    def _fetch_page_track_raw(self, deezer_id: str, token: str) -> dict | None:
        """
        Call deezer.pageTrack which returns a richer object.
        Lyrics are nested under results.DATA.LYRICS (or results.LYRICS directly).
        """
        try:
            sng_id_int = int(deezer_id)
        except ValueError:
            sng_id_int = deezer_id

        data = self._gw_post(
            "deezer.pageTrack",
            {"sng_id": sng_id_int},
            token=token,
        )
        if not data:
            return None

        errors = data.get("error", {})
        if errors:
            if "VALID_TOKEN_REQUIRED" in str(errors):
                self._refresh_token()
                return None
            logger.debug("Deezer pageTrack error (id=%s): %s", deezer_id, errors)
            return None

        results = data.get("results", {})
        if not results:
            logger.debug("Deezer pageTrack: empty results for id=%s", deezer_id)
            return None

        logger.debug("Deezer pageTrack results keys for id=%s: %s", deezer_id, list(results.keys())[:10])

        # Lyrics may be nested under "LYRICS" key or at top level of results
        lyrics = results.get("LYRICS") or {}
        if not lyrics:
            # Some responses embed LYRICS inside DATA
            lyrics = results.get("DATA", {}).get("LYRICS") or {}
        if not lyrics:
            # Try SONG_LYRICS (older API format)
            lyrics = results.get("SONG_LYRICS") or {}

        if not lyrics:
            logger.info(
                "  [Deezer/pageTrack] id=%s: no LYRICS key found. "
                "Top-level results keys: %s",
                deezer_id, list(results.keys())[:10],
            )
            return None

        sync = lyrics.get("LYRICS_SYNC_JSON") or []
        plain = (lyrics.get("LYRICS_TEXT") or lyrics.get("LYRICS_TEXT_PLAIN") or "")
        logger.info(
            "  [Deezer/pageTrack] id=%s: sync=%d entries, plain=%s",
            deezer_id, len(sync), bool(plain),
        )
        # Always log all keys present in the LYRICS object so we can spot unexpected fields
        logger.debug("  [Deezer/pageTrack] LYRICS keys: %s", list(lyrics.keys()))
        if sync:
            # Log first entry with ALL its keys so we can see timestamp field names exactly
            logger.debug("  [Deezer/pageTrack] LYRICS_SYNC_JSON[0] keys: %s", list(sync[0].keys()))
            logger.debug("  [Deezer/pageTrack] LYRICS_SYNC_JSON[0] full: %s", sync[0])
        elif not plain:
            # Neither sync nor plain — dump everything so we can see what Deezer returned
            logger.debug(
                "  [Deezer/pageTrack] id=%s: LYRICS present but empty. Full LYRICS dict: %s",
                deezer_id, lyrics,
            )
        return lyrics

    # ── Fallback method: song.getLyrics ───────────────────────────────────────

    def _try_get_lyrics(self, deezer_id: str, token: str, track: TrackInfo) -> str | None:
        raw = self._fetch_get_lyrics_raw(deezer_id, token)
        if not raw:
            return None
        return self._parse_lyrics(raw, track, accept_plain=False)

    def _fetch_get_lyrics_raw(self, deezer_id: str, token: str) -> dict | None:
        try:
            sng_id_int = int(deezer_id)
        except ValueError:
            sng_id_int = deezer_id

        data = self._gw_post("song.getLyrics", {"sng_id": sng_id_int}, token=token)
        if not data:
            return None

        errors = data.get("error", {})
        if errors:
            if "VALID_TOKEN_REQUIRED" in str(errors):
                self._refresh_token()
                return None
            logger.debug("Deezer getLyrics error (id=%s): %s", deezer_id, errors)
            return None

        result = data.get("results", {})
        if not result:
            return None

        sync = result.get("LYRICS_SYNC_JSON") or []
        plain = result.get("LYRICS_TEXT", "")
        logger.info(
            "  [Deezer/getLyrics] id=%s: sync=%d entries, plain=%s",
            deezer_id, len(sync), bool(plain),
        )
        # Log all top-level keys so we can spot any fields we're not currently reading
        logger.debug("  [Deezer/getLyrics] result keys: %s", list(result.keys()))
        if sync:
            logger.debug("  [Deezer/getLyrics] LYRICS_SYNC_JSON[0] keys: %s", list(sync[0].keys()))
            logger.debug("  [Deezer/getLyrics] LYRICS_SYNC_JSON[0] full: %s", sync[0])
        elif not plain:
            # Complete miss — dump full result so we can see what was actually returned
            logger.debug(
                "  [Deezer/getLyrics] id=%s: no sync or plain. Full result: %s",
                deezer_id, result,
            )
        return result

    # ── Shared parser ─────────────────────────────────────────────────────────

    def _parse_lyrics(self, result: dict, track: TrackInfo, accept_plain: bool = False) -> str | None:
        """
        Convert a Deezer lyrics dict (from either pageTrack or getLyrics) to LRC.

        Field names work for both methods:
          LYRICS_SYNC_JSON  — list of entries with lrc_timestamp / milliseconds / line
          LYRICS_TEXT       — plain text fallback

        Critical: use `(entry.get("lrc_timestamp") or "")` — key may exist with None value.
        """
        sync_json  = result.get("LYRICS_SYNC_JSON") or []
        plain_text = result.get("LYRICS_TEXT", "")

        if sync_json:
            header = _lrc_header(track, lyrics_source=self.NAME)
            lines: list[str] = []
            has_ts = False

            # Log the first entry in detail so we can see exactly what field names
            # and timestamp formats Deezer returned for this specific track
            if sync_json:
                first = sync_json[0]
                logger.debug(
                    "  [Deezer/parse] First LYRICS_SYNC_JSON entry — "
                    "keys=%s  lrc_timestamp=%r  milliseconds=%r  line=%r",
                    list(first.keys()),
                    first.get("lrc_timestamp"),
                    first.get("milliseconds"),
                    first.get("line") or first.get("LINE"),
                )

            for entry in sync_json:
                lrc_ts = (entry.get("lrc_timestamp") or "").strip()
                text   = (entry.get("line") or entry.get("LINE") or "").strip()

                if not lrc_ts:
                    ms_raw = entry.get("milliseconds") or entry.get("MILLISECONDS")
                    if ms_raw:
                        try:
                            total_s = int(ms_raw) / 1000
                            lrc_ts = f"[{int(total_s//60):02d}:{total_s%60:05.2f}]"
                        except (ValueError, TypeError):
                            pass

                if lrc_ts:
                    lines.append(f"{lrc_ts}{text}")
                    has_ts = True
                elif text:
                    lines.append(text)

            if lines and has_ts:
                logger.info("  [Deezer] '%s': saved with timestamps ✓", track.title)
                return header + "\n".join(lines)
            elif lines and accept_plain:
                logger.info("  [Deezer] '%s': sync data present but no timestamps — saving plain", track.title)
                return header + "\n".join(lines)
            elif lines:
                logger.info(
                    "  [Deezer] '%s': sync entries have no timestamps (keys: %s) — passing to next source",
                    track.title, list(sync_json[0].keys()) if sync_json else [],
                )
                return None
            return None

        if plain_text:
            if accept_plain:
                logger.info("  [Deezer] '%s': plain text only", track.title)
                return _lrc_header(track, lyrics_source=self.NAME) + plain_text
            logger.info(
                "  [Deezer] '%s': plain text only (no sync data) — passing to next source",
                track.title,
            )
            return None

        return None

    # ── Token / session helpers ───────────────────────────────────────────────

    def _refresh_token(self):
        """Force a fresh token — called after CSRF errors."""
        logger.debug("Deezer: refreshing token after CSRF error")
        self._session = self._make_session()
        self._api_token = None
        self._expires_at = 0
        CACHE_FILE.unlink(missing_ok=True)

    def _gw_post(self, method: str, body: dict, token: str | None = None) -> dict | None:
        if token is None:
            token = self._api_token or "null"
        if self._session is None:
            self._session = self._make_session()
        try:
            resp = self._session.post(
                DEEZER_GW_URL,
                params={
                    "method": method,
                    "api_version": "1.0",
                    "api_token": token,
                    "input": "3",
                },
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("Deezer gw-light POST failed (%s): %s", method, e)
            return None

    def _get_token(self) -> str | None:
        if self._api_token and time.time() < self._expires_at:
            return self._api_token
        if CACHE_FILE.exists():
            try:
                cached = json.loads(CACHE_FILE.read_text())
                if time.time() < cached.get("expires_at", 0):
                    self._api_token = cached["token"]
                    self._expires_at = cached["expires_at"]
                    return self._api_token
            except Exception:
                pass
        data = self._gw_post("deezer.getUserData", {}, token="null")
        if not data:
            return None
        token = data.get("results", {}).get("checkForm")
        if not token or token == "null":
            return None
        self._api_token = token
        self._expires_at = time.time() + TOKEN_TTL
        self._save_cache(token)
        return token

    def _save_cache(self, token: str):
        try:
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(
                json.dumps({"token": token, "expires_at": self._expires_at})
            )
        except OSError:
            pass

    def _search_id(self, track: TrackInfo) -> str | None:
        query = f'artist:"{track.primary_artist}" track:"{track.title}"'
        try:
            resp = requests.get(
                DEEZER_SEARCH_URL,
                params={"q": query, "limit": 1},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
            if items:
                return str(items[0]["id"])
        except Exception as e:
            logger.debug("Deezer search failed for '%s': %s", track.title, e)
        return None
