@echo off
:: ============================================================
:: setup.bat -- Run the multiplatform-lyric-downloader setup wizard
:: Double-click this to configure your credentials and sources.
:: ============================================================

echo.
echo  multiplatform-lyric-downloader -- Setup Wizard
echo  ==========================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.10+ from https://python.org
    echo.
    pause
    exit /b 1
)

:: Install dependencies if requirements.txt exists
if exist requirements.txt (
    echo  Installing / updating dependencies...
    python -m pip install -r requirements.txt --quiet
    echo  Done.
    echo.
)

:: Run the wizard
python setup_wizard.py

echo.
pause
