# ============================================================
# download.ps1 -- Multiplatform Lyric Downloader
# PowerShell interactive runner — handles & in URLs natively
# ============================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Disable QuickEdit so right-clicking doesn't freeze the window.
# Done here (not in the bat) so there's only one PowerShell process — avoids
# the focus-steal/minimize caused by spawning a hidden PS process first.
try { Set-ItemProperty 'HKCU:\Console' QuickEdit 0 -ErrorAction SilentlyContinue } catch {}

# Force UTF-8 so checkmarks and arrows display correctly in the terminal
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding             = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING       = "utf-8"

# One log file per interactive session
$SessionTimestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$env:MLD_SESSION_LOG = Join-Path $ScriptDir "logs\$SessionTimestamp.log"
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "logs") | Out-Null

# Clear the session summary so this session starts fresh
$SummaryFile = Join-Path $ScriptDir ".cache\session_summary.json"
if (Test-Path $SummaryFile) { Remove-Item $SummaryFile -Force }

function Get-Kind($url) {
    if ($url -match "youtube\.com/playlist")                                               { return "-playlist" }
    if ($url -match "spotify\.com/album|deezer\.com.*/album|tidal\.com.*/album")          { return "-album"    }
    if ($url -match "spotify\.com/playlist|deezer\.com.*/playlist|tidal\.com.*/playlist") { return "-playlist" }
    if ($url -match "spotify\.com/artist|deezer\.com.*/artist|tidal\.com.*/artist")       { return "-artist"   }
    return "-track"
}

function Run-Download($url, $flags) {
    $url = $url.Trim('"').Trim("'").Trim()
    # Split flags string into individual tokens.
    # Simple whitespace split — Python argparse handles -key and value as
    # separate array elements fine, so no need to pre-pair them.
    $fArgs = if ($flags) {
        $flags.Trim() -split '\s+' | Where-Object { $_ -ne "" }
    } else { @() }

    switch ($url.ToLower()) {
        "playing" { & python downloader.py -playing $fArgs }
        "retry"   { & python downloader.py -retry   $fArgs }
        default   {
            $kind = Get-Kind $url
            Write-Host "  Detected: $kind"
            if ($flags) { Write-Host "  Flags:    $flags" }
            Write-Host ""
            & python downloader.py $kind $url $fArgs
        }
    }
}

function Prompt-AndRun($url) {
    $url = $url.Trim('"').Trim("'").Trim()
    Write-Host ""
    Write-Host "  Optional flags (press Enter to skip):"
    Write-Host "    -source spotify/deezer/lrclib/musixmatch/youtube"
    Write-Host "    -f                    Re-download existing files"
    Write-Host "    -v                    Verbose / debug output"
    Write-Host "    -o `"path`"             Custom output folder"
    Write-Host ""
    $flags = (Read-Host "  Flags").Trim()
    Write-Host ""
    Run-Download $url $flags
}

function Show-SessionSummary {
    Write-Host ""
    Write-Host " =============================================="
    Write-Host "  Session complete"
    if (Test-Path $SummaryFile) {
        try {
            $s = Get-Content $SummaryFile | ConvertFrom-Json
            Write-Host ("  + Downloaded : {0}" -f $s.downloaded)
            Write-Host ("  > Skipped    : {0}  (already existed)" -f $s.skipped)
            if ($s.missing -gt 0)  { Write-Host ("  - Not found  : {0}" -f $s.missing) }
            if ($s.errors  -gt 0)  { Write-Host ("  ! Errors     : {0}" -f $s.errors)  }
        } catch {
            # summary file missing or malformed — skip gracefully
        }
    }
    Write-Host " =============================================="
    Write-Host ""
    Write-Host "  Press any key to close..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# ── Startup status check ──────────────────────────────────────────────────────
Write-Host ""
Write-Host " Multiplatform Lyric Downloader"
Write-Host " ================================"
Write-Host ""
Write-Host " Checking sources..."
& python downloader.py -check
# (exits immediately via sys.exit, output goes straight to terminal)

# ── Main loop ─────────────────────────────────────────────────────────────────
while ($true) {
    Write-Host ""
    Write-Host " Multiplatform Lyric Downloader"
    Write-Host " ================================"
    Write-Host ""
    Write-Host " Paste any URL -- source detected automatically:"
    Write-Host "   Spotify, Deezer, Tidal, YouTube, YouTube Music"
    Write-Host ""
    Write-Host " Or type a command:  playing  |  retry  |  quit"
    Write-Host ""

    $rawInput = (Read-Host "  URL or command").Trim('"').Trim("'").Trim()

    if ([string]::IsNullOrWhiteSpace($rawInput)) { Write-Host "  Nothing entered. Try again."; continue }
    if ($rawInput -iin @("quit","exit","q")) { Show-SessionSummary; exit 0 }

    Prompt-AndRun $rawInput

    # Inner loop — stays here after each download
    while ($true) {
        Write-Host ""
        $again = (Read-Host "  Download another? [Y/n/URL]").Trim()

        if ($again -iin @("n","no","quit","exit","q")) { Show-SessionSummary; exit 0 }

        if ($again -match "^https?://" -or $again -iin @("playing","retry")) {
            Prompt-AndRun $again
            continue
        }

        break  # y / Enter — show full header
    }
}
