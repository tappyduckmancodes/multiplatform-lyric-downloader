# multiplatform-lyric-downloader

Download synced (timestamped) `.lrc` lyric files from Spotify, Deezer, Musixmatch, LRCLIB, and YouTube — organized into `Artist\Album\Track.lrc` folders compatible with MusicBee, Plex, Kodi, Poweramp, and most LRC-aware players.

> **Migrating from [spotify-lyric-downloader](https://github.com/tappyduckmancodes/spotify-lyric-downloader)?**
> This is a complete rewrite — same output format, entirely new architecture. No browser automation or extra dependencies required. See [Migration Notes](#migrating-from-spotify-lyric-downloader) below.

---

## Features

- **Multi-source waterfall** — tries native source first, cascades through fallbacks automatically
- **Source priority**: Spotify → Deezer → LRCLIB → Musixmatch → YouTube captions
- **Auto-detects URL type** — paste any Spotify, Deezer, Tidal, or YouTube URL, no flags needed
- **Batch downloads** — full album, playlist, or artist discography from a single URL
- **YouTube playlist support** — expands `youtube.com/playlist?list=PL...` into all tracks
- **Smart metadata enrichment** — unknown albums filled in via LRCLIB → Spotify fallback
- **Fallback transparency** — logs which source was used and why a fallback occurred
- **Retry missed tracks** — `retry` command re-runs failed tracks from the last session
- **Token auto-renewal** — Musixmatch tokens refresh automatically; Spotify prompts on expiry
- **Cross-platform** — Windows (CMD/PowerShell), macOS, Linux, Git Bash

---

## Requirements

- **Python 3.10 or newer** — required. The tool uses `|` union types and other 3.10+ syntax.
  - Check your version: `python --version` (Windows) or `python3 --version` (macOS/Linux)
  - Download from [python.org](https://www.python.org/downloads/) if needed

---

## Quick Start

### 1. Install dependencies

```bash
pip install spotipy requests python-dotenv tqdm
pip install yt-dlp        # optional — enables YouTube caption source
pip install questionary   # optional — nicer interactive setup wizard prompts
```

Or install everything at once:
```bash
pip install -r requirements.txt
```

> **macOS / Linux:** use `pip3` and `python3` instead of `pip` and `python` if your system has both Python 2 and Python 3 installed.

### 2. Configure credentials

```bash
python setup_wizard.py
```

The wizard walks you through each credential interactively and writes your `.env` file.
Or copy `.env.example` to `.env` and fill it in manually — see [Credentials](#credentials) below.

> **Minimum setup with zero credentials:** LRCLIB and Musixmatch work out of the box with no setup at all. Coverage will be limited to those two sources.

### 3. Run

**Windows:**
```
download.bat
```

**macOS / Linux / Git Bash:**
```bash
bash download.sh
# or make it executable once:
chmod +x download.sh && ./download.sh
```

**Direct CLI (any platform):**
```bash
python downloader.py -track    https://open.spotify.com/track/...
python downloader.py -album    https://www.deezer.com/us/album/...
python downloader.py -playlist https://youtube.com/playlist?list=PL...
python downloader.py -artist   https://open.spotify.com/artist/...
python downloader.py -playing
python downloader.py -retry
```

---

## Credentials

### What actually requires credentials

Not everything needs credentials — here is what each one unlocks:

| Credential | What it enables | Without it |
|---|---|---|
| `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` | Spotify URL downloads, `-playing`, metadata enrichment for non-Spotify tracks | Spotify URLs won't resolve; other sources still work |
| `SPOTIFY_AUTH_TOKEN` | Spotify lyrics (the most accurate synced source) | Lyrics fall back to Deezer → LRCLIB → Musixmatch → YouTube |
| `DEEZER_ARL` | Deezer URL downloads + Deezer lyrics | Deezer URLs won't resolve; other sources still work |
| `MUSIXMATCH_TOKEN` | Higher Musixmatch rate limits | Community token used automatically — works fine for normal use |
| *(nothing)* | LRCLIB + YouTube captions | Always available, no setup required |

**Recommended minimum:** add `DEEZER_ARL` — Deezer has the best non-English and newer-release coverage and requires only a browser cookie to set up.

---

### Spotify

Two separate Spotify auth flows are used — one for metadata (track lists from URLs), one for lyrics:

| Purpose | Credential | Required for |
|---|---|---|
| Track/album/playlist metadata | `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` | Spotify URL downloads, `-playing`, metadata enrichment |
| Synced lyrics | `SPOTIFY_AUTH_TOKEN` | Spotify lyrics specifically |

You can have one without the other — e.g. Spotify API keys for URL resolution but no lyrics token means the lyrics waterfall skips Spotify and tries Deezer next.

**Getting API keys (free):**
1. Go to [developer.spotify.com](https://developer.spotify.com) → Log in → Create App
2. Set any Redirect URI (e.g. `http://192.168.1.x:8888/callback` — see `-playing` note below)
3. Copy Client ID and Client Secret into `.env`

**Getting the Spotify lyrics token** (~1 hour validity, prompted automatically when it expires):
1. Open [open.spotify.com](https://open.spotify.com) in your browser and play any song
2. Open DevTools (F12) → Network tab → filter requests by `color-lyrics`
3. Click the request → Headers tab → find `authorization`
4. Copy the value (everything after `Bearer `) and paste into `.env` as `SPOTIFY_AUTH_TOKEN=BQA...`

---

### Deezer

```
DEEZER_ARL=your_arl_here
```

The ARL cookie covers both resolving Deezer URLs and fetching Deezer lyrics — one credential does both.

**Getting your ARL:**
1. Open [deezer.com](https://www.deezer.com) and log in (requires a free or paid account)
2. Open DevTools (F12) → Application tab → Cookies → `deezer.com`
3. Find the cookie named `arl` and copy its value
4. Paste into `.env` as `DEEZER_ARL=your_value`

Validity: ~3 months. When it expires the Deezer plugin will disable itself at startup and the waterfall falls through to LRCLIB.

---

### Musixmatch

No setup required — a community token is used automatically.

For higher rate limits (useful for large batch downloads):
1. Open [musixmatch.com](https://www.musixmatch.com) in Firefox
2. F12 → Network → reload the page → click the `www.musixmatch.com` request → Cookies tab
3. Copy the value of `musixmatchUserToken`
4. Set `MUSIXMATCH_TOKEN=your_token` in `.env`

---

### LRCLIB

No credentials needed. Always available.

---

### YouTube captions

No credentials needed. `yt-dlp` must be installed (`pip install yt-dlp`).
Works with auto-generated and manual captions on any public YouTube video.

For age-restricted or private content, set one of these in `.env`:
```env
YTDLP_BROWSER=chrome    # reads cookies directly from your browser (chrome/firefox/edge/brave)
YTDLP_COOKIES_FILE=path/to/cookies.txt   # Netscape-format export from a browser extension
```

---

## Output Structure

Files are saved to `Lyrics/` by default (override with `-o path`):

```
Lyrics/
├── Pitbull/
│   └── Planet Pit (Deluxe Version)/
│       └── 02 Give Me Everything ft. Ne-Yo, Afrojack, Nayer.lrc
├── Sabrina Carpenter/
│   └── Short n' Sweet/
│       └── 07 Espresso.lrc
└── OCT/
    └── On Company Time/
        └── 03 POP! POP!.lrc
```

### Singles vs albums

The folder name comes from whatever the streaming service reports as the album at the time of download. For a track released as a single before its parent album:

- Downloaded while it's a single → saved under the single name (e.g. `Artist/Song Title/`)
- Downloaded again after the album releases → saved under the album name (e.g. `Artist/Album Name/`)
- The old single-directory file is not automatically removed — use `-f` to re-download and then manually delete the old directory

Spotify retroactively updates a track's album metadata when the parent album drops, so the same track URL will return the album name once it's out.

### LRC header tags

Each file includes standard LRC metadata:

```lrc
[ti:Track Title]
[ar:Artist Name]
[al:Album Name]
[length:03:45]
[#:2]           ← track number
[re:Spotify]    ← which plugin provided the lyrics
[by:multiplatform-lyric-downloader]

[00:01.23]First line of lyrics
[00:04.56]Second line of lyrics
```

---

## CLI Reference

```
python downloader.py MODE URL [FLAGS]

Modes:
  -track    URL     Single track
  -album    URL     Full album
  -playlist URL     Full playlist (Spotify, Deezer, YouTube)
  -artist   URL     Artist discography
  -playing          Currently playing Spotify track (requires OAuth)
  -retry            Re-run failed tracks from last session

Info:
  -check            Show resolver/plugin status and exit
  -list-plugins     List all lyrics plugins and their status
  -list-resolvers   List all metadata resolvers and their status

Flags:
  -source NAME      Force a specific lyrics source:
                    spotify | deezer | lrclib | musixmatch | youtube
  -f                Re-download existing files
  -v                Verbose / debug output
  -o PATH           Custom output directory (default: Lyrics/)
  -delay SECONDS    Delay between tracks (default: 0.5s)
```

---

## How the Waterfall Works

For each track, lyrics are fetched in priority order:

1. **Pass 1 — Synced only**: Spotify → Deezer → LRCLIB → Musixmatch → YouTube
   - Each source is tried; only timestamped (`[mm:ss.xx]`) results are accepted
   - If the URL is from Deezer, Deezer is moved to the front automatically
   - If the URL is from YouTube, YouTube is moved to the front
2. **Pass 2 — Plain text fallback**: if no synced lyrics found anywhere, plain text is accepted from any source
3. **Metadata enrichment**: if album or track number are unknown after lyrics fetch, LRCLIB then Spotify are queried for metadata only

A log line shows which source was used and when fallback occurs:
```
  [Spotify] Matched 'Espresso' → Sabrina Carpenter / Short n' Sweet (track #1)
  ℹ  Synced lyrics not available via Deezer — using LRCLIB as fallback source
```

---

## Platform Notes

### Windows

**Python setup:**
1. Download Python 3.10+ from [python.org](https://www.python.org/downloads/)
2. During install, check **"Add Python to PATH"** — required for `python` to work in CMD/PowerShell
3. Verify: open CMD and run `python --version`

**Running the tool:**

Use `download.bat` for the interactive launcher — double-click it or run it from any terminal. It opens a PowerShell session that handles everything including URLs containing `&` characters (no quoting needed).

If running `python downloader.py` directly from CMD, URLs containing `&` must be quoted:
```
python downloader.py -track "https://youtube.com/watch?v=ID&list=..."
```
This is not an issue in the launcher or in PowerShell.

**Dependencies:**
```
pip install spotipy requests python-dotenv tqdm yt-dlp
```

---

### macOS

**Python setup:**

macOS does not ship with Python 3.10+. Install it one of two ways:

Option A — Homebrew (recommended):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python
```

Option B — installer from [python.org](https://www.python.org/downloads/macos/).

After installing, use `python3` and `pip3` (not `python`/`pip`, which may point to the old system Python 2):
```bash
python3 --version   # should say 3.10 or newer
pip3 install spotipy requests python-dotenv tqdm yt-dlp
```

**Running the tool:**
```bash
bash download.sh
# or make executable once:
chmod +x download.sh
./download.sh
```

If macOS Gatekeeper blocks the script with "cannot be opened because it is from an unidentified developer", clear the quarantine flag:
```bash
xattr -d com.apple.quarantine download.sh
```

The setup wizard and direct CLI use `python3`:
```bash
python3 setup_wizard.py
python3 downloader.py -track https://...
```

> **Note:** macOS testing on this version is community-reported. Core functionality is confirmed working; edge cases on older macOS versions or non-Homebrew Python setups may vary.

---

### Linux

**Python setup:**

Most distributions ship with Python 3, but you may need to install `pip`:

Ubuntu / Debian:
```bash
sudo apt update
sudo apt install python3 python3-pip
```

Fedora / RHEL:
```bash
sudo dnf install python3 python3-pip
```

Arch:
```bash
sudo pacman -S python python-pip
```

Then install dependencies:
```bash
pip3 install spotipy requests python-dotenv tqdm yt-dlp
```

**Running the tool:**
```bash
bash download.sh
# or:
chmod +x download.sh && ./download.sh
```

Direct CLI:
```bash
python3 downloader.py -track https://...
```

---

### Git Bash (Windows)

Git Bash ships with Git for Windows and runs `download.sh` natively.

**Requirements:**
- Python must be installed on Windows and on your PATH (same as the Windows setup above)
- Git Bash inherits the Windows PATH, so if `python --version` works in CMD it will work here too

**Running the tool:**
```bash
bash download.sh
```

Direct CLI:
```bash
python downloader.py -track https://...
# or if python3 is the name on your system:
python3 downloader.py -track https://...
```

> If you see `winpty: command not found` errors when running interactive Python scripts, prefix with `winpty`: `winpty python setup_wizard.py`. The launcher handles this automatically.

---

## Known Limitations

- **Deezer synced lyrics**: only available for tracks where Deezer has indexed sync data. New releases often have plain text only for the first few weeks — this is a Deezer data gap, not a bug. The waterfall falls through to LRCLIB or Musixmatch automatically.
- **Spotify lyrics token**: expires after ~1 hour. The downloader detects this at startup and prompts for a new token before a batch run begins.
- **YouTube captions**: quality varies — auto-generated captions are not always accurate, and non-English tracks may have no captions at all.
- **`-playing`**: requires the Spotipy OAuth flow. Spotify no longer accepts `localhost` as a redirect URI — you must use your machine's LAN IP (e.g. `http://192.168.1.50:8888/callback`). Add this exact URL in your Spotify developer app settings under Redirect URIs, and set the same value as `SPOTIFY_REDIRECT_URI` in `.env`. Run `python setup_wizard.py` to auto-detect your LAN IP.
- **YouTube playlists**: mix/radio URLs (`&list=RD...`) are not playlists — only the current video downloads. Use a proper playlist URL (`youtube.com/playlist?list=PL...`).
- **Rate limits**: LRCLIB has no rate limit; Musixmatch community token can hit limits on heavy use (auto-renews once, then gracefully skips); Spotify lyrics ~60 requests/minute.
- **Singles becoming albums**: if you download a track while it is only available as a single, it saves under the single name. Once the parent album is released, re-downloading will save to the album directory — the single-directory file must be removed manually.

---

## `.env` Reference

```env
# ── Spotify API (metadata + OAuth for -playing) ───────────────────────────────
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
# Spotify no longer accepts localhost — use your LAN IP:
SPOTIFY_REDIRECT_URI=http://192.168.x.x:8888/callback

# ── Spotify lyrics Bearer token (~1hr, auto-prompted on expiry) ───────────────
SPOTIFY_AUTH_TOKEN=

# ── Deezer ARL cookie (~3 months, covers URL resolution + lyrics) ─────────────
DEEZER_ARL=

# ── Musixmatch usertoken (optional, community token used by default) ──────────
MUSIXMATCH_TOKEN=

# ── YouTube auth (optional, for age-restricted/private content) ───────────────
YTDLP_BROWSER=          # chrome | firefox | edge | brave | opera | safari
YTDLP_COOKIES_FILE=     # path to Netscape cookies.txt export

# ── Source priority (written by setup_wizard.py, edit to reorder) ─────────────
RESOLVER_ORDER=Spotify,Deezer,YouTube
PLUGIN_ORDER=Spotify,Deezer,LRCLIB,Musixmatch,YouTube
```

---

## Upgrading

```bash
git pull                        # or download and extract new zip
cp ../old-version/.env .env     # bring credentials forward (Windows: copy)
pip install -r requirements.txt --upgrade
```

The `.cache/` folder holds only derived runtime state (session tokens) and rebuilds itself automatically — no need to copy it between versions.

---

## Migrating from spotify-lyric-downloader

The [original repo](https://github.com/tappyduckmancodes/spotify-lyric-downloader) only downloaded the currently-playing Spotify track.

This rewrite replaces all of that:

| | Original | This version |
|---|---|---|
| Entry point | `converter.py` | `download.bat` / `download.sh` / `python downloader.py` |
| Input | Currently-playing Spotify only | Any Spotify, Deezer, YouTube, YouTube Music URL + `-playing` |
| Batch support | No | Track, album, playlist, artist discography |
| Lyrics sources | Spotify only | 5-source waterfall: Spotify → Deezer → LRCLIB → Musixmatch → YouTube |
| Token handling | Manual copy each run | Cached, prompted automatically on expiry |
| Output format | `Artist/Album/NN Track.lrc` | Identical |

**Dependencies from the original repo that are no longer needed:**

The original project listed `pyautogui`, `pyperclip`, and `beautifulsoup4 (bs4)` as dependencies. These were unused dead imports in that codebase and are not required here. If you have them installed from the original, you can uninstall them:
```bash
pip uninstall pyautogui pyperclip beautifulsoup4
```

**Output format is unchanged** — `Artist/Album/NN Track.lrc` — so your existing `Lyrics/` folder works as-is with this version.

**To migrate your credentials:**
1. Copy your old `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` into the new `.env`
2. Run `python setup_wizard.py` to fill in the rest interactively
3. Your Spotify Bearer token from the old setup is still valid — copy `SPOTIFY_AUTH_TOKEN` across too

---

## License

MIT
