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

# ── tkinter check (Linux ships it as a separate system package) ────────────────
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "ERROR: tkinter not found — this is required for the GUI."
    echo "       Install it with your package manager, then re-run this script:"
    echo ""
    echo "       Ubuntu/Debian:  sudo apt install python3-tk"
    echo "       Fedora/RHEL:    sudo dnf install python3-tkinter"
    echo "       Arch:           sudo pacman -S tk"
    echo ""
    exit 1
fi
echo "✓ tkinter found"

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

# ── aria2c (optional — big multi-connection download speed boost) ──────────────
echo ""
if command -v aria2c &>/dev/null; then
    echo "✓ aria2c found (fast multi-connection downloads enabled)"
else
    echo "→ aria2c not found — attempting install (optional speed boost)..."
    set +e
    OS="$(uname -s)"
    if [ "$OS" = "Darwin" ] && command -v brew &>/dev/null; then
        brew install aria2
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y aria2
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y aria2
    elif command -v pacman &>/dev/null && [ -w /usr ]; then
        sudo pacman -S --noconfirm aria2
    fi

    # Fallback for read-only systems (Steam Deck / SteamOS): a static binary in
    # the venv. No root, on PATH via run.sh, untouched by OS updates.
    # Source: github.com/q3aql/aria2-static-builds (third-party static builds).
    if ! command -v aria2c &>/dev/null && [ "$OS" = "Linux" ]; then
        echo "→ Installing a local static aria2c into .venv/bin ..."
        ARIA_VER="1.37.0"
        ARIA_NAME="aria2-${ARIA_VER}-linux-gnu-64bit-build1"
        ARIA_URL="https://github.com/q3aql/aria2-static-builds/releases/download/v${ARIA_VER}/${ARIA_NAME}.tar.bz2"
        TMP="$(mktemp -d)"
        if curl -fsSL "$ARIA_URL" -o "$TMP/aria2.tar.bz2" && tar -xjf "$TMP/aria2.tar.bz2" -C "$TMP"; then
            ARIA_BIN="$(find "$TMP" -name aria2c -type f | head -1)"
            if [ -n "$ARIA_BIN" ]; then
                cp "$ARIA_BIN" .venv/bin/aria2c && chmod +x .venv/bin/aria2c
                echo "   (from $ARIA_URL)"
            fi
        fi
        rm -rf "$TMP"
    fi
    set -e

    if command -v aria2c &>/dev/null; then
        echo "✓ aria2c installed"
    else
        echo "⚠  Could not auto-install aria2c — downloads still work (yt-dlp native)."
        echo "   The app simply runs without the extra speed boost."
    fi
fi

echo ""
echo "========================================"
echo "  Setup complete!"
echo "  Run the app with:  ./run.sh"
echo "========================================"
