#!/usr/bin/env bash
# macOS double-click launcher — Finder runs .command files in a terminal.
cd "$(dirname "$0")"

# First run: create venv if missing.
if [ ! -f ".venv/bin/activate" ]; then
    echo "First run: setting up Sub-Scraper..."
    ./setup.sh
fi

./run.sh
