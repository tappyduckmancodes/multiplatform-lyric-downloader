"""
auth.py — Spotify lyrics Bearer token management.

sp_dc auto-refresh is permanently broken since 2024 (Spotify added TLS
fingerprinting). The only working method is a manually-copied Bearer token
from DevTools. These tokens are valid for roughly 1 hour from when Spotify
issued them (not from when you paste them in).

Token sources (tried in order):
  1. .cache/spotify_token.json  — cached from a previous run, checked for expiry
  2. SPOTIFY_AUTH_TOKEN in .env — raw token, written to cache on first use
  3. Interactive prompt           — if neither above works

Re-auth during a run:
  SpotifyAuth.reauth() can be called mid-run (e.g. after a 401) to get a fresh
  token without restarting. It offers to update .env so future runs work immediately.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = Path(__file__).parent / ".cache" / "spotify_token.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://open.spotify.com/",
    "Origin": "https://open.spotify.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "App-Platform": "WebPlayer",
    "Spotify-App-Version": "1.2.46.25.g21775a4b",
}


class SpotifyAuth:
    def __init__(self, sp_dc: str | None = None, manual_token: str | None = None):
        if sp_dc:
            logger.warning(
                "SPOTIFY_SP_DC is set but is non-functional (Spotify added TLS "
                "fingerprinting in 2024). Using Bearer token instead. "
                "You can safely remove SPOTIFY_SP_DC from your .env."
            )
        self._manual_token: str | None = manual_token
        self._token: str | None = None
        self._expires_at: float = 0
        # Load cache + .env token on init
        self._load_cache()
        if not self._is_valid():
            self._acquire_token()

    def _is_valid(self) -> bool:
        return bool(self._token) and time.time() < self._expires_at - 60

    def get_token(self) -> str:
        if not self._is_valid():
            self._acquire_token()
        return self._token

    @property
    def session_headers(self) -> dict:
        return {**HEADERS, "Authorization": f"Bearer {self.get_token()}"}

    def notify_401(self):
        """
        Called by the Spotify plugin when a request returns 401.
        Clears the cached token and prompts for a new one immediately
        so the rest of the run (e.g. remaining artist tracks) can continue.
        """
        logger.warning(
            "Spotify: Bearer token rejected (401). It has probably expired."
        )
        self._token = None
        self._expires_at = 0
        TOKEN_CACHE_FILE.unlink(missing_ok=True)
        self._prompt_for_token(reason="Token expired during this run.")

    def reauth(self):
        """Force re-authentication (clears cache and prompts)."""
        self._token = None
        self._expires_at = 0
        TOKEN_CACHE_FILE.unlink(missing_ok=True)
        self._acquire_token()

    def _load_cache(self):
        """Load token from .cache/spotify_token.json if present and valid."""
        if TOKEN_CACHE_FILE.exists():
            try:
                data = json.loads(TOKEN_CACHE_FILE.read_text())
                token = data.get("token", "")
                if token.lower().startswith("bearer "):
                    token = token[7:].strip()
                expires_at = data.get("expires_at", 0)
                remaining = expires_at - time.time()
                if token and remaining > 60:
                    self._token = token
                    self._expires_at = expires_at
                    logger.debug(
                        "Spotify: loaded cached token (expires in %dm %ds)",
                        int(remaining // 60), int(remaining % 60),
                    )
                    return
                elif token:
                    logger.debug("Spotify: cached token has expired, discarding.")
            except Exception:
                pass

    def _save_cache(self, token: str, expires_at: float):
        self._token = token
        self._expires_at = expires_at
        try:
            TOKEN_CACHE_FILE.parent.mkdir(exist_ok=True)
            TOKEN_CACHE_FILE.write_text(
                json.dumps({"token": token, "expires_at": expires_at})
            )
        except OSError:
            pass

    def _acquire_token(self):
        """Resolve a token from .env or interactive prompt."""
        if self._manual_token:
            token = self._manual_token.strip()
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            # .env token: we don't know when Spotify issued it so we treat it
            # as expiring in 1 hour from NOW. If it's already expired, the first
            # 401 will trigger notify_401() and prompt for a fresh one.
            self._save_cache(token, time.time() + 3600)
            logger.debug("Spotify: using SPOTIFY_AUTH_TOKEN from .env.")
            return
        self._prompt_for_token()

    def _prompt_for_token(self, reason: str = ""):
        print()
        print("=" * 65)
        print("  Spotify Bearer Token Required")
        print("=" * 65)
        if reason:
            print(f"  {reason}")
            print()
        print("  How to get a fresh token (takes ~30 seconds):")
        print("  1. Open https://open.spotify.com and play any song")
        print("  2. DevTools (F12) -> Network tab -> filter: color-lyrics")
        print("  3. Click that request -> Headers -> 'authorization'")
        print("  4. Copy everything AFTER 'Bearer ' (the BQA... part)")
        print()
        print("  Tokens are valid ~1 hour from when Spotify issues them.")
        print()
        try:
            token = input("  Paste token (or press Enter to skip Spotify): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            logger.warning("No token provided — Spotify lyrics will be skipped this run.")
            self._token = "__SKIP__"
            self._expires_at = time.time() + 3600
            return

        if not token:
            logger.warning("No token provided — Spotify lyrics will be skipped this run.")
            self._token = "__SKIP__"
            self._expires_at = time.time() + 3600
            return

        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        # Offer to save to .env so next run doesn't prompt
        env_path = Path(".env")
        try:
            save = input(
                "  Save to .env as SPOTIFY_AUTH_TOKEN? [Y/n]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            save = "n"

        if save != "n":
            _update_env(env_path, "SPOTIFY_AUTH_TOKEN", token)
            print("  Saved to .env.")
            self._manual_token = token  # so next _acquire_token uses it

        self._save_cache(token, time.time() + 3600)
        remaining_min = int((self._expires_at - time.time()) // 60)
        logger.info("Spotify: token cached, good for up to ~%d minutes.", remaining_min)


def _update_env(env_path: Path, key: str, value: str):
    """Write or update a key=value line in the .env file."""
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("Could not update .env: %s", e)
