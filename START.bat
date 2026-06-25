@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Sub-Scraper

REM ============================================================
REM  Sub-Scraper -- one-click launcher
REM
REM  First double-click : sets everything up and asks for your
REM                       keys ONE time in a Notepad window.
REM  Every time after   : the same double-click just opens the app.
REM ============================================================

REM ---------- 1. Python check ----------
where python >nul 2>&1
if errorlevel 1 goto :no_python

REM ---------- 2. First-run setup (virtual env + dependencies) ----------
if exist ".venv\Scripts\activate.bat" goto :have_venv
echo ============================================================
echo   First-time setup -- this runs ONCE and takes a few minutes.
echo   Grab a coffee; the window moves on by itself when it's done.
echo ============================================================
echo.
echo [1/3] Creating environment...
python -m venv .venv
call ".venv\Scripts\activate.bat"
echo [2/3] Updating installer...
python -m pip install --upgrade pip >nul 2>&1
echo [3/3] Installing Sub-Scraper -- the slow part, hang tight...
pip install -r requirements.txt
goto :after_venv

:have_venv
call ".venv\Scripts\activate.bat"

:after_venv

REM ---------- 3. ffmpeg (audio conversion) -- auto-install once ----------
where ffmpeg >nul 2>&1
if not errorlevel 1 goto :have_ffmpeg
where winget >nul 2>&1
if errorlevel 1 goto :no_winget_ffmpeg
echo.
echo Installing ffmpeg (one time, needed for audio) -- this can take a minute...
winget install --silent --accept-package-agreements --accept-source-agreements Gyan.FFmpeg
if errorlevel 1 goto :no_winget_ffmpeg
goto :need_relaunch

:no_winget_ffmpeg
echo.
echo NOTE: ffmpeg isn't installed. The app still opens, but converting
echo       audio needs it. Quickest fix: press Win+R, type  cmd  press Enter,
echo       then run:  winget install ffmpeg
echo       (or download from https://ffmpeg.org/download.html). Continuing...
echo.

:have_ffmpeg

REM ---------- 4. First-run credentials (the .env file) ----------
if exist ".env" goto :have_env
if exist ".env.example" (
    copy ".env.example" ".env" >nul
) else (
    > ".env" echo SPOTIFY_CLIENT_ID=
    >> ".env" echo SPOTIFY_CLIENT_SECRET=
    >> ".env" echo SOUNDCLOUD_USERNAME=
    >> ".env" echo SOUNDCLOUD_AUTH_TOKEN=
)
cls
echo ============================================================
echo   ONE-TIME SETUP -- your login keys
echo ------------------------------------------------------------
echo   A Notepad window just opened with 4 blank keys.
echo   Type or paste your value after each = sign, like:
echo.
echo       SPOTIFY_CLIENT_ID=ab12cd34...
echo.
echo   Then press Ctrl+S to save and close Notepad.
echo   You will NEVER have to do this again.
echo ============================================================
notepad ".env"

:have_env

REM ---------- 5. Launch ----------
cls
echo Launching Sub-Scraper...
echo (You can close this black window once the app appears.)
python main.py
goto :eof

:no_python
echo ============================================================
echo   Python isn't installed, or isn't on your PATH.
echo     1) Install it from  https://python.org
echo     2) During install, TICK "Add Python to PATH"
echo     3) Then double-click this file again.
echo ============================================================
pause
goto :eof

:need_relaunch
cls
echo ============================================================
echo   Almost there! ffmpeg was just installed, but this window
echo   can't see it until it restarts.
echo.
echo   Please double-click START.bat ONE more time to finish.
echo ============================================================
pause
goto :eof
