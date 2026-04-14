"""
resolvers/base.py -- Abstract base class for metadata resolvers.

A resolver takes a URL from a streaming service and yields TrackInfo objects.
It is entirely separate from the lyrics plugin system — resolvers handle
WHERE the track list comes from, plugins handle WHERE the lyrics come from.

To add a new service:
  1. Copy example_resolver.py to yourservice_resolver.py in this folder
  2. Set NAME, URL_PATTERNS, CONFIG, INSTALL_REQUIRES, and implement
     can_handle() + resolve()
  3. Run setup_wizard.py to configure credentials
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator

from lyrics import TrackInfo


@dataclass
class ResolverConfig:
    name: str
    env_key: str
    description: str
    required: bool = True
    secret: bool = True


class BaseResolver(ABC):
    NAME: str = "Unknown"
    URL_PATTERNS: list[str] = []
    CONFIG: list[ResolverConfig] = field(default_factory=list)

    # pip package(s) needed, or None if built-in
    # e.g. "tidalapi" or "ytmusicapi"
    INSTALL_REQUIRES: str | None = None

    # Which lyrics plugin NAME should be tried first for this resolver's tracks.
    # None = use the configured priority order unchanged.
    # Set to e.g. "Deezer" so Deezer URLs try Deezer lyrics first.
    NATIVE_LYRICS_SOURCE: str | None = None

    def __init__(self):
        self._enabled = False
        self._compiled = [re.compile(p) for p in self.URL_PATTERNS]

    def can_handle(self, url: str) -> bool:
        return any(p.search(url) for p in self._compiled)

    @abstractmethod
    def setup(self, config: dict[str, str]) -> bool: ...

    @abstractmethod
    def resolve(self, url: str, kind: str) -> Generator[TrackInfo, None, None]: ...

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __repr__(self):
        return f"<{self.__class__.__name__} {'enabled' if self._enabled else 'disabled'}>"
