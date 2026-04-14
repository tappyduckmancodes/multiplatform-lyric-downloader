"""
plugins/example_plugin.py -- Template for building a new lyrics source plugin.

To add a new source:
  1. Copy this file to yourservice_plugin.py in this plugins/ folder
  2. Fill in NAME, PRIORITY, CONFIG, setup(), and fetch()
  3. Run the setup wizard (python setup_wizard.py) to enter your credentials
  4. That's it -- the loader will auto-discover it on next run

PRIORITY guide:
  10  = Spotify (highest priority, most accurate sync)
  20  = Deezer
  30  = LRCLIB
  40+ = Add your source here
"""

import logging

from plugins.base import LyricsPlugin, PluginConfig
from lyrics import TrackInfo

logger = logging.getLogger(__name__)


class ExamplePlugin(LyricsPlugin):
    NAME = "MyService"       # Human-readable name shown in logs and wizard
    PRIORITY = 40            # Lower = tried earlier in the waterfall
    CONFIG = [
        PluginConfig(
            name="MyService API Key",
            env_key="MYSERVICE_API_KEY",
            description="Your API key from myservice.com/developers",
            required=True,
            secret=True,       # Masked in setup wizard display
        ),
        PluginConfig(
            name="MyService Region",
            env_key="MYSERVICE_REGION",
            description="Optional region code (e.g. 'us', 'uk')",
            required=False,
            secret=False,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._api_key: str | None = None

    def setup(self, config: dict) -> bool:
        """
        Called once at startup. config is a dict of {env_key: value}
        for all keys declared in CONFIG above.

        Return True if ready to use, False to disable this plugin.
        """
        self._api_key = config.get("MYSERVICE_API_KEY", "").strip()
        if not self._api_key:
            self._enabled = False
            return False

        # TODO: optionally validate the key here (e.g. test API call)

        self._enabled = True
        return True

    def fetch(self, track: TrackInfo) -> str | None:
        """
        Fetch lyrics for the given track.
        Return an LRC-formatted string, or None if not found / not available.

        TrackInfo fields available:
          track.track_id      -- Spotify track ID
          track.title         -- Track title
          track.artist        -- All artists joined (e.g. "Bad Bunny, Drake")
          track.primary_artist -- Album/first artist only (use this for searches)
          track.album         -- Album name
          track.duration_ms   -- Duration in milliseconds
          track.track_number  -- Track number on album
          track.disc_number   -- Disc number

        LRC format reference:
          [ti:Title]
          [ar:Artist]
          [al:Album]
          [length:MM:SS]

          [MM:SS.xx]Line of lyrics here
          [MM:SS.xx]Next line
        """
        # TODO: implement your lyrics fetch logic here
        # Example:
        #   resp = requests.get("https://api.myservice.com/lyrics", params={...})
        #   return resp.json().get("lrc_content")
        return None
