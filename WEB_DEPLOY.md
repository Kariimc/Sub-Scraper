<div align="center">
  <img src="assets/logo.png" width="90" alt="Sub-Scraper logo"/>

  # Sub-Scraper — Web Version & Sharing Guide

  **Get a link your friends can open in their browser.**
</div>

---

There are three ways to let other people try Sub-Scraper, from fastest to most
permanent.

## Option A — Try it right now on your network (30 seconds)

If your friends are on the same Wi-Fi (same house, same office):

```bash
pip install -r requirements-web.txt
python web_run.py
```

The terminal prints something like `http://0.0.0.0:8080`. Find your computer's
local IP address and share `http://<your-ip>:8080`:

- **macOS:** System Settings → Wi-Fi → Details → IP Address (e.g. `192.168.1.42`)
- **Windows:** run `ipconfig` and look for "IPv4 Address"
- **Linux:** run `hostname -I`

So friends open e.g. **`http://192.168.1.42:8080`**. Your machine has to stay on
and running the command.

## Option B — A public link from your machine (a few minutes)

To share with friends **anywhere** (not just your Wi-Fi) without hosting, use a
free tunnel. Easiest is [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/):

```bash
# Terminal 1 — start the app
python web_run.py

# Terminal 2 — expose it (no signup needed)
cloudflared tunnel --url http://localhost:8080
```

Cloudflare prints a public `https://something-random.trycloudflare.com` link.
Share that. It lasts as long as the command runs.

> [ngrok](https://ngrok.com) works the same way: `ngrok http 8080`.

## Option C — Host it permanently (one-click, recommended for sharing)

Deploy your own always-on instance with a stable URL. Both options below build
from the included `Dockerfile` and read your `$PORT` automatically.

### Render (easiest, free tier)

1. Click: **[Deploy to Render](https://render.com/deploy?repo=https://github.com/Kariimc/Sub-Scraper)**
2. Sign in with GitHub, click **Connect** on this repo.
3. Render reads `render.yaml`, click **Apply**.
4. Wait ~3 minutes for the first build. You'll get a link like
   **`https://sub-scraper.onrender.com`**.
5. Open it → **Settings** → paste your Spotify/SoundCloud credentials → **Save**.

> Free Render services **sleep after 15 minutes idle** and take ~30s to wake on
> the next visit. That's fine for friends trying it out.

### Railway

1. Click: **[Deploy on Railway](https://railway.com/new)**
2. Choose **Deploy from GitHub repo** and select this repository.
3. Railway reads `railway.json` + `Dockerfile` and builds it.
4. Under **Settings → Networking**, click **Generate Domain** to get a public URL
   like **`https://sub-scraper-production.up.railway.app`**.
5. Open it → **Settings** → paste your credentials → **Save**.

---

## Important notes for hosted instances

- **Each person needs their own Spotify keys.** The app reads *your* library, so
  whoever uses it must add their own credentials in Settings (it takes 2 minutes
  — see [`SETUP.md`](SETUP.md)). Nothing is shared between instances.
- **Downloads land on the server**, not on the visitor's computer. The hosted web
  version is best for browsing libraries and trying the engine. To build and keep
  a real music collection — and to copy it to a **portable HiFi player** — use the
  desktop app (it has the **Device Sync** tab).
- **Spotify redirect URI:** the hosted login flow still uses the desktop OAuth
  redirect. For a purely hosted login you'd add your deploy URL to the Spotify
  app's redirect URIs; for trying it out, loading a public playlist or your
  SoundCloud likes works without that step.
- **Keep your instance private** if you put real credentials in it — don't post
  the Settings page publicly with keys saved.

---

Questions about the desktop app instead? See the [README](README.md) and
[`SETUP.md`](SETUP.md).
