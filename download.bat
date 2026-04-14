@echo off
:: ============================================================
:: download.bat -- Multiplatform Lyric Downloader
:: Launches the PowerShell interactive runner (handles & in URLs)
:: ============================================================

:: Single PowerShell invocation — avoids the focus-steal/minimize
:: that happens when a hidden PS process runs first.
:: QuickEdit is disabled inside download.ps1 at startup.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download.ps1"
