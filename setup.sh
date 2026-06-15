#!/usr/bin/env bash
set -e

echo "========================================"
echo "  Sub-Scraper — Automated Setup"
echo "========================================"
echo ""

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found."
    echo "       Install Python 3.10+ from https://python.org then re-run this script."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python $PY_VER found but 3.10+ is required."
    exit 1
fi
echo "✓ Python $PY_VER"

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "✓ Virtual environment ready"

# ── Dependencies ───────────────────────────────────────────────────────────────
echo "→ Installing Python dependencies (this may take a minute)..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# ── ffmpeg check ──────────────────────────────────────────────────────────────
echo ""
if command -v ffmpeg &>/dev/null; then
    echo "✓ ffmpeg found"
else
    echo "⚠  ffmpeg not found — audio conversion will not work without it."
    OS="$(uname -s)"
    case "$OS" in
        Darwin)
            echo "   Install with:  brew install ffmpeg"
            echo "   (If you don't have Homebrew: https://brew.sh)"
            ;;
        Linux)
            echo "   Ubuntu/Debian:  sudo apt install ffmpeg"
            echo "   Fedora/RHEL:    sudo dnf install ffmpeg"
            echo "   Arch:           sudo pacman -S ffmpeg"
            ;;
        *)
            echo "   Download from: https://ffmpeg.org/download.html"
            ;;
    esac
fi

echo ""
echo "========================================"
echo "  Setup complete!"
echo "  Run the app with:  ./run.sh"
echo "========================================"
