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

print("=== Raw /me/library/all (first page) ===")
try:
    data = sc._api_get("/me/library/all?limit=200")
    coll = data.get("collection", [])
    print(f"items on first page: {len(coll)}")
    # Show the distinct shapes so we can see where playlists live.
    from collections import Counter
    key_combos = Counter(
        ",".join(sorted(k for k in it.keys() if it.get(k))) for it in coll
    )
    for combo, n in key_combos.most_common():
        print(f"  {n:3d} x keys: {combo}")
except Exception as exc:
    import traceback
    traceback.print_exc()

print("\n=== Fetching playlists ===")
try:
    playlists = sc.fetch_playlists()
    print(f"Found {len(playlists)} playlists:")
    for p in playlists:
        print(f"  - {p['name']}  ({p['total']} tracks)  {p['id']}")
except Exception as exc:
    import traceback
    traceback.print_exc()
    print(f"\nFAILED: {exc}")
