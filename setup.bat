@echo off
setlocal EnableDelayedExpansion

echo ========================================
echo   Sub-Scraper -- Automated Setup
echo ========================================
echo.

:: ── Python check ────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo        Install Python 3.10+ from https://python.org
    echo        Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")"') do set PY_VER=%%v
echo ✓ Python %PY_VER% found

:: ── Virtual environment ──────────────────────────────────────────────────────
if not exist ".venv" (
    echo ^→ Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
echo ✓ Virtual environment ready

:: ── Dependencies ────────────────────────────────────────────────────────────
echo ^→ Installing Python dependencies (this may take a minute)...
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo ✓ Dependencies installed

:: ── ffmpeg check ────────────────────────────────────────────────────────────
echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo ⚠  ffmpeg not found -- audio conversion will not work without it.
    echo    Install options:
    echo      winget:      winget install ffmpeg
    echo      Chocolatey:  choco install ffmpeg
    echo      Manual:      https://ffmpeg.org/download.html
    echo    After installing, re-open this terminal and run setup.bat again.
) else (
    echo ✓ ffmpeg found
)

echo.
echo ========================================
echo   Setup complete!
echo   Run the app with:  run.bat
echo ========================================
pause
