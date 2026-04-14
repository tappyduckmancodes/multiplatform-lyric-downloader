"""
resolver_loader.py -- Auto-discovers and routes metadata resolvers.

Key behaviours:
  - RESOLVER_ORDER in .env sets priority order
  - Each resolver declares NATIVE_LYRICS_SOURCE so the download loop
    can try the matching lyrics plugin first for that service's tracks
  - Missing optional deps (tidalapi, ytmusicapi) produce a WARNING with
    the install command, not a silent DEBUG message
"""

import importlib
import urllib.request
import inspect
import logging
import os
import pkgutil
from pathlib import Path
from typing import Generator

import resolvers
from resolvers.base import BaseResolver
from lyrics import TrackInfo

logger = logging.getLogger(__name__)

_SPECIAL = {"playing"}


def discover_resolvers() -> list[type[BaseResolver]]:
    resolver_dir = Path(__file__).parent / "resolvers"
    found = []
    for _, module_name, _ in pkgutil.iter_modules([str(resolver_dir)]):
        if module_name in ("base", "example_resolver"):
            continue
        try:
            module = importlib.import_module(f"resolvers.{module_name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseResolver)
                    and obj is not BaseResolver
                    and obj.__module__ == module.__name__
                ):
                    found.append(obj)
        except Exception as e:
            logger.warning("Failed to load resolver module '%s': %s", module_name, e)
    return _apply_order(found)


def _apply_order(classes: list[type[BaseResolver]]) -> list[type[BaseResolver]]:
    order_str = os.environ.get("RESOLVER_ORDER", "").strip()
    default_order = ["Spotify", "Deezer", "Tidal", "YouTube Music"]

    if not order_str:
        order = default_order
    else:
        order = [n.strip() for n in order_str.split(",") if n.strip()]

    name_to_pos = {n: i for i, n in enumerate(order)}
    return sorted(classes, key=lambda c: (name_to_pos.get(c.NAME, 99), c.NAME))


def initialize_resolvers(env: dict) -> list[BaseResolver]:
    """Instantiate and setup all discovered resolvers. Returns all instances
    (both enabled and disabled) so the setup wizard can inspect INSTALL_REQUIRES."""
    instances = []
    for cls in discover_resolvers():
        instance = cls()
        config = {cfg.env_key: env.get(cfg.env_key, "") for cfg in cls.CONFIG}
        try:
            instance.setup(config)
        except Exception as e:
            logger.warning("Resolver %s failed to init: %s", cls.NAME, e)
        instances.append(instance)
    return instances


def get_active_resolvers(all_resolvers: list[BaseResolver]) -> list[BaseResolver]:
    return [r for r in all_resolvers if r.enabled]


def _expand_short_url(url: str) -> str:
    """
    Follow redirects for known short-link domains so resolvers can match them.
    Handles: link.deezer.com, deezer.page.link, spotify.link, etc.
    """
    short_domains = ("link.deezer.com", "deezer.page.link", "spotify.link",
                     "spoti.fi", "youtu.be")
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if any(parsed.netloc.endswith(d) for d in short_domains):
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                final = resp.url
            if final and final != url:
                logger.debug("Expanded short URL %s -> %s", url, final)
                return final
    except Exception as e:
        logger.debug("Short URL expansion failed for %s: %s", url, e)
    return url


def route(
    active: list[BaseResolver],
    url: str,
    kind: str,
) -> tuple[Generator[TrackInfo, None, None], str | None]:
    """
    Route a URL to the appropriate resolver.
    Returns (track_generator, native_lyrics_source_name).
    native_lyrics_source_name is the plugin name to try first (e.g. "Deezer"),
    or None if the resolver has no native lyrics counterpart.
    """
    # Expand short/redirect URLs before routing
    url = _expand_short_url(url)

    if kind in _SPECIAL:
        for r in active:
            if r.NAME == "Spotify" and r.enabled:
                return r.resolve(url, kind), r.NATIVE_LYRICS_SOURCE
        logger.error("Spotify resolver is required for -%s but is not enabled.", kind)
        return _empty_gen(), None

    for r in active:
        if r.enabled and r.can_handle(url):
            logger.info("Resolver: %s  ← %s", r.NAME, url)
            return r.resolve(url, kind), r.NATIVE_LYRICS_SOURCE

    logger.error(
        "No resolver matched URL: %s\n"
        "  Active resolvers: %s\n"
        "  Supported: Spotify (open.spotify.com), Deezer (deezer.com),\n"
        "             Tidal (tidal.com), YouTube Music (music.youtube.com)",
        url,
        ", ".join(r.NAME for r in active if r.enabled) or "none",
    )
    return _empty_gen(), None


def _empty_gen():
    return
    yield


def list_resolvers(all_resolvers: list[BaseResolver]):
    print("\nMetadata resolvers (in priority order):")
    for r in all_resolvers:
        if r.enabled:
            status = "enabled"
            hint = ""
        elif r.INSTALL_REQUIRES:
            status = "disabled"
            hint = f"  →  pip install {r.INSTALL_REQUIRES}"
        else:
            status = "disabled (missing credentials)"
            hint = "  →  run: python setup_wizard.py"
        print(f"  {r.NAME:<22} {status}{hint}")
    print()
