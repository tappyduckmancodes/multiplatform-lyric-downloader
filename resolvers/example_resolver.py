"""
resolvers/example_resolver.py -- Template for adding a new streaming service.

To add a new service:
  1. Copy this file to yourservice_resolver.py in this folder
  2. Fill in NAME, URL_PATTERNS, CONFIG, setup(), and resolve()
  3. Run setup_wizard.py to enter credentials
  4. Pass a URL from that service — it will be auto-detected

The resolver only handles track metadata (title, artist, album, duration).
Lyrics are fetched separately by the plugins/ system, in priority order,
regardless of which resolver provided the track info.
"""

import logging
import re
from typing import Generator

from lyrics import TrackInfo
from resolvers.base import BaseResolver, ResolverConfig

logger = logging.getLogger(__name__)


class ExampleResolver(BaseResolver):
    NAME = "MyService"

    # Regex patterns that identify URLs this resolver can handle.
    # The first resolver whose pattern matches the URL will be used.
    URL_PATTERNS = [
        r"myservice\.com/(track|album|playlist|artist)/",
    ]

    CONFIG = [
        ResolverConfig(
            name="API Key",
            env_key="MYSERVICE_API_KEY",
            description="Your API key from myservice.com/developers",
            required=True,
            secret=True,
        ),
    ]

    def __init__(self):
        super().__init__()
        self._api_key: str | None = None

    def setup(self, config: dict) -> bool:
        self._api_key = config.get("MYSERVICE_API_KEY", "").strip()
        if not self._api_key:
            self._enabled = False
            return False
        self._enabled = True
        return True

    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]:
        """
        Yield TrackInfo objects for the given URL.

        'kind' is what the user passed as a flag: track/album/playlist/artist.
        You can also infer kind from the URL itself if you prefer.

        TrackInfo fields:
          track_id      -- Your service's unique ID for this track (string)
          title         -- Track title
          artist        -- All credited artists joined (e.g. "Artist A, Artist B")
          primary_artist -- Main/album artist only — used for folder name
          album         -- Album name
          duration_ms   -- Duration in milliseconds
          track_number  -- Track number on album (0 if unknown)
          disc_number   -- Disc number (1 if unknown/single disc)
        """
        # TODO: implement your URL parsing and API calls here
        # Example:
        #   match = re.search(r"myservice\.com/album/(\d+)", url)
        #   album_id = match.group(1)
        #   for track in myservice_api.get_album_tracks(album_id):
        #       yield TrackInfo(track_id=..., title=..., ...)
        return
        yield  # make this a generator
