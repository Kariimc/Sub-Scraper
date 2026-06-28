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
4. During setup Render asks you to fill in the **environment variables** from
   `render.yaml` (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
   `SOUNDCLOUD_USERNAME`, `SOUNDCLOUD_AUTH_TOKEN`). Paste in the ones you use and
   leave the rest blank. You can also add/edit them later under your service →
   **Environment**.
5. Wait ~3 minutes for the first build. You'll get a link like
   **`https://sub-scraper.onrender.com`** — open it and you're ready.

> ⚠️ **Put your keys in Environment variables, not just the Settings page.**
> Free Render services **sleep after 15 minutes idle** (and take ~30s to wake).
> When they sleep, the disk is wiped — so anything typed into the in-app
> **Settings** page is **lost**, but environment variables **persist**. Setting
> them as above means you never re-enter your keys.
>
> One exception: the Spotify *login* itself can't be stored as an env var, so
> after the instance wakes you may need to click **Connect Spotify** once more
> (no retyping — your keys are already there). SoundCloud has no such step.

### Railway

1. Click: **[Deploy on Railway](https://railway.com/new)**
2. Choose **Deploy from GitHub repo** and select this repository.
3. Railway reads `railway.json` + `Dockerfile` and builds it.
4. Under **Settings → Networking**, click **Generate Domain** to get a public URL
   like **`https://sub-scraper-production.up.railway.app`**.
5. Under **Variables**, add the keys you use — `SPOTIFY_CLIENT_ID`,
   `SPOTIFY_CLIENT_SECRET`, `SOUNDCLOUD_USERNAME`, `SOUNDCLOUD_AUTH_TOKEN` — so
   they persist. (Credentials typed into the in-app Settings page don't survive a
   redeploy.) Then open your URL.

### Keep it auto-updating on every push

Both platforms redeploy automatically whenever you push, so your hosted link
always runs the latest version — nothing to configure:

- **Render:** `render.yaml` sets `autoDeploy: true`, so Render rebuilds on every
  push to the connected branch. (An instance deployed *before* this setting was
  added needs **one** manual redeploy to adopt it — service → **Manual Deploy →
  Deploy latest commit** — after that it's automatic.)
- **Railway:** automatic once the repo is connected — nothing else to do.

> Prefer to drive Render deploys from CI instead? Copy your service's **Deploy
> Hook** (service → Settings → Deploy Hook) and add it to GitHub as a secret
> named **`RENDER_DEPLOY_HOOK`** (repo → Settings → Secrets and variables →
> Actions). The included `.github/workflows/ci.yml` pings it after tests pass.
> With `autoDeploy: true` this is entirely optional.

---

## Important notes for hosted instances

- **Each person needs their own Spotify keys.** The app reads *your* library, so
  whoever uses it must add their own credentials (it takes 2 minutes — see
  [`SETUP.md`](SETUP.md)). Nothing is shared between instances.
- **Downloads land on the server**, not on the visitor's computer. The hosted web
  version is best for browsing libraries and trying the engine. To build and keep
  a real music collection — and to copy it to a **portable HiFi player** — use the
  desktop app (it has the **Device Sync** tab).
- **Spotify login is fully hosted.** The web app has a built-in login: open
  **Settings → Connect your Spotify account**, copy the **Redirect URI** it shows
  (your live URL + `/api/spotify/callback`), add it to your Spotify app's redirect
  URIs, then click **Connect Spotify**. The page walks you through it. SoundCloud
  public likes need only a username — no login step.
- **Keep your instance private** if you put real credentials in it — don't post
  the Settings page publicly with keys saved.

---

Questions about the desktop app instead? See the [README](README.md) and
[`SETUP.md`](SETUP.md).
