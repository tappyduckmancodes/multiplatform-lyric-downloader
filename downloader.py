#!/usr/bin/env python3
"""
downloader.py -- Main CLI for multiplatform-lyric-downloader v2.0.0

URLs from any supported service are auto-detected. Metadata source (resolver)
and lyrics source (plugin) are completely independent.

By default, lyrics are fetched from the same service as the track URL first,
then fall back to the configured waterfall if unavailable. Use -source to
override this for a specific run.

Usage:

  python setup_wizard.py                  # first-time setup

  python downloader.py -playing
  python downloader.py -liked

  python downloader.py -track    https://open.spotify.com/track/...
  python downloader.py -track    https://www.deezer.com/track/...
  python downloader.py -track    https://tidal.com/browse/track/...
  python downloader.py -track    https://music.youtube.com/watch?v=...
  python downloader.py -album    <url>
  python downloader.py -playlist <url>
  python downloader.py -artist   <url>

  # Force a specific lyrics source regardless of URL origin
  python downloader.py -album https://www.deezer.com/album/... -source spotify
  python downloader.py -track https://open.spotify.com/track/... -source lrclib

  # Options
  python downloader.py -album <url> -o ~/Music/Lyrics -f -v
  python downloader.py -list-plugins
  python downloader.py -list-resolvers
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from auth import SpotifyAuth
from plugin_loader import initialize_plugins, get_active_plugins, fetch_with_plugins, list_plugins
from plugins.spotify_plugin import SpotifyPlugin
from resolvers.spotify_resolver import SpotifyResolver
from resolver_loader import initialize_resolvers, get_active_resolvers, route, list_resolvers
from utils import build_lrc_path, lrc_exists, save_lrc
from lyrics import _stamp_lyrics_source, TrackInfo, is_valid_album

load_dotenv()


class _TqdmStreamHandler(logging.StreamHandler):
    """
    Logging handler that routes messages through tqdm.write() so that
    log output doesn't break the tqdm progress bar at the bottom of
    the terminal. Falls back to plain write() when tqdm is not available.
    """
    def emit(self, record):
        try:
            msg = self.format(record)
            if HAS_TQDM:
                from tqdm import tqdm as _tqdm
                _tqdm.write(msg, file=sys.stdout)
            else:
                sys.stdout.write(msg + self.terminator)
                self.flush()
        except Exception:
            self.handleError(record)


def setup_logging(verbose: bool):
    """
    Configure logging to both stdout and a log file in logs/.

    If the environment variable LOG_FILE is set (by the PS1/bat launcher),
    all runs in that session append to the same file — one log per session.
    Otherwise each invocation gets its own timestamped file (direct CLI use).
    """
    level = logging.DEBUG if verbose else logging.INFO
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    cache_dir = Path(__file__).parent / ".cache"
    cache_dir.mkdir(exist_ok=True)

    from datetime import datetime
    # Use session log path if launcher set one, otherwise make a fresh file
    session_log = os.environ.get("MLD_SESSION_LOG", "").strip()
    if session_log:
        log_file = Path(session_log)
        log_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = log_dir / f"{timestamp}.log"

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")

    stream_handler = _TqdmStreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8", mode="a")
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


def _interactive_track_select(
    track: "TrackInfo",
    spotify_resolver,
    source_override: str | None,
) -> "TrackInfo | None":
    """
    Show a numbered list of Spotify candidates when YouTube metadata is uncertain.
    The user can:
      - Press Enter / type the number to select a candidate
      - Type 0 to accept the current YouTube-derived metadata as-is
      - Type s to skip (don't download this track)

    Returns the (possibly updated) TrackInfo, or None to skip.
    Only runs when stdin is a real terminal (not piped/batch).
    """
    import sys
    if not sys.stdin.isatty():
        return track  # non-interactive — proceed with what we have

    if spotify_resolver is None:
        return track

    search_fn = getattr(spotify_resolver, "search_track_candidates", None)
    if not search_fn:
        return track

    candidates = search_fn(track.title, track.primary_artist)
    if not candidates:
        return track

    from lyrics import is_valid_album

    print()
    print(f"  ┌─ Uncertain match: {track.primary_artist} — {track.title}")
    print(f"  │  (YouTube metadata was unclear — pick the correct track or accept as-is)")
    print(f"  │")
    print(f"  │   0. ✓ Keep as-is:  {track.primary_artist} / {track.album} — {track.title}")
    for i, item in enumerate(candidates, 1):
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        album   = item.get("album", {}).get("name", "?")
        t_num   = item.get("track_number", "?")
        print(f"  │   {i}. {artists} / {album} (#{t_num}) — {item['name']}")
    print(f"  │   s. Skip this track")
    print(f"  └")

    while True:
        try:
            raw = input("  Pick [0–{n}/s, Enter=0]: ".format(n=len(candidates))).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return track

        if raw in ("", "0"):
            return track
        if raw in ("s", "skip"):
            return None
        try:
            idx = int(raw)
            if 1 <= idx <= len(candidates):
                chosen = candidates[idx - 1]
                artists_str = ", ".join(a["name"] for a in chosen.get("artists", []))
                primary = chosen["artists"][0]["name"] if chosen.get("artists") else track.primary_artist
                album_name = (chosen.get("album", {}).get("name") or "").strip()
                clean_album = album_name if is_valid_album(album_name) else "Unknown Album"
                track.track_id      = chosen["id"]
                track.title         = chosen["name"]
                track.artist        = artists_str
                track.primary_artist = primary
                track.album         = clean_album
                track.track_number  = chosen.get("track_number") or 0
                track.duration_ms   = chosen.get("duration_ms") or track.duration_ms
                track.resolver_confident = True
                logger.info(
                    "  [selected] %s / %s — %s",
                    primary, clean_album, chosen["name"],
                )
                return track
        except ValueError:
            pass
        print(f"  Please enter a number between 0 and {len(candidates)}, or 's' to skip.")


def _find_latest_log() -> "Path | None":
    """Return path to the most recent log file, or None."""
    log_dir = Path("logs")
    if not log_dir.exists():
        return None
    logs = sorted(log_dir.glob("*.log"), reverse=True)
    return logs[0] if logs else None


def _parse_missed_from_log(log_path: "Path") -> list[str]:
    """
    Extract URLs of tracks that were missed in a previous run.
    Reads [MISS] lines and tries to correlate them with the resolver lines above.
    Returns a list of track descriptions like "Artist - Title" for manual retry.
    """
    missed = []
    last_resolver_url = None
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            # Track the URL being resolved
            if "Resolver:" in line and "←" in line:
                last_resolver_url = line.split("←")[-1].strip()
            # Capture miss lines: [MISS]  Artist - Title
            if "[MISS]" in line:
                # Extract artist - title after the [MISS] marker
                parts = line.split("[MISS]", 1)
                if len(parts) > 1:
                    desc = parts[1].strip()
                    if desc:
                        missed.append(desc)
    return missed


def _handle_retry(args) -> tuple[str, str]:
    """
    Find the last log, show missed tracks, ask for confirmation, then re-run.
    Returns ("retry_batch", "") as a signal — the actual retry is handled inline.
    """
    log_path = _find_latest_log()
    if not log_path:
        logger.error("No log files found in logs/. Run a download first.")
        return ("", "")

    missed = _parse_missed_from_log(log_path)
    if not missed:
        logger.info("No missed tracks found in last log: %s", log_path.name)
        return ("", "")

    print(f"\n  Last run: {log_path.name}")
    print(f"  Found {len(missed)} missed track(s):\n")
    for i, desc in enumerate(missed, 1):
        print(f"    {i:2d}. {desc}")

    print()
    ans = input("  Retry all missed tracks? [Y/n]: ").strip().lower()
    if ans in ("n", "no"):
        return ("", "")

    # Re-run downloader for each missed track via Spotify search
    # We store the missed list on args for the main loop to pick up
    args._retry_missed = missed
    return ("retry_batch", "")


def _enrich_track_from_lrclib(track) -> None:
    """
    Query LRCLIB for album and track_number metadata.
    Called when album is "Unknown Album" or track_number is 0 (e.g. YouTube tracks).
    Mutates track in place. Silent on failure.
    """
    from utils import enrich_from_lrclib
    enriched = enrich_from_lrclib(track.primary_artist, track.title)
    album = enriched.get("album", "")
    track_num = enriched.get("track_number") or 0
    if album and track.album in ("Unknown Album", ""):
        track.album = album
    if track_num and track.track_number == 0:
        track.track_number = track_num


def _enrich_track_from_spotify(track, spotify_resolver) -> bool:
    """
    Query Spotify for album + track_number metadata only (no lyrics fetch).
    Called when album is still Unknown after LRCLIB enrichment fails.
    Returns True if any metadata was updated.
    """
    if spotify_resolver is None:
        return False
    try:
        search_fn = getattr(spotify_resolver, 'search_track', None)
        if not search_fn:
            return False
        item = search_fn(track.title, track.primary_artist)
        if not item:
            return False
        from lyrics import is_valid_album

        # Update primary_artist from Spotify's album_artists — more reliable than
        # splitting a comma-separated string (handles "Tyler, The Creator" correctly)
        album_obj = item.get("album", {})
        album_artists = album_obj.get("artists", []) if isinstance(album_obj, dict) else []
        track_artists = item.get("artists", [])
        new_primary = (
            album_artists[0]["name"] if album_artists
            else (track_artists[0]["name"] if track_artists else "")
        )
        if new_primary:
            track.primary_artist = new_primary
            # Keep full artist list in the [ar:] tag
            if track_artists:
                track.artist = ", ".join(a["name"] for a in track_artists)

        album_name = (album_obj.get("name", "") if isinstance(album_obj, dict) else "").strip()
        track_num  = item.get("track_number") or 0
        updated = False

        # Only accept the enriched album if it's different from the artist name
        # (guards against the Limp Bizkit self-titled false-positive)
        if album_name and is_valid_album(album_name, track.primary_artist):
            if album_name.lower().strip() != track.primary_artist.lower().strip():
                if track.album in ("Unknown Album", "") or not is_valid_album(track.album):
                    track.album = album_name
                    updated = True
                elif track.album.lower().strip() == track.primary_artist.lower().strip():
                    # Current album IS the artist name — always replace it
                    track.album = album_name
                    updated = True

        if track_num and track.track_number == 0:
            track.track_number = track_num
            updated = True
        return updated
    except Exception:
        return False


# Known LRC header tag prefixes — used to distinguish header lines from timestamp lines.
# Timestamp lines like [00:12.34] must NOT be treated as header lines.
_LRC_HEADER_TAGS = ("[ti:", "[ar:", "[al:", "[length:", "[#:", "[re:", "[by:", "[offset:")


def _rebuild_lrc_header(lrc_content: str, track) -> str:
    """Replace the header block in an LRC string with a fresh one from current track state."""
    import re as _re
    from lyrics import _lrc_header
    lines = lrc_content.splitlines(keepends=True)
    # Find header end by matching known tag prefixes only — avoids confusing
    # timestamp lines like [00:12.34] with header tags.
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(tag) for tag in _LRC_HEADER_TAGS):
            header_end = i + 1
        elif not stripped and header_end > 0:
            header_end = i + 1
            break
        else:
            break
    new_header = _lrc_header(track)
    src_match = _re.search(r"\[re:([^\]]+)\]", lrc_content)
    if src_match:
        src = src_match.group(1)
        new_header = _re.sub(r"\[re:[^\]]*\]", f"[re:{src}]", new_header)
    return new_header + "".join(lines[header_end:])


def _needs_enrich(t) -> bool:
    if t.album in ("Unknown Album", ""):
        return True
    if t.track_number == 0:
        return True
    if t.album.lower().strip() == t.primary_artist.lower().strip():
        return True
    return False


def _print_status_check(active_resolvers, active_plugins, spotify_auth):
    """
    Print a one-line status table for each resolver and plugin.
    Called by the launcher before showing the main menu.
    """
    from plugins.spotify_plugin import SpotifyPlugin
    from plugins.deezer_plugin import DeezerPlugin

    def _check(name, enabled, detail=""):
        mark = "✓" if enabled else "✗"
        line = f"  {mark}  {name}"
        if detail:
            line += f"  ({detail})"
        print(line)

    print()
    print("  Resolvers")
    for r in active_resolvers:
        _check(r.NAME, True)

    print()
    print("  Lyrics sources")
    for p in active_plugins:
        if isinstance(p, SpotifyPlugin):
            token_ok = bool(spotify_auth and spotify_auth.session_headers)
            _check(p.NAME, token_ok, "token valid" if token_ok else "no token — will prompt")
        elif isinstance(p, DeezerPlugin):
            _check(p.NAME, p.enabled, "signed in" if p.enabled else "no ARL")
        else:
            _check(p.NAME, p.enabled)
    print()


def _verify_spotify_token(active_plugins, spotify_auth):
    """
    Quick startup check that the Spotify Bearer token is valid.
    Uses the color-lyrics endpoint with a known track ID.
    Shows a clear warning if expired so the user knows before a long batch run.
    """
    sp = next((p for p in active_plugins if isinstance(p, SpotifyPlugin) and p.enabled), None)
    if not sp or not sp._auth_headers:
        return
    try:
        import requests as _req
        # Rick Astley — Never Gonna Give You Up (reliable test track)
        test_id = "4PTG3Z6ehGkBFwjybzWkR8"
        url = (
            f"https://spclient.wg.spotify.com/color-lyrics/v2/track/{test_id}"
            "?format=json&vocalRemoval=false&market=from_token"
        )
        resp = _req.get(url, headers=sp._auth_headers, timeout=6)
        if resp.status_code == 200:
            logger.info("Spotify token: ✓ valid")
        elif resp.status_code == 401:
            logger.warning(
                "Spotify token: ✗ EXPIRED — Spotify lyrics will fail this run. "
                "Update SPOTIFY_AUTH_TOKEN in .env or delete .cache/spotify_token.json "
                "to be prompted for a new token."
            )
            # Trigger the re-auth prompt now rather than failing mid-batch
            spotify_auth.notify_401()
            sp._auth_headers = spotify_auth.session_headers
        elif resp.status_code == 403:
            logger.warning("Spotify token: ✗ 403 Forbidden — token may be invalid or revoked.")
    except Exception as e:
        logger.debug("Spotify token check failed: %s", e)


def main():
    parser = argparse.ArgumentParser(
        description="Download synced lyrics — URL source auto-detected",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        prefix_chars="-",
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("-playing",        action="store_true",
                     help="Currently playing Spotify track (requires OAuth setup)")
    src.add_argument("-retry",          action="store_true",
                     help="Re-run only tracks that failed in the last run (reads log)")
    src.add_argument("-track",          metavar="URL",
                     help="Track URL — Spotify/Deezer/Tidal/YouTube Music auto-detected")
    src.add_argument("-playlist",       metavar="URL", help="Playlist URL — auto-detected")
    src.add_argument("-album",          metavar="URL", help="Album URL — auto-detected")
    src.add_argument("-artist",         metavar="URL", help="Artist URL — auto-detected")
    src.add_argument("-check",          action="store_true", dest="check",
                     help="Print resolver/plugin status and exit (used by launcher)")
    src.add_argument("-list-plugins",   action="store_true", dest="list_plugins",
                     help="Show lyrics plugins and exit")
    src.add_argument("-list-resolvers", action="store_true", dest="list_resolvers",
                     help="Show metadata resolvers and exit")

    parser.add_argument(
        "-source", metavar="NAME",
        help=(
            "Force a specific lyrics source for this run "
            "(e.g. -source spotify, -source deezer, -source lrclib, -source youtube). "
            "Overrides the native-first and PLUGIN_ORDER behaviour."
        ),
    )
    parser.add_argument("-o",      metavar="DIR", default="Lyrics", dest="output",
                        help="Output directory (default: ./Lyrics)")
    parser.add_argument("-f",      action="store_true", dest="force",
                        help="Re-download even if .lrc already exists")
    parser.add_argument("-delay",  type=float, default=0.5, metavar="SECONDS",
                        help="Delay between requests (default: 0.5s)")
    parser.add_argument("-v",      action="store_true", dest="verbose",
                        help="Verbose/debug logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    env = dict(os.environ)

    # -- Initialize resolvers and plugins -------------------------------------
    logger.info("Initializing metadata resolvers...")
    all_resolvers  = initialize_resolvers(env)
    active_resolvers = get_active_resolvers(all_resolvers)

    logger.info("Initializing lyrics plugins...")
    all_plugins    = initialize_plugins(env)
    active_plugins = get_active_plugins(all_plugins)

    # Inject Spotify lyrics auth headers
    spotify_auth = SpotifyAuth(
        sp_dc=os.getenv("SPOTIFY_SP_DC"),
        manual_token=os.getenv("SPOTIFY_AUTH_TOKEN"),
    )
    # Find the Spotify resolver to wire its search function into the Spotify plugin.
    # This lets the Spotify lyrics plugin look up track IDs for non-Spotify tracks
    # when the user explicitly requests -source spotify.
    spotify_resolver_instance = next(
        (r for r in active_resolvers if isinstance(r, SpotifyResolver)),
        None,
    )

    for plugin in active_plugins:
        if isinstance(plugin, SpotifyPlugin):
            # Pass both the headers dict AND the auth object so the plugin can
            # call notify_401() to re-prompt mid-run if the token expires.
            plugin.inject_headers(spotify_auth.session_headers, auth=spotify_auth)
            if spotify_resolver_instance:
                plugin.inject_search_fn(spotify_resolver_instance.search_track_id)

    # -- Info flags -----------------------------------------------------------
    if args.list_plugins:
        list_plugins(all_plugins)
        sys.exit(0)

    if args.list_resolvers:
        list_resolvers(all_resolvers)
        sys.exit(0)

    if getattr(args, "check", False):
        _print_status_check(active_resolvers, active_plugins, spotify_auth)
        sys.exit(0)

    if not active_resolvers:
        logger.error(
            "No metadata resolvers enabled.\n"
            "  Run: python setup_wizard.py\n"
            "  Or check warnings above for missing optional dependencies."
        )
        sys.exit(1)

    if not active_plugins:
        logger.error(
            "No lyrics plugins enabled.\n"
            "  Run: python setup_wizard.py\n"
            "  Or check warnings above for missing optional dependencies."
        )
        sys.exit(1)

    logger.info(
        "Resolvers : %s",
        ", ".join(r.NAME for r in active_resolvers),
    )
    logger.info(
        "Lyrics    : %s",
        " -> ".join(p.NAME for p in active_plugins),
    )

    # Verify Spotify token is valid before starting downloads
    _verify_spotify_token(active_plugins, spotify_auth)

    # -- Determine kind and URL -----------------------------------------------
    if getattr(args, "retry", False):
        kind, url = _handle_retry(args)
        if not url and kind != "retry_batch":
            sys.exit(0)
    elif args.playing:
        kind, url = "playing", ""
    elif args.track:
        kind, url = "track", args.track
    elif args.album:
        kind, url = "album", args.album
    elif args.playlist:
        kind, url = "playlist", args.playlist
    elif args.artist:
        kind, url = "artist", args.artist

    # Strip tracking/session params that don't affect resolution but cause
    # bash to split URLs when unquoted (& forks to background in bash)
    # Spotify: ?si=xxx  YouTube: &list=xxx &start_radio=xxx &index=xxx
    if url:
        import re as _re
        # Remove ?si= and &si= Spotify share tokens
        url = _re.sub(r"[?&]si=[A-Za-z0-9]+", "", url)
        # Remove YouTube playlist/radio params (keep ?v= intact)
        url = _re.sub(r"&(?:list|start_radio|index|pp)=[^&]*", "", url)
        # Clean up trailing ? or &
        url = url.rstrip("?&").rstrip("~")
    else:
        parser.print_help()
        sys.exit(1)

    # -- Resolve tracks -------------------------------------------------------
    if kind == "retry_batch":
        # Retry mode: search Spotify for each missed "Artist - Title"
        missed = getattr(args, "_retry_missed", [])
        tracks = []
        for desc in missed:
            # desc is like "Artist  Title" or "Artist - Title"
            # Try splitting on double-space (log format) then on " - "
            if "  " in desc:
                parts = desc.split("  ", 1)
                artist, title = parts[0].strip(), parts[1].strip()
            elif " - " in desc:
                artist, title = desc.split(" - ", 1)
            else:
                artist, title = "", desc
            from lyrics import is_valid_album as _iva_retry
            sr = next((r for r in active_resolvers if isinstance(r, SpotifyResolver)), None)
            if sr:
                item = sr.search_track(title.strip(), artist.strip())
                if item:
                    album_name = (item.get("album", {}).get("name") or "").strip()
                    clean_album = album_name if is_valid_album(album_name) else "Unknown Album"
                    primary = item["artists"][0]["name"] if item.get("artists") else artist
                    track = TrackInfo(
                        track_id=item["id"],
                        title=item["name"],
                        artist=", ".join(a["name"] for a in item.get("artists", [])),
                        primary_artist=primary,
                        album=clean_album,
                        duration_ms=item.get("duration_ms", 0),
                        track_number=item.get("track_number", 0),
                        disc_number=item.get("disc_number", 1),
                        resolver_name="Spotify",
                    )
                    tracks.append(track)
                else:
                    logger.warning("Retry: could not find '%s' on Spotify", desc)
            else:
                logger.error("Retry requires Spotify resolver to be enabled.")
                break
        native_source = "Spotify"
        if not tracks:
            logger.info("No tracks resolved for retry.")
            sys.exit(0)
    else:
        track_gen, native_source = route(active_resolvers, url, kind)
        tracks = list(track_gen)
        if not tracks:
            logger.error("No tracks found.")
            sys.exit(1)

    # If -source is specified, ignore native-first behaviour
    source_override = args.source or None
    if source_override:
        logger.info("Lyrics source locked to: %s (from -source flag)", source_override)
    elif native_source:
        logger.info(
            "Native lyrics source: %s will be tried first for these tracks",
            native_source,
        )

    logger.info("Found %d track(s). Starting download...", len(tracks))
    output_dir = Path(args.output).expanduser()

    # -- Download loop --------------------------------------------------------
    stats = {"ok": 0, "skipped": 0, "user_skipped": 0, "missing": 0, "error": 0}
    plugin_stats: dict[str, dict] = {}    # per-plugin hit/miss/err counts
    resolver_counts: dict[str, int] = {}  # metadata resolver counts
    iterator = tqdm(tracks, unit="track") if HAS_TQDM and len(tracks) > 1 else tracks

    for track in iterator:
        # Track which resolver provided this track (stored on TrackInfo if available)
        r_name = getattr(track, "resolver_name", None) or native_source or "unknown"
        resolver_counts[r_name] = resolver_counts.get(r_name, 0) + 1

        # -- Interactive selection for uncertain YouTube metadata ---------------
        if (not getattr(track, "resolver_confident", True)
                and not source_override
                and kind not in ("playlist", "album", "artist", "retry_batch")):
            result = _interactive_track_select(
                track, spotify_resolver_instance, source_override
            )
            if result is None:
                logger.info("[SKIP] User skipped: %s - %s",
                            track.primary_artist, track.title)
                stats["user_skipped"] += 1
                continue
            track = result

        lrc_path = build_lrc_path(output_dir, track)

        if HAS_TQDM and hasattr(iterator, "set_description"):
            iterator.set_description(f"{track.primary_artist[:20]} -- {track.title[:25]}")

        if not args.force and lrc_exists(lrc_path):
            logger.debug("[SKIP] %s", lrc_path)
            stats["skipped"] += 1
            continue

        lrc_content, source_name = None, None
        for attempt in range(3):
            try:
                lrc_content, source_name = fetch_with_plugins(
                    active_plugins,
                    track,
                    native_first=native_source,
                    source_override=source_override,
                    plugin_stats=plugin_stats,
                )
                break
            except Exception as e:
                logger.warning("Attempt %d/3 failed for '%s': %s", attempt + 1, track.title, e)
                time.sleep(2 ** attempt)

        if lrc_content is None:
            logger.info("[MISS]  %s - %s", track.primary_artist, track.title)
            stats["missing"] += 1
        else:
            # Stamp the actual lyrics source name into the [re:] header tag
            if source_name:
                lrc_content = _stamp_lyrics_source(lrc_content, source_name)
            # Emit a clear notice when we had to fall back to a different source
            clean_source = source_name.split("(")[0].strip() if source_name else ""
            if (native_source and clean_source
                    and clean_source.lower() != native_source.lower()
                    and not source_override):
                logger.info(
                    "  ℹ  Synced lyrics not available via %s — "
                    "using %s as fallback source",
                    native_source, clean_source,
                )
            # If album/track_number are still unknown, try LRCLIB for metadata.
            # This lets YouTube-sourced tracks get proper folder organization
            # even when Spotify isn't available or wasn't tried.
            # Enrich when album is unknown, missing, or suspiciously equals the
            # artist name (yt-dlp sometimes uses artist name as album for official uploads)
            if _needs_enrich(track):
                _enrich_track_from_lrclib(track)
            # If still needs enrichment after LRCLIB, try Spotify metadata search
            if _needs_enrich(track):
                if _enrich_track_from_spotify(track, spotify_resolver_instance):
                    logger.debug(
                        "Metadata enriched via Spotify for '%s' → %s / %s #%s",
                        track.title, track.primary_artist, track.album, track.track_number,
                    )
            # Re-stamp header with any enriched metadata
            if source_name:
                lrc_content = _rebuild_lrc_header(lrc_content, track)
            # Rebuild the path — album/track_number may have been enriched
            # by the Spotify plugin's cross-service lookup or LRCLIB above.
            final_path = build_lrc_path(output_dir, track)
            # If the path changed and the old (unenriched) file exists, remove it
            if final_path != lrc_path and lrc_exists(lrc_path):
                try:
                    lrc_path.unlink()
                except OSError:
                    pass
            if save_lrc(final_path, lrc_content):
                clean_src = source_name.split("(")[0].strip() if source_name else ""
                if clean_src == "Spotify" and track.track_id and len(track.track_id) == 22:
                    logger.info("[OK][%s]  %s  (id:%s)",
                                source_name, final_path.relative_to(output_dir), track.track_id)
                else:
                    logger.info("[OK][%s]  %s", source_name, final_path.relative_to(output_dir))
                stats["ok"] += 1
            else:
                stats["error"] += 1

        time.sleep(args.delay)

    # -- Summary --------------------------------------------------------------
    # Build per-plugin lines showing hit / miss / err for every active plugin
    plugin_lines = ""
    for p in active_plugins:
        s = plugin_stats.get(p.NAME, {"hit": 0, "miss": 0, "err": 0})
        total = s["hit"] + s["miss"] + s["err"]
        if total == 0:
            # Plugin was active but no tracks reached it (all skipped or hit earlier)
            note = "not tried"
        elif s["hit"] > 0 and s["miss"] == 0 and s["err"] == 0:
            note = f"{s['hit']} downloaded"
        else:
            parts = []
            if s["hit"]:
                parts.append(f"{s['hit']} downloaded")
            if s["miss"]:
                parts.append(f"{s['miss']} no lyrics")
            if s["err"]:
                parts.append(f"{s['err']} error")
            note = " / ".join(parts)
        plugin_lines += f"    {p.NAME:<14} {note}\n"

    resolver_lines = "".join(
        f"    {name:<14} {count} track(s)\n"
        for name, count in sorted(resolver_counts.items(), key=lambda x: -x[1])
    )

    logger.info(
        "\n-- Done ----------------------------------\n"
        "  Sources resolved from:\n"
        "%s"
        "  Lyrics plugin results:\n"
        "%s"
        "  + Downloaded : %d\n"
        "  > Skipped    : %d  (already existed)\n"
        "%s"
        "  - Not found  : %d  (exhausted all sources)\n"
        "  ! Errors     : %d\n"
        "------------------------------------------",
        resolver_lines, plugin_lines,
        stats["ok"], stats["skipped"],
        f"  / Skipped    : {stats['user_skipped']}  (user skipped)\n" if stats["user_skipped"] else "",
        stats["missing"], stats["error"],
    )

    # Write session summary for the launcher (PS1/bash) to display on exit
    try:
        import json as _json
        _summary_path = Path(__file__).parent / ".cache" / "session_summary.json"
        _summary_path.parent.mkdir(exist_ok=True)
        _existing = {}
        if _summary_path.exists():
            try:
                _existing = _json.loads(_summary_path.read_text())
            except Exception:
                pass
        _summary = {
            "downloaded": _existing.get("downloaded", 0) + stats["ok"],
            "skipped":    _existing.get("skipped", 0)    + stats["skipped"],
            "missing":    _existing.get("missing", 0)    + stats["missing"],
            "errors":     _existing.get("errors", 0)     + stats["error"],
        }
        _summary_path.write_text(_json.dumps(_summary))
    except Exception:
        pass

    # Flush all log handlers and stdio before exit.
    # yt-dlp and requests leave non-daemon threads that block normal Python
    # shutdown. os._exit(0) bypasses that, but we must flush first or the
    # summary output may not appear (especially in Git Bash / mintty).
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
