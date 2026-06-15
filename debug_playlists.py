#!/usr/bin/env python3
"""Run this from the sub-scraper directory to debug SoundCloud playlist fetching."""
import json
import subprocess
import sys
from pathlib import Path

config_path = Path.home() / ".sub_scraper" / "config.json"
config = json.loads(config_path.read_text())
username = config.get("soundcloud_username", "")
auth_token = config.get("soundcloud_auth_token", "")

if not username:
    print("ERROR: No SoundCloud username in config.")
    sys.exit(1)

url = f"https://soundcloud.com/{username}/sets"
print(f"Fetching: {url}\n")

auth_args = ["--add-header", f"Authorization: OAuth {auth_token}"] if auth_token else []

# Test 1: -j (per-line JSON, current approach)
print("=== TEST 1: yt-dlp --flat-playlist -j ===")
cmd = ["yt-dlp", "--flat-playlist", "-j", "--no-warnings", url] + auth_args
result = subprocess.run(cmd, capture_output=True, text=True)
print(f"returncode: {result.returncode}")
print(f"stderr: {result.stderr[:300]}")
lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
print(f"stdout lines: {len(lines)}")
for i, line in enumerate(lines[:3]):
    try:
        e = json.loads(line)
        print(f"  entry {i}: type={e.get('_type')} ie_key={e.get('ie_key')} url={e.get('url','')[:80]} title={e.get('title','')}")
    except Exception as ex:
        print(f"  entry {i}: parse error: {ex} | raw: {line[:120]}")

print()

# Test 2: -J (single JSON dump)
print("=== TEST 2: yt-dlp --flat-playlist -J ===")
cmd2 = ["yt-dlp", "--flat-playlist", "-J", "--no-warnings", url] + auth_args
result2 = subprocess.run(cmd2, capture_output=True, text=True)
print(f"returncode: {result2.returncode}")
try:
    data = json.loads(result2.stdout)
    entries = data.get("entries")
    print(f"entries type: {type(entries)}, len: {len(entries) if entries else 'N/A'}")
    if entries:
        e0 = entries[0]
        print(f"  first entry: {json.dumps(e0)[:200]}")
except Exception as ex:
    print(f"parse error: {ex}")
    print(f"raw (first 300): {result2.stdout[:300]}")
