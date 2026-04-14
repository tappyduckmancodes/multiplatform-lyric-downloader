#!/usr/bin/env bash
# ============================================================
# download.sh -- Multiplatform Lyric Downloader
# Cross-platform launcher for macOS, Linux, and Git Bash
# ============================================================

if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else echo "Error: Python not found."; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SESSION_TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$SCRIPT_DIR/logs"
export MLD_SESSION_LOG="$SCRIPT_DIR/logs/${SESSION_TIMESTAMP}.log"
SUMMARY_FILE="$SCRIPT_DIR/.cache/session_summary.json"
# Clear previous session summary
rm -f "$SUMMARY_FILE"

show_session_summary() {
    echo
    echo " =============================================="
    echo "  Session complete"
    if [ -f "$SUMMARY_FILE" ]; then
        python3 -c "
import json, sys
try:
    s = json.load(open('$SUMMARY_FILE'))
    print(f'  + Downloaded : {s.get("downloaded",0)}')
    print(f'  > Skipped    : {s.get("skipped",0)}  (already existed)')
    if s.get('missing',0): print(f'  - Not found  : {s.get("missing",0)}')
    if s.get('errors',0):  print(f'  ! Errors     : {s.get("errors",0)}')
except: pass
" 2>/dev/null
    fi
    echo " =============================================="
    echo
    printf "  Press Enter to close..."
    read -r _dummy
}

detect_kind() {
    local url="$1"
    if echo "$url" | grep -qi "youtube\.com/playlist";                                            then echo "-playlist"; return; fi
    if echo "$url" | grep -qiE "spotify\.com/album|deezer\.com.*/album|tidal\.com.*/album";       then echo "-album";    return; fi
    if echo "$url" | grep -qiE "spotify\.com/playlist|deezer\.com.*/playlist|tidal\.com.*/playlist"; then echo "-playlist"; return; fi
    if echo "$url" | grep -qiE "spotify\.com/artist|deezer\.com.*/artist|tidal\.com.*/artist";    then echo "-artist";   return; fi
    echo "-track"
}

# Run one download cycle for a URL or command.
# Returns 0 normally, 1 if user wants to quit.
do_download() {
    local INPUT="$1"
    INPUT="${INPUT%\"}"
    INPUT="${INPUT#\"}"
    INPUT="$(echo "$INPUT" | xargs)"   # trim whitespace

    INPUT_LOWER="$(echo "$INPUT" | tr '[:upper:]' '[:lower:]')"
    case "$INPUT_LOWER" in
        quit|exit|q) return 1 ;;
    esac

    echo
    echo "  Optional flags (press Enter to skip):"
    echo "    -source spotify/deezer/lrclib/musixmatch/youtube"
    echo "    -f                    Re-download existing files"
    echo "    -v                    Verbose / debug output"
    echo "    -o \"path\"            Custom output folder"
    echo
    printf "  Flags: "
    read -r FLAGS

    echo

    INPUT_LOWER="$(echo "$INPUT" | tr '[:upper:]' '[:lower:]')"
    case "$INPUT_LOWER" in
        playing)
            # shellcheck disable=SC2086
            $PY downloader.py -playing $FLAGS
            ;;
        retry)
            # shellcheck disable=SC2086
            $PY downloader.py -retry $FLAGS
            ;;
        *)
            KIND=$(detect_kind "$INPUT")
            echo "  Detected: $KIND"
            if [ -n "$FLAGS" ]; then echo "  Flags:    $FLAGS"; fi
            echo
            # shellcheck disable=SC2086
            $PY downloader.py $KIND "$INPUT" $FLAGS
            ;;
    esac
    return 0
}

# Startup status check
echo
echo " Multiplatform Lyric Downloader"
echo " ================================"
echo
echo " Checking sources..."
$PY downloader.py -check

while true; do
    echo
    echo " Multiplatform Lyric Downloader"
    echo " ================================"
    echo
    echo " Paste any URL -- source detected automatically:"
    echo "   Spotify, Deezer, Tidal, YouTube, YouTube Music"
    echo
    echo " Or type a command:  playing  |  retry  |  quit"
    echo

    printf "  URL or command: "
    read -r INPUT

    INPUT="${INPUT%\"}"
    INPUT="${INPUT#\"}"

    INPUT_LOWER="$(echo "$INPUT" | tr '[:upper:]' '[:lower:]')"
    case "$INPUT_LOWER" in
        quit|exit|q) show_session_summary; exit 0 ;;
        "")
            echo "  Nothing entered. Try again."
            continue
            ;;
    esac

    do_download "$INPUT" || exit 0

    # Inner loop — stays here after each download instead of redisplaying the full header
    while true; do
        echo
        printf "  Download another? [Y/n/URL]: "
        read -r AGAIN

        AGAIN_LOWER="$(echo "$AGAIN" | tr '[:upper:]' '[:lower:]')"
    case "$AGAIN_LOWER" in
            n|no|quit|exit|q) exit 0 ;;
            http://*|https://*|playing|retry)
                do_download "$AGAIN" || exit 0
                continue   # ask "Download another?" again without full header
                ;;
            *)
                break      # y / Enter — break to outer loop to show full header
                ;;
        esac
    done
done
