#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/activate" ]; then
    echo "Sub-Scraper isn't set up yet (no virtual environment found)."
    echo "Run the one-time setup first:"
    echo ""
    echo "    ./setup.sh"
    echo ""
    read -n 1 -s -r -p "Press any key to close..."
    echo ""
    exit 1
fi

source .venv/bin/activate
python main.py
status=$?
if [ "$status" -ne 0 ]; then
    echo ""
    echo "Sub-Scraper exited with an error (code $status)."
    echo "If this was a crash, the lines above show where."
    read -n 1 -s -r -p "Press any key to close..."
    echo ""
fi
