#!/usr/bin/env python3
"""
setup_wizard.py -- Interactive config for multiplatform-lyric-downloader (v2)

Run this first:
  python setup_wizard.py

Architecture recap:
  RESOLVERS  = where your track list comes from (Spotify, Deezer, Tidal, YouTube Music...)
  PLUGINS    = where lyrics come from (Spotify, Deezer, LRCLIB, YouTube...)

These are totally independent. A Tidal album can get Spotify lyrics.
A Deezer track can fall back to LRCLIB. Whichever combination you configure,
every download tries each lyrics source in priority order until one succeeds.
"""

import sys, io
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os
from pathlib import Path

ENV_FILE = Path(".env")

def _is_fancy_terminal() -> bool:
    # MSYSTEM check removed -- questionary works fine in Git Bash once
    # stdout is forced to utf-8 (done at module top).
    if not sys.stdin.isatty():
        return False
    if os.environ.get("TERM", "") in ("dumb",):
        return False
    return True

try:
    import questionary
    from questionary import Style
    HAS_QUESTIONARY = _is_fancy_terminal()
    STYLE = Style([
        ("qmark",       "fg:#00b4d8 bold"),
        ("question",    "bold"),
        ("answer",      "fg:#90e0ef bold"),
        ("pointer",     "fg:#00b4d8 bold"),
        ("highlighted", "fg:#00b4d8 bold"),
        ("selected",    "fg:#90e0ef"),
        ("separator",   "fg:#555555"),
        ("instruction", "fg:#555555"),
    ])
except ImportError:
    HAS_QUESTIONARY = False
    STYLE = None


# -- Registry ------------------------------------------------------------------
#
# RESOLVERS: provide track metadata (title, artist, album) from a streaming URL.
# LYRICS_PLUGINS: search for lyrics using that metadata, in priority order.
#
# A resolver and a lyrics plugin for the same service are independent.
# You can use Tidal as a resolver with Spotify + LRCLIB as lyrics sources.

RESOLVERS = [
    {
        "name": "Spotify",
        "description": "Spotify URLs. Requires a free developer app for API keys.",
        "install": None,
        "fields": [
            {"key": "SPOTIFY_CLIENT_ID",     "label": "Client ID",
             "description": "From https://developer.spotify.com/dashboard", "secret": False},
            {"key": "SPOTIFY_CLIENT_SECRET", "label": "Client Secret",
             "description": "From https://developer.spotify.com/dashboard", "secret": True},
            {"key": "SPOTIFY_REDIRECT_URI",  "label": "Redirect URI",
             "description": "Your machine's LAN IP — e.g. http://192.168.1.x:8888/callback\nMust match EXACTLY what you add to developer.spotify.com → Your App → Edit Settings → Redirect URIs\nSpotify no longer accepts localhost — use your LAN IP (auto-detected)",
             "secret": False, "default": ""},
        ],
    },
    {
        "name": "Deezer",
        "description": "Deezer URLs. Public content needs no credentials. ARL unlocks private playlists.",
        "install": None,
        "fields": [
            {"key": "DEEZER_ARL", "label": "ARL Cookie (optional)",
             "description": "deezer.com DevTools -> Application -> Cookies -> 'arl'",
             "secret": True, "required": False},
        ],
    },
    {
        "name": "Tidal",
        "description": "Tidal URLs. Uses browser OAuth -- opens a login link once, then caches the session.",
        "install": "pip install tidalapi",
        "fields": [],  # OAuth flow handles auth -- no manual token needed
    },
    {
        "name": "YouTube Music",
        "description": "music.youtube.com URLs. Works unauthenticated for public content.",
        "install": "pip install ytmusicapi",
        "fields": [
            {"key": "YTMUSIC_AUTH_HEADERS", "label": "Auth headers file (optional)",
             "description": "Run 'ytmusicapi browser' to generate. Leave blank for public access only.",
             "secret": False, "required": False},
        ],
    },
]

LYRICS_PLUGINS = [
    {
        "name": "Spotify",
        "description": "Official synced lyrics. Best timing accuracy. Requires a Bearer token (~1hr).",
        "install": None,
        "fields": [
            {"key": "SPOTIFY_AUTH_TOKEN", "label": "Bearer Token",
             "description": (
                 "DevTools (F12) -> Network -> filter 'color-lyrics'\n"
                 "  -> click request -> Headers -> copy 'authorization'\n"
                 "  You can paste the full 'Bearer BQAxx...' string -- the prefix is stripped automatically."
             ), "secret": True, "required": True},
        ],
    },
    {
        "name": "Deezer",
        "description": "Deezer lyrics. Excellent non-English coverage. Requires ARL cookie.",
        "install": None,
        "fields": [
            {"key": "DEEZER_ARL", "label": "ARL Cookie",
             "description": "deezer.com DevTools -> Application -> Cookies -> 'arl'",
             "secret": True},
        ],
    },
    {
        "name": "LRCLIB",
        "description": "Free community lyrics database. No credentials needed. Great for older/obscure tracks.",
        "install": None,
        "fields": [],
    },
    {
        "name": "YouTube",
        "description": "Auto-generated captions from YouTube. Broadest possible coverage. No credentials needed.",
        "install": "pip install yt-dlp",
        "fields": [],
    },
]

RESOLVER_NAMES     = [r["name"] for r in RESOLVERS]
LYRICS_PLUGIN_NAMES = [p["name"] for p in LYRICS_PLUGINS]


# -- .env helpers --------------------------------------------------------------

def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


_ENV_TEMPLATE = """# ============================================================
# multiplatform-lyric-downloader configuration
# Generated by setup_wizard.py — safe to edit manually
# ============================================================

# ------------------------------------------------------------
# Spotify API Keys (resolver — from developer.spotify.com)
# ------------------------------------------------------------
SPOTIFY_CLIENT_ID={SPOTIFY_CLIENT_ID}
SPOTIFY_CLIENT_SECRET={SPOTIFY_CLIENT_SECRET}
SPOTIFY_REDIRECT_URI={SPOTIFY_REDIRECT_URI}

# ------------------------------------------------------------
# Spotify Lyrics Token (plugin — from DevTools color-lyrics)
# Paste full "Bearer BQA..." or just the token, prefix is stripped
# Lasts ~1 hour. Leave blank to be prompted on each run.
# ------------------------------------------------------------
SPOTIFY_AUTH_TOKEN={SPOTIFY_AUTH_TOKEN}

# ------------------------------------------------------------
# Deezer ARL Cookie (resolver + lyrics plugin — lasts ~3 months)
# From deezer.com DevTools -> Application -> Cookies -> arl
# ------------------------------------------------------------
DEEZER_ARL={DEEZER_ARL}

# ------------------------------------------------------------
# YouTube Music resolver — path to ytmusicapi headers file
# Run: ytmusicapi browser    to generate. Leave blank for public only.
# ------------------------------------------------------------
YTMUSIC_AUTH_HEADERS={YTMUSIC_AUTH_HEADERS}

# ------------------------------------------------------------
# Source priority (written by setup_wizard.py)
# Edit manually to reorder without re-running the wizard
# ------------------------------------------------------------
RESOLVER_ORDER={RESOLVER_ORDER}
PLUGIN_ORDER={PLUGIN_ORDER}
"""

_ENV_KEYS = [
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI",
    "SPOTIFY_AUTH_TOKEN", "DEEZER_ARL", "YTMUSIC_AUTH_HEADERS",
    "RESOLVER_ORDER", "PLUGIN_ORDER",
]


def save_env(env: dict):
    """Write .env with comments and sections. Unknown keys appended at bottom."""
    known = {k: env.get(k, "") for k in _ENV_KEYS}
    content = _ENV_TEMPLATE.format(**known)

    # Append any extra keys not in the template (e.g. TIDAL_CLIENT_ID)
    extra = {k: v for k, v in env.items() if k not in _ENV_KEYS and not k.startswith("_")}
    if extra:
        content += "\n# Additional settings\n"
        for k, v in extra.items():
            content += f"{k}={v}\n"

    ENV_FILE.write_text(content, encoding="utf-8")


def mask(value: str, secret: bool) -> str:
    if not value:
        return "(not set)"
    if not secret:
        return value
    return value[:4] + "*" * max(0, len(value) - 8) + value[-4:] if len(value) > 8 else "*" * len(value)


# -- UI primitives -------------------------------------------------------------

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def header(title: str, step: str = ""):
    print()
    print("=" * 64)
    if step:
        print(f"  {step}")
    print(f"  {title}")
    print("=" * 64)
    print()

def ask_select(question: str, choices: list[str]) -> str | None:
    if HAS_QUESTIONARY:
        return questionary.select(question, choices=choices, style=STYLE).ask()
    print(f"  {question}")
    for i, c in enumerate(choices, 1):
        print(f"    {i}. {c}")
    print()
    while True:
        try:
            raw = input(f"  Choose [1-{len(choices)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except (ValueError, KeyboardInterrupt):
            return None

def ask_confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    if HAS_QUESTIONARY:
        result = questionary.confirm(question, default=default, style=STYLE).ask()
        return result if result is not None else default
    while True:
        raw = input(f"  {question} {hint}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def _extract_name(choice: str, name_list: list[str]) -> str:
    """Extract the service name from a display string like 'YouTube Music  description...'
    by matching against the known list of names (handles multi-word names correctly)."""
    choice_stripped = choice.strip()
    # Try longest names first to avoid "YouTube" matching before "YouTube Music"
    for name in sorted(name_list, key=len, reverse=True):
        if choice_stripped.startswith(name):
            return name
    # Fallback: first word (shouldn't reach here)
    return choice_stripped.split()[0]


def prompt_field(field: dict, current: str) -> str:
    label    = field["label"]
    desc     = field["description"]
    secret   = field["secret"]
    default  = field.get("default", "")
    required = field.get("required", False)

    # For redirect URI: auto-detect local IP and suggest it
    if field.get("key") == "SPOTIFY_REDIRECT_URI" and not current:
        detected_ip = _detect_local_ip()
        if detected_ip:
            default = f"http://{detected_ip}:8888/callback"
        else:
            default = ""

    print(f"  {label}")
    for line in desc.splitlines():
        print(f"    {line}")
    if field.get("key") == "SPOTIFY_REDIRECT_URI":
        print()
        print("  ⚠  Spotify no longer accepts 'localhost' as a redirect URI.")
        print("     Use your machine's LAN IP address (auto-detected below),")
        print("     and add the EXACT same URL to your Spotify app settings at")
        print("     https://developer.spotify.com/dashboard → Edit Settings → Redirect URIs")
    if default and not current:
        print(f"  Suggested: {default}")
    print()

    while True:
        print(f"  Current: {mask(current, secret)}")
        if HAS_QUESTIONARY:
            new_val = (
                questionary.password("  New value (Enter to keep):", style=STYLE).ask()
                if secret else
                questionary.text("  New value (Enter to keep):", default=current or default, style=STYLE).ask()
            )
            if new_val is None:
                new_val = ""
        else:
            if secret:
                print("  (input visible -- paste and press Enter)")
            new_val = input(f"  New value (Enter to keep): ").strip()

        # Strip Bearer prefix if present
        if new_val and new_val.lower().startswith("bearer "):
            new_val = new_val[7:].strip()
            print("  (Stripped 'Bearer ' prefix automatically)")

        result = new_val if new_val else (current or default)

        if required and not result:
            print(f"  This field is required -- please enter a value.")
            print()
            continue

        return result


# -- Steps ---------------------------------------------------------------------

def step_intro():
    clear()
    header("multiplatform-lyric-downloader -- Setup Wizard")
    print("  This tool downloads synced .lrc lyrics for your music library.")
    print()
    print("  HOW IT WORKS")
    print("  ------------")
    print("  1. A RESOLVER reads track info from a streaming URL")
    print("     (Spotify, Deezer, Tidal, YouTube Music, etc.)")
    print()
    print("  2. LYRICS PLUGINS search for lyrics in priority order")
    print("     (Spotify -> Deezer -> LRCLIB -> YouTube...)")
    print()
    print("  These are independent -- a Tidal album can get Spotify lyrics.")
    print("  A Deezer URL falls back to LRCLIB if Spotify has nothing.")
    print()
    input("  Press Enter to continue...")


def _pick_from_list(title: str, items: list, prompt_done: str = "Done") -> list:
    """
    Stable-list multi-picker.
    All items shown every loop with fixed numbers. Selected = [x] + order #.
    Enter the number to toggle. 0 or blank = done.
    """
    chosen = []

    while True:
        print()
        print(f"  {title}")
        print(f"  {'#':<4} {'':3} {'Service':<20} Description")
        print("  " + "-" * 62)
        for i, item in enumerate(items, 1):
            name = item["name"]
            desc = item["description"]
            inst = f"  [install: {item['install']}]" if item.get("install") else ""
            order = f"#{chosen.index(name)+1}" if name in chosen else ""
            mark  = "[x]" if name in chosen else "[ ]"
            print(f"  {i:<4} {order:<3} {mark} {name:<18} {desc}{inst}")
        print("  " + "-" * 62)
        if chosen:
            print(f"  Current order: {' -> '.join(chosen)}")
        print(f"  0 / Enter  {prompt_done}")
        print()

        raw = input("  Toggle number (0 to finish): ").strip().strip("\r")

        if raw in ("", "0", "q", "done"):
            if not chosen:
                print("  Select at least one option first.")
                continue
            break

        try:
            idx = int(raw) - 1
        except ValueError:
            print(f"  Invalid '{raw}' -- enter a number 1-{len(items)}, or 0 to finish.")
            continue

        if not (0 <= idx < len(items)):
            print(f"  Out of range -- pick 1-{len(items)}.")
            continue

        name = items[idx]["name"]
        if name in chosen:
            chosen.remove(name)
            print(f"  Removed: {name}")
        else:
            chosen.append(name)
            print(f"  Added at #{len(chosen)}: {name}")

    return chosen


def step_choose_resolvers(env: dict) -> list:
    """Choose which streaming services to pull track lists from."""
    clear()
    header("Step 1 -- Metadata Sources (Resolvers)", "Where do your track lists come from?")
    print("  Toggle each service on/off with its number.")
    print("  First selected = primary source, rest = fallbacks in order.")
    return _pick_from_list("Metadata resolvers:", RESOLVERS, "Done -- continue to lyrics sources")


def step_choose_lyrics_plugins(env: dict) -> list:
    """Choose which lyrics sources to use and in what order."""
    clear()
    header("Step 2 -- Lyrics Sources (Plugins)", "Where do lyrics come from?")
    print("  Toggle each source on/off. First selected = tried first.")
    print("  TIP: Spotify and Deezer are official. LRCLIB/YouTube are community fallbacks.")
    return _pick_from_list("Lyrics sources:", LYRICS_PLUGINS, "Done -- continue to credentials")



# ── Credential verification ───────────────────────────────────────────────────

def _verify_spotify_lyrics_token(token: str) -> tuple[bool, str]:
    """Test the Bearer token against a known track (Never Gonna Give You Up)."""
    import requests
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    # Match the exact headers the downloader sends (User-Agent is required)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Authorization": f"Bearer {token}",
        "App-Platform": "WebPlayer",
        "Spotify-App-Version": "1.2.46.25.g21775a4b",
        "Referer": "https://open.spotify.com/",
        "Origin": "https://open.spotify.com",
    }
    # Rick Astley - Never Gonna Give You Up (well-known, always has synced lyrics)
    test_url = (
        "https://spclient.wg.spotify.com/color-lyrics/v2/track/"
        "4uLU6hMCjMI75M1A2tKUQC"
        "?format=json&vocalRemoval=false&market=from_token"
    )
    try:
        resp = requests.get(test_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            lines = data.get("lyrics", {}).get("lines", [])
            line_count = len(lines)
            return True, f"Token valid -- got {line_count} lyric lines for test track."
        elif resp.status_code == 401:
            return False, "401 Unauthorized -- token is wrong or expired."
        elif resp.status_code == 403:
            return False, (
                "403 Forbidden -- token was rejected. "
                "Make sure you copied the token from a color-lyrics network request "
                "while the Spotify web player was open and active."
            )
        else:
            return False, f"Unexpected status {resp.status_code}."
    except Exception as e:
        return False, f"Request failed: {e}"


def _detect_local_ip() -> str:
    """Detect the machine's LAN IP for use as Spotify redirect URI."""
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _verify_spotify_api(client_id: str, client_secret: str, redirect_uri: str) -> tuple[bool, str]:
    """Test Spotify API credentials with a client credentials flow."""
    import requests, base64
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {creds}"},
            timeout=8,
        )
        if resp.status_code == 200:
            return True, "Client credentials valid."
        elif resp.status_code == 400:
            return False, "400 Bad Request -- check Client ID / Secret."
        elif resp.status_code == 401:
            return False, "401 Unauthorized -- Client ID or Secret is wrong."
        else:
            return False, f"Status {resp.status_code}: {resp.text[:80]}"
    except Exception as e:
        return False, f"Request failed: {e}"


def _verify_deezer_arl(arl: str) -> tuple[bool, str]:
    """Test ARL by fetching user data from gw-light."""
    import requests
    try:
        resp = requests.get(
            "https://www.deezer.com/ajax/gw-light.php",
            params={"method": "deezer.getUserData", "api_version": "1.0", "api_token": "null"},
            cookies={"arl": arl},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        data = resp.json()
        user = data.get("results", {}).get("USER", {})
        name = user.get("BLOG_NAME") or user.get("EMAIL") or ""
        plan = data.get("results", {}).get("OFFER_NAME", "")
        if user.get("USER_ID", 0) != 0:
            desc = f"Signed in as: {name}"
            if plan:
                desc += f"  ({plan})"
            return True, desc
        return False, "ARL rejected or not logged in."
    except Exception as e:
        return False, f"Request failed: {e}"


def verify_credentials_for_service(name: str, env: dict, context: str = 'all'):
    """Run the appropriate verification and print the result."""
    if name == "Spotify":
        if context in ("lyrics", "all"):
            token = env.get("SPOTIFY_AUTH_TOKEN", "").strip()
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            if token:
                ok, msg = _verify_spotify_lyrics_token(token)
                _print_verify("Spotify lyrics token", ok, msg)
            else:
                print("  (no Bearer token set yet -- skipping lyrics token check)")
        if context in ("resolver", "all"):
            client_id = env.get("SPOTIFY_CLIENT_ID", "").strip()
            client_secret = env.get("SPOTIFY_CLIENT_SECRET", "").strip()
            redirect_uri = env.get("SPOTIFY_REDIRECT_URI", "")
            if client_id and client_secret:
                ok, msg = _verify_spotify_api(client_id, client_secret, redirect_uri)
                _print_verify("Spotify API keys", ok, msg)
    elif name == "Deezer":
        arl = env.get("DEEZER_ARL", "").strip()
        if arl:
            ok, msg = _verify_deezer_arl(arl)
            _print_verify("Deezer ARL", ok, msg)


def _print_verify(label: str, ok: bool, msg: str):
    status = "  OK  " if ok else "  FAIL"
    print(f"  [{status}] {label}: {msg}")

def step_configure_credentials(chosen_resolvers: list[str], chosen_plugins: list[str], env: dict) -> dict:
    """Prompt for credentials for every chosen source that needs them."""
    # Build a deduped ordered list of services needing credentials
    # (Deezer appears in both resolvers and plugins but shares one ARL key)
    needs_config: list[tuple[str, str, list]] = []  # (section_title, kind, fields)
    seen_keys: set[str] = set()

    def add_if_new(title: str, fields: list):
        new_fields = [f for f in fields if f["key"] not in seen_keys]
        if new_fields:
            needs_config.append((title, new_fields))
            for f in new_fields:
                seen_keys.add(f["key"])

    for name in chosen_resolvers:
        r = next(x for x in RESOLVERS if x["name"] == name)
        if r["fields"]:
            add_if_new(f"{name} (resolver credentials)", r["fields"])

    for name in chosen_plugins:
        p = next(x for x in LYRICS_PLUGINS if x["name"] == name)
        if p["fields"]:
            add_if_new(f"{name} (lyrics credentials)", p["fields"])

    if not needs_config:
        return env

    total = len(needs_config)
    for i, (title, fields) in enumerate(needs_config, 1):
        clear()
        header(f"Step 3 -- Credentials ({i}/{total})", title)

        for field in fields:
            current = env.get(field["key"], "")
            new_val = prompt_field(field, current)
            env[field["key"]] = new_val
            print()

        # Verify credentials immediately after entry
        service_name = title.split(" (")[0]
        context = "resolver" if "(resolver" in title else "lyrics"
        print("  Verifying credentials...")
        verify_credentials_for_service(service_name, env, context)
        print()
        input("  Press Enter to continue...")

    return env


def show_install_hints(chosen_resolvers: list[str], chosen_plugins: list[str]):
    """Print pip install commands for any optional deps that are needed."""
    installs = []
    for name in chosen_resolvers:
        r = next(x for x in RESOLVERS if x["name"] == name)
        if r.get("install"):
            installs.append((name, r["install"]))
    for name in chosen_plugins:
        p = next(x for x in LYRICS_PLUGINS if x["name"] == name)
        if p.get("install"):
            installs.append((name, p["install"]))

    if installs:
        print("  Required packages:")
        for service, cmd in installs:
            print(f"    {service:<18} {cmd}")
        print()


def show_summary(chosen_resolvers: list[str], chosen_plugins: list[str], env: dict):
    clear()
    header("Configuration Summary")

    print("  METADATA RESOLVERS (where track lists come from):")
    for i, name in enumerate(chosen_resolvers, 1):
        print(f"    {i}. {name}")
    print()

    print("  LYRICS SOURCES (tried in this order):")
    for i, name in enumerate(chosen_plugins, 1):
        print(f"    {i}. {name}")
    print()

    all_services = list(dict.fromkeys(chosen_resolvers + chosen_plugins))
    for name in all_services:
        r_entry = next((x for x in RESOLVERS     if x["name"] == name), None)
        p_entry = next((x for x in LYRICS_PLUGINS if x["name"] == name), None)
        fields  = []
        if r_entry:
            fields += r_entry.get("fields", [])
        if p_entry:
            for f in p_entry.get("fields", []):
                if f["key"] not in {x["key"] for x in fields}:
                    fields.append(f)
        if not fields:
            print(f"  {name}: no credentials needed")
            continue
        print(f"  {name}:")
        for field in fields:
            val = env.get(field["key"], "")
            print(f"    {'OK' if val else '--'}  {field['label']:<28} {mask(val, field['secret'])}")
    print()


# -- Main ----------------------------------------------------------------------

def install_optional_sources():
    """Check for optional deps by scanning resolver/plugin INSTALL_REQUIRES — no hardcoding."""
    import importlib
    import subprocess
    import pkgutil
    import inspect
    import sys as _sys

    # Autodiscover optional deps from resolver and plugin classes
    OPTIONAL = []
    seen_packages = set()

    def _scan_dir(pkg_name, base_class_name):
        import importlib as _il
        import pkgutil as _pu
        from pathlib import Path
        pkg_dir = Path(__file__).parent / pkg_name
        for _, mod_name, _ in _pu.iter_modules([str(pkg_dir)]):
            if mod_name in ("base", "example_resolver", "example_plugin"):
                continue
            try:
                mod = _il.import_module(f"{pkg_name}.{mod_name}")
                for _, obj in inspect.getmembers(mod, inspect.isclass):
                    req = getattr(obj, "INSTALL_REQUIRES", None)
                    name = getattr(obj, "NAME", None)
                    if req and name and req not in seen_packages:
                        seen_packages.add(req)
                        import_name = req.replace("-", "_")
                        OPTIONAL.append({
                            "name": f"{name} ({pkg_name[:-1]})",
                            "package": req,
                            "import": import_name,
                        })
            except Exception:
                pass

    _scan_dir("resolvers", "BaseResolver")
    _scan_dir("plugins", "LyricsPlugin")

    clear()
    header("Install Optional Sources")
    print("  Checking optional dependencies..\n")

    missing = []
    for dep in OPTIONAL:
        try:
            importlib.import_module(dep["import"])
            print(f"  OK        {dep['name']} ({dep['package']})")
        except ImportError:
            print(f"  MISSING   {dep['name']} ({dep['package']})")
            missing.append(dep)

    if not missing:
        print("\n  All optional sources are already installed.")
        input("\n  Press Enter to return...")
        return

    print()
    if not ask_confirm(f"  Install {len(missing)} missing package(s)?"):
        return

    for dep in missing:
        print(f"\n  Installing {dep['package']}...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", dep["package"]],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  OK -- {dep['package']} installed successfully.")
        else:
            print(f"  FAILED -- {dep['package']}:")
            print(result.stderr[-500:] if result.stderr else "  (no output)")
            print(f"  Try manually: pip install {dep['package']}")

    print()
    input("  Done. Press Enter to return...")



def main():
    clear()
    header("multiplatform-lyric-downloader -- Setup Wizard")

    if not HAS_QUESTIONARY:
        print("  TIP: Install questionary for arrow-key menus:")
        print("       pip install questionary")
        print()

    env = load_env()

    # Re-run or quick edit if already configured
    if ENV_FILE.exists() and (env.get("SPOTIFY_CLIENT_ID") or env.get("PLUGIN_ORDER")):
        print("  Existing configuration found.")
        choice = ask_select(
            "What would you like to do?",
            ["Re-run full setup wizard", "Edit credentials for one service", "Edit priority order", "Install optional sources", "Exit"],
        )
        if not choice or choice == "Exit":
            sys.exit(0)
        if choice == "Edit credentials for one service":
            _quick_edit_credentials(env)
            return
        if choice == "Edit priority order":
            _quick_edit_order(env)
            return
        if choice == "Install optional sources":
            install_optional_sources()
            return
        # else fall through to full wizard

    step_intro()

    chosen_resolvers = step_choose_resolvers(env)
    if not chosen_resolvers:
        print("\n  No resolvers selected. Exiting.")
        sys.exit(0)

    chosen_plugins = step_choose_lyrics_plugins(env)
    if not chosen_plugins:
        print("\n  No lyrics sources selected. Exiting.")
        sys.exit(0)

    env = step_configure_credentials(chosen_resolvers, chosen_plugins, env)

    env["RESOLVER_ORDER"] = ",".join(chosen_resolvers)
    env["PLUGIN_ORDER"]   = ",".join(chosen_plugins)

    show_summary(chosen_resolvers, chosen_plugins, env)
    show_install_hints(chosen_resolvers, chosen_plugins)

    if ask_confirm("Save and exit?"):
        save_env(env)
        print(f"\n  Saved to {ENV_FILE.resolve()}")
        print()
        print("  You're ready. Try:")
        print("    python downloader.py -playing")
        print("    python downloader.py -album https://open.spotify.com/album/...")
        print("    python downloader.py -album https://www.deezer.com/album/...")
        print("    python downloader.py -list-resolvers")
        print()
    else:
        print("\n  Exiting without saving.")


def _quick_edit_credentials(env: dict):
    all_services = (
        [f"{r['name']} (resolver)" for r in RESOLVERS if r["fields"]]
        + [f"{p['name']} (lyrics)"  for p in LYRICS_PLUGINS if p["fields"]]
        + ["Cancel"]
    )
    choice = ask_select("Which service?", all_services)
    if not choice or choice == "Cancel":
        return

    service_name = choice.split(" (")[0]
    kind         = "resolver" if "resolver" in choice else "lyrics"
    registry     = RESOLVERS if kind == "resolver" else LYRICS_PLUGINS
    entry        = next(x for x in registry if x["name"] == service_name)

    print()
    for field in entry["fields"]:
        current = env.get(field["key"], "")
        new_val = prompt_field(field, current)
        env[field["key"]] = new_val
        print()
    save_env(env)
    print("  Saved.")


def _quick_edit_order(env: dict):
    print(f"\n  Current resolver order: {env.get('RESOLVER_ORDER', '(not set)')}")
    print(f"  Available: {', '.join(RESOLVER_NAMES)}")
    new = input("  New resolver order (comma-separated, Enter to keep): ").strip()
    if new:
        env["RESOLVER_ORDER"] = new

    print(f"\n  Current lyrics order:   {env.get('PLUGIN_ORDER', '(not set)')}")
    print(f"  Available: {', '.join(LYRICS_PLUGIN_NAMES)}")
    new = input("  New lyrics order (comma-separated, Enter to keep): ").strip()
    if new:
        env["PLUGIN_ORDER"] = new

    save_env(env)
    print("\n  Saved.")


if __name__ == "__main__":
    main()
