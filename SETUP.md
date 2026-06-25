<div align="center">
  <img src="assets/logo.png" width="90" alt="Sub-Scraper logo"/>

  # Sub-Scraper — Setup Guide

  **No coding. ~10 minutes. Follow the steps in order and you're done.**
</div>

---

> 💡 **The fastest path:** just install and open the app (Part 1). The first time
> it launches, a **built-in wizard** walks you through everything below with
> clickable buttons and a one-click **Copy** for the tricky bits. This document
> is the same walkthrough in writing, with pictures-in-words and troubleshooting.

## What you'll set up

| Thing | Needed for | Difficulty |
| --- | --- | --- |
| **The app itself** | Everything | ⭐ Easy |
| **Spotify keys** | Downloading your Spotify likes & playlists | ⭐ Easy (2 min) |
| **SoundCloud username** | Downloading your **public** SoundCloud likes | ⭐ Easy (30 sec) |
| **SoundCloud token** | SoundCloud **playlists** & **private** likes | ⭐⭐ Medium (3 min) |

You don't need *all* of these — only the parts for the service you want to use.

---

## Part 1 — Install & open the app

First, two free helpers most computers don't have out of the box:

- **ffmpeg** — converts the audio (required).
- **Python 3.9+** — runs the app (macOS/Linux usually already have it).

Pick your system:

### 🍎 macOS

1. Install [Homebrew](https://brew.sh) if you don't have it (paste the one line
   from that page into the **Terminal** app).
2. In Terminal, run:
   ```bash
   brew install ffmpeg python git
   ```
3. Download Sub-Scraper:
   ```bash
   git clone https://github.com/Kariimc/Sub-Scraper.git
   cd Sub-Scraper
   ```
4. Set it up and open it:
   ```bash
   ./setup.sh
   ./run.sh
   ```
   After the first time, you can just **double-click `SubScraper.command`** in
   Finder to open it.

### 🪟 Windows

1. Install **Python** from [python.org/downloads](https://www.python.org/downloads/).
   On the first screen, **tick “Add Python to PATH”**, then click Install.
2. Install **ffmpeg**: open **PowerShell** and run
   ```powershell
   winget install Gyan.FFmpeg
   ```
   (or download it from [ffmpeg.org](https://ffmpeg.org/download.html)).
3. Download Sub-Scraper as a ZIP from GitHub (green **Code** button → **Download
   ZIP**) and unzip it.
4. Open the unzipped folder and **double-click `setup.bat`**, then
   **`run.bat`**.

### 🐧 Linux / 🎮 Steam Deck

1. Install the helpers (Steam Deck / Arch shown; use your distro's package
   manager otherwise):
   ```bash
   sudo pacman -S ffmpeg python git      # Debian/Ubuntu: sudo apt install ffmpeg python3 python3-venv git
   ```
2. Download and run:
   ```bash
   git clone https://github.com/Kariimc/Sub-Scraper.git
   cd Sub-Scraper
   ./setup.sh
   ./run.sh
   ```
   To get a clickable icon in your apps menu, run `./install_desktop.sh`.

> 🎮 **Steam Deck tip:** switch to **Desktop Mode** first (Steam button → Power →
> Switch to Desktop). Run the commands in the **Konsole** app.

✅ **When the app window opens, you're past the hard part.** The setup wizard
appears automatically — keep this guide open alongside it.

---

## Part 2 — Spotify keys (2 minutes)

Spotify needs you to make a free “app” so Sub-Scraper can read *your* library.
You're not publishing anything — it's just a key for yourself.

1. Open the **Spotify Developer Dashboard**:
   👉 https://developer.spotify.com/dashboard
   *(The wizard has an “Open Spotify Developer Dashboard →” button that does this.)*
2. **Log in** with your normal Spotify account. If it's your first time, accept
   the Developer Terms.
3. Click the green **Create app** button.
4. Fill in the form:
   - **App name:** anything, e.g. `Sub-Scraper`
   - **App description:** anything, e.g. `personal`
   - **Redirect URI:** type this **exactly**, then click **Add**:
     ```
     http://127.0.0.1:8888/callback
     ```
     > ⚠️ **This is the #1 thing people get wrong.** It must match *character for
     > character*. Use `127.0.0.1`, **not** `localhost`. In the app's wizard there's
     > a **Copy** button so you can paste it perfectly. Include `http://` and the
     > `/callback` at the end.
   - **Which API/SDKs are you planning to use?** Tick **Web API**.
5. Tick the agreement box and click **Save**.
6. You're now on the app page. Click **Settings** (top-right).
7. Copy your **Client ID** (it's shown right there).
8. Click **View client secret** and copy the **Client Secret** too.
9. Paste both into Sub-Scraper (wizard **Step 1**, or **Settings → Spotify**).

That's it for Spotify. 🎉

> 🔐 **First download = one browser login.** The very first time you load your
> Spotify library, a browser tab opens asking you to log in and click **Agree**.
> This happens **once** — after that it's remembered.

---

## Part 3 — SoundCloud

There are two levels, depending on what you want.

### 3a. Just your **public** liked songs (easy)

All you need is your **username** — the part after `soundcloud.com/` in your
profile link.

> Example: if your profile is `soundcloud.com/dj-awesome`, your username is
> **`dj-awesome`**.

Put it in the wizard **Step 2** (or **Settings → SoundCloud → Username**).

> Make sure your likes are **public**: SoundCloud → Settings → *“Likes”* set to
> public. If they're private, you'll need the token below.

### 3b. **Playlists** & **private** likes (need a token)

To see your playlists or private likes, Sub-Scraper needs a **token** — a
temporary key your browser already has after you log into SoundCloud. Here's how
to grab it. (Do this in a **desktop web browser**, logged into SoundCloud.)

#### Easiest method — the cookie

1. Go to **[soundcloud.com](https://soundcloud.com)** and make sure you're
   **logged in**.
2. Open your browser's developer tools:
   - **Chrome / Edge / Brave:** press `F12` (or right-click the page → **Inspect**).
   - **Firefox:** press `F12`.
   - **Safari:** first enable it — Safari → Settings → Advanced → tick “Show
     features for web developers”. Then right-click → **Inspect Element**.
3. Find the **cookies**:
   - **Chrome/Edge/Brave:** click the **Application** tab → on the left under
     **Storage → Cookies**, click **`https://soundcloud.com`**.
     > 💡 **Don't see an "Application" tab?** The panel is too narrow to show all
     > the tabs — click the **`»`** at the right end of the tab row and pick
     > **Application** from the little menu. (Widening the panel works too.)
   - **Firefox:** click the **Storage** tab → **Cookies** → `https://soundcloud.com`.
4. In the list, find the row named **`oauth_token`**.
5. **Double-click its Value and copy it.** It looks like
   `2-123456-7890123-AbCdEfGhIjKlMn`.
6. Paste it into the wizard **Step 2 → Auth Token** (or **Settings → SoundCloud
   → Auth Token**).

#### Alternative method — the network request

If you can't find the cookie:

1. Logged into SoundCloud, open dev tools (`F12`) and click the **Network** tab.
2. In the filter box type `api-v2`.
3. Click anything on the SoundCloud page (e.g. play a track) so requests appear.
4. Click any request to `api-v2.soundcloud.com`.
5. Scroll to **Request Headers** and find:
   ```
   Authorization: OAuth 2-123456-7890123-AbCdEfGhIjKlMn
   ```
6. Copy **only the part after `OAuth `** (the `2-…` value) and paste it into the
   Auth Token field.

> ℹ️ **The token can expire** (e.g. if you log out of SoundCloud or after a
> while). If playlists stop loading, just grab a fresh token the same way and
> paste it in again.

---

## Part 4 — Choose where music is saved & finish

In the wizard **Step 3** pick a **download folder** (default is your Music
folder) and your **format/quality** (MP3 320k is a great default). Click
**Launch →** and you're in.

To download: load your library, tick the tracks you want (or **shift-click** a
range, or **Download All**), and go. Already-downloaded songs hide themselves so
you only ever see what's left.

---

## Troubleshooting

<details>
<summary><b>Spotify: “INVALID_CLIENT: Invalid redirect URI”</b></summary>

Your redirect URI doesn't match. In the Spotify Dashboard → your app → Settings
→ **Redirect URIs**, it must be **exactly**:

```
http://127.0.0.1:8888/callback
```

No `localhost`, no trailing slash, no `https`. Delete the wrong one, add this
one, **Save**, and try again.
</details>

<details>
<summary><b>“ffmpeg not found” / downloads fail to convert</b></summary>

ffmpeg isn't installed (or isn't on your PATH). Re-do the ffmpeg step in Part 1
for your OS, then **fully close and reopen** the app so it picks up the change.
</details>

<details>
<summary><b>Nothing downloads / every track fails</b></summary>

This is almost always an out-of-date **yt-dlp** (YouTube/SoundCloud change things
often). Sub-Scraper auto-updates it on launch, but you can force it:

```bash
# from the Sub-Scraper folder
.venv/bin/python -m pip install -U yt-dlp     # Windows: .venv\Scripts\python -m pip install -U yt-dlp
```

Then reopen the app.
</details>

<details>
<summary><b>SoundCloud: playlists won't load / “403” / “client_id”</b></summary>

Listing playlists and private likes **requires a token** — see Part 3b. If you
had one and it stopped working, it likely expired; grab a fresh one.
</details>

<details>
<summary><b>The Spotify login browser tab didn't open</b></summary>

Copy the URL the app prints in its log into your browser manually, log in, click
**Agree**, and you'll be redirected. This only happens on the first load.
</details>

<details>
<summary><b>Where are my settings stored?</b></summary>

In `~/.sub_scraper/config.json` (your home folder). You can delete that file to
start the first-run wizard again from scratch.
</details>

---

Need the cloud-backup feature too? See [`GDRIVE_SETUP.md`](GDRIVE_SETUP.md).
Everything else is in the [README](README.md).
