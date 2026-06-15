#!/usr/bin/env bash
# Creates a double-clickable desktop launcher for Sub-Scraper.
# Run once, after setup.sh has completed.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$HOME/Desktop"
APPS_DIR="$HOME/.local/share/applications"
LAUNCHER="$APP_DIR/SubScraper.desktop"

mkdir -p "$APPS_DIR"

cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Sub-Scraper
Comment=Download your Spotify and SoundCloud libraries
Exec=$APP_DIR/run.sh
Path=$APP_DIR
Icon=multimedia-player
Terminal=true
Categories=AudioVideo;Audio;
EOF

chmod +x "$LAUNCHER"
chmod +x "$APP_DIR/run.sh"

# Register in the application menu (and Steam Deck's app list).
cp "$LAUNCHER" "$APPS_DIR/SubScraper.desktop"

# Place a copy on the desktop if there is one.
if [ -d "$DESKTOP_DIR" ]; then
    cp "$LAUNCHER" "$DESKTOP_DIR/SubScraper.desktop"
    chmod +x "$DESKTOP_DIR/SubScraper.desktop"
    echo "✓ Launcher placed on your Desktop: SubScraper.desktop"
fi

echo "✓ Launcher added to your application menu."
echo ""
echo "On KDE / Steam Deck desktop mode, the first time you double-click it"
echo "you may get a security prompt — choose 'Continue' or right-click the"
echo "icon and pick 'Allow Launching' to trust it."
