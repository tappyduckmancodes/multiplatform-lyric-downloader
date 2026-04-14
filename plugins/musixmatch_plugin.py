"""
plugins/musixmatch_plugin.py -- Synced lyrics from Musixmatch.

Uses the desktop app API endpoint (apic-desktop.musixmatch.com), which is
the same backend Deezer and other services use to display synced lyrics.

TOKEN:
  A usertoken is required. Options from easiest to hardest:
  1. Use the built-in community token (works until rate-limited or rotated).
  2. Get your own: open musixmatch.com in Firefox, F12 → Network → reload →
     click the www.musixmatch.com request → Cookies tab → copy musixmatchUserToken.
  3. Set MUSIXMATCH_TOKEN in .env to override the built-in token.

The subtitle endpoint returns an mxm-format JSON list that we parse to LRC.
Each entry: {"text": "...", "time": {"minutes": N, "seconds": N, "hundredths": N}}
"""

import json
import logging
import time

import requests

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo, _lrc_header

logger = logging.getLogger(__name__)

MXM_API = "https://apic-desktop.musixmatch.com/ws/1.1/"

# Community token — well-known, used by MxLRC and similar tools.
# Override by setting MUSIXMATCH_TOKEN in .env.
_COMMUNITY_TOKEN = "2203269256ff7abcb649269df00e14c833dbf4ddfb5b36a1aae8b0"

_HEADERS = {
    "Authority": "apic-desktop.musixmatch.com",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Musixmatch/0.19.4 Chrome/58.0.3029.110 "
        "Electron/1.7.6 Safari/537.36"
    ),
    "Cookie": "x-mxm-token-guid=",
}


class MusixmatchPlugin(LyricsPlugin):
    NAME = "Musixmatch"
    PRIORITY = 35  # After LRCLIB (30), before YouTube (40)
    CONFIG = [
        PluginConfig(
            name="Musixmatch Token",
            env_key="MUSIXMATCH_TOKEN",
            description=(
                "usertoken from musixmatch.com (optional — a community token "
                "is used by default). To get your own: open musixmatch.com in "
                "Firefox, F12 → Network tab → reload → click www.musixmatch.com "
                "→ Cookies → copy musixmatchUserToken value."
            ),
            required=False,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._token: str = _COMMUNITY_TOKEN
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def setup(self, config: dict) -> bool:
        token = config.get("MUSIXMATCH_TOKEN", "").strip()
        if token:
            self._token = token
            logger.debug("Musixmatch: using token from .env")
        else:
            logger.debug("Musixmatch: using community token")
        self._enabled = True
        return True

    def fetch(self, track: TrackInfo) -> str | None:
        """Fetch synced (timestamped) lyrics via matcher.subtitle.get."""
        duration_s = track.duration_ms / 1000 if track.duration_ms else 0

        params = {
            "format": "json",
            "namespace": "lyrics_richsynched",
            "subtitle_format": "mxm",
            "app_id": "web-desktop-app-v1.0",
            "usertoken": self._token,
            "q_artist": track.primary_artist,
            "q_track": track.title,
            "q_duration": str(int(duration_s)) if duration_s else "",
            "f_subtitle_length": str(int(duration_s)) if duration_s else "",
            "f_subtitle_length_max_deviation": "40",
        }

        data = self._api("matcher.subtitle.get", params)
        if data is None:
            return None

        status = data.get("message", {}).get("header", {}).get("status_code")
        hint = data.get("message", {}).get("header", {}).get("hint", "")

        if status == 401 and hint == "captcha":
            logger.debug("Musixmatch: captcha required, skipping")
            return None
        if status == 401 and hint == "renew":
            logger.debug("Musixmatch: token expired, skipping")
            return None
        if status not in (200, None):
            logger.debug("Musixmatch: API status %s hint=%s for '%s'", status, hint, track.title)
            return None

        subtitle = (
            data.get("message", {})
            .get("body", {})
            .get("subtitle", {})
        )
        if not subtitle:
            logger.debug("Musixmatch: no subtitle found for '%s'", track.title)
            return None

        subtitle_body = subtitle.get("subtitle_body", "")
        if not subtitle_body:
            return None

        try:
            lines = json.loads(subtitle_body)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Musixmatch: failed to parse subtitle JSON for '%s'", track.title)
            return None

        lrc_lines = []
        for entry in lines:
            text = (entry.get("text") or "").strip()
            t = entry.get("time", {})
            if not t:
                continue
            try:
                mins = int(t.get("minutes", 0))
                secs = int(t.get("seconds", 0))
                hundredths = int(t.get("hundredths", 0))
                lrc_lines.append(f"[{mins:02d}:{secs:02d}.{hundredths:02d}]{text}")
            except (TypeError, ValueError):
                continue

        if not lrc_lines:
            logger.debug("Musixmatch: subtitle body had no parseable lines for '%s'", track.title)
            return None

        logger.debug("Musixmatch: got %d synced lines for '%s'", len(lrc_lines), track.title)
        return _lrc_header(track, lyrics_source=self.NAME) + "\n".join(lrc_lines)

    def _refresh_token(self) -> bool:
        """
        Auto-renew the community token via Musixmatch's token.get endpoint.
        This is called when the API returns hint=renew (token expired/rate-limited).
        The auto-generated token is session-scoped and lasts ~10 minutes.
        """
        try:
            resp = self._session.get(
                MXM_API + "token.get",
                params={"user_language": "en", "app_id": "web-desktop-app-v1.0"},
                cookies={"AWSELB": "0", "AWSELBCORS": "0"},
                timeout=8,
            )
            data = resp.json()
            status = data.get("message", {}).get("header", {}).get("status_code")
            hint   = data.get("message", {}).get("header", {}).get("hint", "")
            if status == 401 and hint == "captcha":
                logger.debug("Musixmatch token renewal: captcha required")
                return False
            new_token = data.get("message", {}).get("body", {}).get("user_token", "")
            if new_token and new_token != "UpgradeOnlyUpgradeOnlyUpgradeOnlyUpgradeOnly":
                self._token = new_token
                logger.debug("Musixmatch: token auto-renewed")
                return True
        except Exception as e:
            logger.debug("Musixmatch token renewal failed: %s", e)
        return False

    def _api(self, method: str, params: dict, _retry: bool = True) -> dict | None:
        """Make a GET request to the Musixmatch desktop API. Auto-renews on hint=renew."""
        url = MXM_API + method
        try:
            resp = self._session.get(url, params=params, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            # Check for token expiry
            header = data.get("message", {}).get("header", {})
            if header.get("status_code") == 401 and header.get("hint") == "renew" and _retry:
                logger.debug("Musixmatch: token renew hint received, refreshing...")
                if self._refresh_token():
                    params["usertoken"] = self._token
                    return self._api(method, params, _retry=False)
            return data
        except requests.exceptions.Timeout:
            logger.debug("Musixmatch: timeout for %s", method)
        except Exception as e:
            logger.debug("Musixmatch: request failed (%s): %s", method, e)
        return None
