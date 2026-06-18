#!/usr/bin/env bash
# Launch the Sub-Scraper web interface.
# Activates a local .venv if present, then starts the web server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if one exists
if [ -f ".venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source ".venv/bin/activate"
    echo "Activated .venv"
elif [ -f "venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "venv/bin/activate"
    echo "Activated venv"
fi

# Check for required packages
if ! python3 -c "import uvicorn" 2>/dev/null; then
    echo "Installing web dependencies..."
    pip install -r requirements-web.txt
fi

echo "Starting Sub-Scraper web UI at http://0.0.0.0:8080"
exec python3 web_run.py
