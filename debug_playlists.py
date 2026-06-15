#!/usr/bin/env python3
"""Run this from the sub-scraper directory to debug SoundCloud playlist fetching.

Usage:  source .venv/bin/activate && python debug_playlists.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sub_scraper.scrapers.soundcloud import SoundCloudScraper

config_path = Path.home() / ".sub_scraper" / "config.json"
config = json.loads(config_path.read_text())
auth_token = config.get("soundcloud_auth_token", "")
username = config.get("soundcloud_username", "")

print(f"username: {username!r}")
print(f"auth_token set: {bool(auth_token)} (len={len(auth_token)})\n")

sc = SoundCloudScraper(auth_token=auth_token, username=username)

print("=== Extracting client_id ===")
try:
    cid = sc._get_client_id()
    print(f"client_id: {cid}\n")
except Exception as exc:
    print(f"FAILED: {exc}\n")
    sys.exit(1)

print("=== Fetching playlists ===")
try:
    playlists = sc.fetch_playlists()
    print(f"Found {len(playlists)} playlists:")
    for p in playlists:
        print(f"  - {p['name']}  ({p['total']} tracks)  {p['id']}")
except Exception as exc:
    import traceback
    traceback.print_exc()
    print(f"\nFAILED: {exc}")
