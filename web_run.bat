@echo off
REM Launch the Sub-Scraper web interface on Windows.
REM Activates a local .venv if present, then starts the web server.

setlocal EnableDelayedExpansion

cd /d "%~dp0"

REM Activate virtual environment if present
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
    echo Activated .venv
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
    echo Activated venv
)

REM Check/install web dependencies
python -c "import uvicorn" 2>nul
if errorlevel 1 (
    echo Installing web dependencies...
    pip install -r requirements-web.txt
)

echo Starting Sub-Scraper web UI at http://localhost:8080
python web_run.py
