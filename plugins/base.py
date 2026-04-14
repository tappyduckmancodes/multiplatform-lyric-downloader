"""
plugins/base.py -- Abstract base class for all lyrics source plugins.

To create a new plugin:
  1. Copy example_plugin.py to yourservice_plugin.py in this folder
  2. Implement the required methods
  3. That's it -- it will be auto-discovered on next run

The plugin loader calls plugins in ascending priority order (lower = higher priority).
If a plugin returns None, the next one is tried automatically.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PluginConfig:
    """
    Declares what config keys a plugin needs from the environment / .env file.

    name:        Display name shown in the setup wizard
    env_key:     The key in .env / os.environ
    description: Shown in the setup wizard
    required:    If True, plugin is disabled when this key is missing
    secret:      If True, value is masked in the setup wizard display
    """
    name: str
    env_key: str
    description: str
    required: bool = True
    secret: bool = True


class LyricsPlugin(ABC):
    """
    Base class for all lyrics source plugins.

    Subclasses must set:
      NAME       str   -- Human-readable source name (e.g. "Deezer")
      PRIORITY   int   -- Load order; lower = tried first (Spotify=10, Deezer=20, LRCLIB=30)
      CONFIG     list  -- List of PluginConfig objects this plugin needs

    Subclasses must implement:
      setup(config)    -- Called once at startup with the relevant env values
      fetch(track)     -- Returns an LRC string or None
    """

    NAME: str = "Unknown"
    PRIORITY: int = 50
    CONFIG: list[PluginConfig] = field(default_factory=list)

    def __init__(self):
        self._enabled = False

    @abstractmethod
    def setup(self, config: dict[str, str]) -> bool:
        """
        Called at startup with a dict of {env_key: value} for this plugin's CONFIG.
        Return True if the plugin is ready to use, False to disable it.
        """
        ...

    @abstractmethod
    def fetch(self, track) -> str | None:
        """
        Fetch lyrics for the given TrackInfo.
        Return an LRC-formatted string, or None if not found.
        """
        ...

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __repr__(self):
        status = "enabled" if self._enabled else "disabled"
        return f"<{self.__class__.__name__} priority={self.PRIORITY} {status}>"
