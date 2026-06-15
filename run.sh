#!/usr/bin/env bash
cd "$(dirname "$0")"
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
