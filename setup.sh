#!/usr/bin/env bash
set -e

# Always operate from the app folder, no matter where this script is invoked
# from (terminal cwd, file-manager double-click, desktop launcher, ...).
cd "$(dirname "$0")"

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
# Treat a venv without an activate script as broken (e.g. an earlier run that
# died part-way) and rebuild it from scratch.
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "→ Removing an incomplete .venv from a previous run..."
    rm -rf .venv
fi

if [ ! -d ".venv" ]; then
    echo "→ Creating virtual environment..."
    # SteamOS / some distros ship a Python whose bundled pip (ensurepip) is
    # broken or stripped, so `python3 -m venv .venv` aborts. Fall back to a
    # pip-less venv and bootstrap pip ourselves.
    if ! python3 -m venv .venv 2>/dev/null; then
        echo "  (standard venv failed — creating without pip and bootstrapping)"
        rm -rf .venv
        python3 -m venv --without-pip .venv
    fi
fi

if [ ! -f ".venv/bin/activate" ]; then
    echo ""
    echo "ERROR: Could not create a virtual environment in .venv"
    echo "       On Debian/Ubuntu install the venv package and re-run:"
    echo "         sudo apt install python3-venv"
    exit 1
fi
source .venv/bin/activate

# Make sure pip exists inside the venv (it won't if we used --without-pip, and
# can be missing on minimal SteamOS Python builds).
if ! python -m pip --version &>/dev/null; then
    echo "→ Bootstrapping pip..."
    python -m ensurepip --upgrade 2>/dev/null \
        || { curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
             && python /tmp/get-pip.py && rm -f /tmp/get-pip.py; }
fi
echo "✓ Virtual environment ready"

# ── Dependencies ───────────────────────────────────────────────────────────────
echo "→ Installing Python dependencies (this may take a minute)..."
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
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

# ── Verify the install actually works ──────────────────────────────────────────
echo ""
echo "→ Verifying the install..."
if ! python -c "import customtkinter, aiohttp, spotipy, yt_dlp" 2>/tmp/ss_verify.log; then
    echo ""
    echo "ERROR: setup finished but core packages are missing:"
    sed 's/^/       /' /tmp/ss_verify.log
    rm -f /tmp/ss_verify.log
    echo ""
    echo "       The virtual environment is not usable yet. Try re-running"
    echo "       ./setup.sh, or remove .venv and run it again."
    exit 1
fi
rm -f /tmp/ss_verify.log
echo "✓ All core packages import correctly"

echo ""
echo "========================================"
echo "  Setup complete!"
echo "  Run the app with:  ./run.sh"
echo "========================================"
