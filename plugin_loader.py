"""
plugin_loader.py -- Auto-discovers and loads lyrics plugins from plugins/.

Priority order can be customised via PLUGIN_ORDER in .env:
  PLUGIN_ORDER=Deezer,Spotify,YouTube,LRCLIB

The download loop can also request a native-first ordering per track
(e.g. "try Deezer first for this track because it came from a Deezer URL")
via fetch_with_plugins(plugins, track, native_first="Deezer").
"""

import importlib
import inspect
import logging
import os
import pkgutil
from pathlib import Path

import plugins
from plugins.base import LyricsPlugin
from lyrics import TrackInfo

logger = logging.getLogger(__name__)


def discover_plugins() -> list[type[LyricsPlugin]]:
    plugin_dir = Path(__file__).parent / "plugins"
    found = []
    for _, module_name, _ in pkgutil.iter_modules([str(plugin_dir)]):
        if module_name in ("base", "example_plugin"):
            continue
        try:
            module = importlib.import_module(f"plugins.{module_name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, LyricsPlugin)
                    and obj is not LyricsPlugin
                    and obj.__module__ == module.__name__
                ):
                    found.append(obj)
        except Exception as e:
            logger.warning("Failed to load plugin module '%s': %s", module_name, e)
    return _apply_priority_order(found)


def _apply_priority_order(classes: list[type[LyricsPlugin]]) -> list[type[LyricsPlugin]]:
    order_str = os.environ.get("PLUGIN_ORDER", "").strip()
    if not order_str:
        return sorted(classes, key=lambda c: c.PRIORITY)
    order = [n.strip() for n in order_str.split(",") if n.strip()]
    name_to_pos = {n: i for i, n in enumerate(order)}
    return sorted(classes, key=lambda c: (name_to_pos.get(c.NAME, 99), c.PRIORITY))


def initialize_plugins(env: dict) -> list[LyricsPlugin]:
    """Instantiate all discovered plugins. Returns all (enabled + disabled)
    so the setup wizard can inspect INSTALL_REQUIRES."""
    instances = []
    for cls in discover_plugins():
        instance = cls()
        config = {cfg.env_key: env.get(cfg.env_key, "") for cfg in cls.CONFIG}
        try:
            instance.setup(config)
        except Exception as e:
            logger.warning("Plugin %s failed to initialize: %s", cls.NAME, e)
        instances.append(instance)
    return instances


def get_active_plugins(all_plugins: list[LyricsPlugin]) -> list[LyricsPlugin]:
    return [p for p in all_plugins if p.enabled]


def reorder_for_native(
    plugins: list[LyricsPlugin],
    native_first: str | None,
    source_override: str | None,
) -> list[LyricsPlugin]:
    """
    Return a reordered copy of plugins for a specific track's fetch attempt.

    source_override (from -source flag): locks to that one plugin only.
    native_first (from resolver): moves that plugin to front of waterfall.
    """
    if source_override:
        # Only try the explicitly requested source
        forced = [p for p in plugins if p.NAME.lower() == source_override.lower() and p.enabled]
        if not forced:
            logger.warning(
                "-source '%s' not found or not enabled. Available: %s",
                source_override,
                ", ".join(p.NAME for p in plugins if p.enabled),
            )
        return forced

    if not native_first:
        return plugins

    # Move the native plugin to front, keep everything else in order
    native = [p for p in plugins if p.NAME == native_first]
    rest   = [p for p in plugins if p.NAME != native_first]
    return native + rest


def _update_stats(plugin_stats, name, key):
    if plugin_stats is not None:
        plugin_stats.setdefault(name, {"hit": 0, "miss": 0, "err": 0})
        plugin_stats[name][key] += 1


def _has_timestamps(lrc: str) -> bool:
    """Return True if the LRC string contains at least one [mm:ss.xx] line."""
    import re
    return bool(re.search(r"\[\d{2}:\d{2}\.\d{2}\]", lrc))


def fetch_with_plugins(
    plugins: list[LyricsPlugin],
    track: TrackInfo,
    native_first: str | None = None,
    source_override: str | None = None,
    plugin_stats: dict | None = None,
) -> tuple[str, str] | tuple[None, None]:
    """
    Two-pass waterfall:
    Pass 1 — try each plugin via .fetch(); accept only synced (timestamped) results.
    Pass 2 — if nothing synced found, try .fetch_plain() on each plugin (plain text ok).

    This ensures a plain-text Deezer result never blocks a synced Spotify/LRCLIB result.
    """
    ordered = reorder_for_native(plugins, native_first, source_override)

    # Pass 1: synced lyrics only
    for plugin in ordered:
        if not plugin.enabled:
            continue
        try:
            result = plugin.fetch(track)
            if result and _has_timestamps(result):
                _update_stats(plugin_stats, plugin.NAME, "hit")
                return result, plugin.NAME
            elif result:
                # Got content but no timestamps — log as partial miss, keep trying
                logger.info("  [unsync][%s]  %s", plugin.NAME, track.title)
                _update_stats(plugin_stats, plugin.NAME, "miss")
            else:
                logger.info("  [miss][%s]  %s", plugin.NAME, track.title)
                _update_stats(plugin_stats, plugin.NAME, "miss")
        except Exception as e:
            logger.warning("  [err][%s]  %s -- %s", plugin.NAME, track.title, e)
            _update_stats(plugin_stats, plugin.NAME, "err")

    # Pass 2: accept plain text as fallback (better than nothing)
    for plugin in ordered:
        if not plugin.enabled:
            continue
        try:
            # Use fetch_plain() if available, otherwise fall back to fetch()
            fn = getattr(plugin, "fetch_plain", plugin.fetch)
            result = fn(track)
            if result:
                logger.info("  [plain][%s]  %s", plugin.NAME, track.title)
                _update_stats(plugin_stats, plugin.NAME, "hit")
                return result, f"{plugin.NAME}(plain)"
        except Exception as e:
            logger.debug("  [err plain][%s]  %s -- %s", plugin.NAME, track.title, e)

    return None, None


def list_plugins(all_plugins: list[LyricsPlugin]):
    print("\nLyrics plugins (in priority order):")
    for p in all_plugins:
        if p.enabled:
            status = "enabled"
            hint = ""
        elif hasattr(p, "INSTALL_REQUIRES") and p.INSTALL_REQUIRES:
            status = "disabled"
            hint = f"  →  pip install {p.INSTALL_REQUIRES}"
        else:
            status = "disabled (missing credentials)"
            hint = "  →  run: python setup_wizard.py"
        print(f"  {p.NAME:<22} {status}{hint}")
    print()
