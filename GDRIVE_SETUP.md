# Google Drive Sync — Setup Guide

When enabled, Sub-Scraper uploads every track to your Google Drive right
after it finishes downloading to your computer. This is the bridge for
getting your music onto another device (e.g. your phone) without a cable.

There are two halves: get a credentials file from Google (one time), then
point the app at it.

---

## Part A — Get `credentials.json` from Google

You can do this entirely in a phone browser and save the file for later.

1. **Create a project**
   Go to <https://console.cloud.google.com>, sign in, and create a new
   project. Name it `Sub-Scraper`.

2. **Enable the Drive API**
   Left menu → **APIs & Services → Library**. Search **"Google Drive API"**,
   open it, click **Enable**.

3. **Configure the consent screen**
   **APIs & Services → OAuth consent screen**.
   - User type: **External** → Create
   - App name: `Sub-Scraper`; fill the support email and developer email
     with your own address
   - On the **Test users** step, click **Add Users** and add your own Gmail
     address. *This step is required* — without it Google blocks the login.
   - Save through to the end.

4. **Create the OAuth client**
   **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Desktop app** (must be this — the app uses a local
     loopback login flow)
   - Name it anything → **Create**
   - Click **Download JSON**. That downloaded file is your `credentials.json`.

5. **Save the file** somewhere you can reach from your computer later
   (Google Drive, a password manager, email to yourself).

---

## Part B — Turn it on in the app

On the computer where you run Sub-Scraper:

1. Open the app → **Settings** tab → **Google Drive** section.
2. Tick **Enable Google Drive Sync**.
3. Next to **credentials.json Path**, click **Browse** and select the file
   you saved in Part A.
4. (Optional) **Folder ID** — see the note below.
5. Click **Save Settings**.

The first time a download completes, your browser opens asking you to log in
to Google and approve access. Because the app is in "testing" mode you will
see an **"unverified app"** warning — click **Advanced → Go to Sub-Scraper
(unsafe)** and approve. This happens only once; the login is then cached at
`~/.sub_scraper/gdrive_token.pkl`.

---

## About the "Folder ID" field

The app requests the minimal-permission `drive.file` scope, which means it
can only manage files **it creates** — it cannot see folders you made by
hand in Drive.

- **Leave Folder ID blank (recommended):** uploads land in the top level of
  *My Drive*. Simplest, always works.
- **Use a Folder ID:** this only works reliably for a folder the app itself
  created. Pasting the ID of a folder you made manually will usually fail
  with a "File not found" error because of the limited scope. The ID is the
  last part of a folder's URL:
  `drive.google.com/drive/folders/`**`THIS_PART`**

If you want uploads to go into a specific pre-made folder, ask and the scope
can be widened to full `drive` access (requires re-approving the login).

---

## Getting the music onto your phone

Once tracks are in Google Drive, install the **Google Drive** app on your
phone, open the folder, and download the files — or use Drive's offline /
"Available offline" option. Any music player that reads local files will
then see them.
