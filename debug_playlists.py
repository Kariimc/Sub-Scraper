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

user_id = sc._api_get("/me")["id"]
print(f"/me id: {user_id}\n")

# Probe every endpoint where playlists might live and report counts.
probes = [
    ("created playlists", f"/users/{user_id}/playlists?limit=200"),
    ("liked playlists", f"/users/{user_id}/playlist_likes?limit=200"),
    ("reposted playlists", f"/users/{user_id}/playlist_reposts?limit=200"),
    ("albums", f"/users/{user_id}/albums?limit=200"),
    ("library all", "/me/library/all?limit=200"),
    ("library playlists", "/me/library/playlists?limit=200"),
]
for label, path in probes:
    try:
        data = sc._api_get(path)
        coll = data.get("collection", data if isinstance(data, list) else [])
        print(f"=== {label}: {len(coll)} items  ({path}) ===")
        for it in coll[:25]:
            # Unwrap library items that nest the playlist.
            pl = it.get("playlist") or it.get("system_playlist") or it
            title = pl.get("title", "?")
            url = pl.get("permalink_url", "")
            tc = pl.get("track_count", "?")
            print(f"    {title}  ({tc} tracks)  {url}")
    except Exception as exc:
        print(f"=== {label}: FAILED — {exc}  ({path}) ===")
    print()

print("\n=== Fetching playlists ===")
playlists = []
try:
    playlists = sc.fetch_playlists()
    print(f"Found {len(playlists)} playlists:")
    for p in playlists:
        print(f"  - {p['name']}  ({p['total']} tracks)  {p['id']}")
except Exception as exc:
    import traceback
    traceback.print_exc()
    print(f"\nFAILED: {exc}")

if playlists:
    first = playlists[0]
    print(f"\n=== Tracks in first playlist: {first['name']} ===")
    try:
        tracks = sc.fetch_playlist_tracks(first["id"])
        print(f"Found {len(tracks)} tracks:")
        for t in tracks[:15]:
            print(f"  - {t.artist} - {t.title}  ({t.duration_ms // 1000}s)  {t.url}")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nFAILED: {exc}")
