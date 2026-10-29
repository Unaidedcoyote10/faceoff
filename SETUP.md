# Faceoff setup

This is the short walkthrough for the Faceoff Setup Pack. Faceoff runs on your own machine, in Docker. It blurs or black-boxes faces in photos, and it has a separate **Docs** tab that blacks out words and common personal details in PDFs and photos of text.

Instagram sync is optional and **extension-only**. The Faceoff Sync browser extension copies saves from your own logged-in Instagram tab. The server never talks to Instagram and never accepts a session cookie or sessionid. There is nowhere to paste one. Faceoff works if you skip the extension.

[README.md](README.md) is the full reference: every setting, the preset accuracy table, the API, and the privacy notes. Use that when this page is not enough.

---

## What you need

- **Docker Desktop** (Mac, Windows, or Linux), or **Docker Engine** with the Compose plugin on a 64-bit Raspberry Pi 4 or 5.
- About 1 GB of RAM for the container, plus disk space for photos and exports.
- A browser. The extension, if you use it, needs Chrome, Brave, or Edge on a computer.
- Internet **while the image builds** (Python packages, the face-detection model, and English OCR data). After that, Faceoff does not need outbound access.

---

## 1. Install Docker

### Docker Desktop

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it. Wait until it says it is running.
2. Unzip the Setup Pack. The folder must contain `docker-compose.yml` and `.env.example`.
3. Open Terminal (Mac/Linux) or PowerShell (Windows) in that folder.

### Raspberry Pi

Use a **64-bit** OS (Pi 4 or 5). Install Docker Engine and the Compose plugin, then open a terminal in the unzipped folder. Docker's convenience script is the usual way:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
```

Log out and back in so the group change applies. Confirm with `docker compose version`. Keep `WORKERS=2` on a Pi 4. The first build takes a few minutes.

---

## 2. Set the folders

```bash
cp .env.example .env
mkdir -p data export
```

On Windows PowerShell, the same two steps are `copy .env.example .env` and `mkdir data, export`.

Edit `.env`:

| What to set | Why |
|---|---|
| `EXPORT_HOST_DIR` | Where redacted photos and Docs files are written. Quote the path if it has spaces. |
| `DATA_HOST_DIR` | Private originals, the database, and settings. Keep this off a shared folder. |
| `PUID` and `PGID` | Owner of the exported files. On Linux or a Pi, match the export folder (`id -u` and `id -g`). `1000` is the default and matches the image. |
| `FACEOFF_PORT` | Host port. Default `8088`. |
| `APP_PASSWORD` | Optional. Set it if Faceoff is reachable beyond your own machine. The browser will ask for it when you open the app. |

Leave the Docs lines commented unless you need a different page cap. The image already includes English OCR.

Optional raw copies of Instagram saves (still no cookie, still extension-only): set `IG_EXPORT_ENABLED=1` and `IG_EXPORT_HOST_DIR`. Processed copies still go to the main export folder. Videos are never redacted.

---

## 3. Start

```bash
docker compose up -d --build
```

Open `http://localhost:8088`, or `http://<the-pi-address>:8088` from another device on the same network. On a phone, use *Add to Home Screen*.

To pick up a new pack, unzip over this folder (keep `.env`, `data/`, and `export/`) and run `docker compose up -d --build` again.

Plain HTTP is what Faceoff serves. Put it behind HTTPS if you want iPhone **Save to Photos** or access from outside your network. See [HTTPS](README.md#https-recommended-for-phones) in the README, and set `APP_PASSWORD` if you expose it.

---

## 4. Blur or box a photo

1. Open the **Upload** tab.
2. Choose photos, or a whole folder. You can also drag files or folders onto the page.
3. Pick **Blur** or **Black box**.
4. Wait until the queue finishes. Results show in **Gallery**. The redacted file is also in the export folder. Names look like `20261023-173901_upload_IMG_1234_blur.jpg`.
5. Tap a photo to flip before and after. Open the viewer for the slider, **Redo as box/blur**, or **Download**.

Upload is photos only. It does not search for words. Check the export before you share it: tiny, turned, or poorly lit faces can be missed.

Originals stay in the private data folder. Removing an item from the Gallery does not delete the exported file.

---

## 5. Docs (words, not faces)

**Docs** is its own tab. It does not blur faces, and Blur/Box settings do not apply to it.

1. Tick any presets you want: **Names**, **Money**, **Addresses**, **Emails**, **Phone numbers**, **Locations**.
2. Optionally type custom lines, one phrase per line (`account number`, `OMEGA-9`). Case-insensitive whole words and phrases. Up to 40 lines, 80 characters each.
3. You need at least one tick or one line. If both are empty, the upload is rejected.
4. Choose a PDF or a photo of text (JPEG, PNG, WEBP, HEIC, TIFF, BMP).

Each match is a solid black box. The original stays in the data folder. A new file lands in the same export folder as photos (`*_doc_*_redact.pdf`, or a JPEG/PNG for an image). It does not appear in the Gallery. Download it from the Docs card.

Presets are patterns and short lists, not a full reading of the page. Emails, phone numbers, and money follow shapes such as `jane@example.com` and `$1,234.56`. Names, addresses, and locations miss unusual names, small towns, most non-US addresses, and anything split across two lines, and they sometimes cover an ordinary word. OCR (English only) misses handwriting, tiny type, and rotated pages. Review every page. The limits and the per-preset table are in [Keyword redaction](README.md#keyword-redaction-docs).

---

## 6. Optional: Faceoff Sync

Skip this section if you do not want Instagram saves. Nothing else in the app depends on it.

Faceoff Sync runs in your browser, on a computer where you are already logged into Instagram. It sends photo and video files to your Faceoff server. **The server never takes a cookie.** Do not look for a sessionid box; older builds had one, and this one does not. On startup Faceoff deletes a leftover `ig_cookie.json` in the data folder if it finds one, and it does not read it.

Automated access may be against Instagram's terms. Use the extension at your own risk. Photos in **Saved** can be blurred or boxed later. Videos stay raw.

### Install and pair

1. In Faceoff, open **Settings → Browser extension** and click **Generate pairing token**. Copy the token. It is shown once. **Rotate token** later if you need a new one; the old one stops working immediately.
2. Click **Download extension (.zip)** (or run `python3 scripts/build_extension.py` if you are working from a git checkout; the zip is `dist/faceoff-sync.zip`).
3. Unzip it. You get a `faceoff-sync/` folder.
4. Open `chrome://extensions`, `brave://extensions`, or `edge://extensions`. Turn on **Developer mode**. **Load unpacked** and choose the `faceoff-sync` folder.
5. The options page opens on install. Enter your Faceoff URL (for example `http://192.168.1.20:8088` or `http://localhost:8088`) and paste the token. There is no built-in server address.
6. Click **Save & test**. The browser asks for permission to reach that address, including the one you always use. Choose **Allow**. The options page does not save the URL or the token until that access is granted. If you dismiss the prompt, it tells you to allow access and click **Save & test** again. Sync now cannot call Faceoff without it.
7. Sync starts **paused**. In Faceoff Settings, press **Resume** when you want it to run.

The options page then says it is connected. If it also says sync is paused, that is expected until you press **Resume** in Faceoff Settings. A blank URL left by an older install counts as not set and does not wipe an address you already configured.

### Sync now

**Sync now** is on the extension popup (the toolbar icon), not inside Faceoff.

1. Pairing has to be saved. The button stays disabled, and the status stays **not paired**, until both the Faceoff URL and a pairing token are saved. Until then the result line says `Not paired — open Settings, paste a Faceoff token, Save, and Allow access`.
2. Host access to the Faceoff origin has to be allowed, from the **Save & test** prompt above. Nothing is stored if you dismiss it.
3. Press **Resume** in Faceoff if the card still says **Instagram sync PAUSED**. While it is paused, Sync now does nothing.
4. Open instagram.com and leave that tab **visible** (the page on screen, not only a background tab). Then press **Sync now**.

If **Sync now** still does nothing after an update, reload the unpacked extension from `chrome://extensions` or `brave://extensions` and pair again: new token, your Faceoff URL (a custom address is treated the same as any built-in default), **Save & test**, **Allow**, then a visible instagram.com tab.

If no Instagram tab is visible, the popup result says `Open instagram.com (visible), then press Sync now`. Sync now does not need the tab to be focused. The extension never opens a tab for you.

The first sync only remembers saves that are already there, unless you set **On the very first sync, also send my latest N saves** above 0 (at most 10 are sent). Later checks look for new saves. The popup's auto-check does that every 3–6 hours, and only while you are using instagram.com (the tab visible and focused). You can turn auto-check off on the popup.

**Backfill** of older saves stays off until you turn on **Allow slow backfill** in Faceoff Settings, and only while sync is not paused. It is slow on purpose: one page every 45–120 seconds, a long break every 10 pages, and a daily cap. Start or pause it from the extension popup. Any Instagram warning stops the extension and pauses Faceoff until you resume it. Details are in [Faceoff Sync](README.md#faceoff-sync-browser-extension).

Saved items show on the **Saved** tab, unprocessed. Select photos and choose **Blur** or **Box**. **Watch as feed** plays saved videos. They are not redacted.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| The browser cannot open the page | Docker Desktop is running, and `docker compose ps` shows `faceoff` up. Use the port in `FACEOFF_PORT` (8088 unless you changed it). From a phone, use the computer's LAN address, not `localhost`. |
| The container exits or restarts | `docker compose logs --tail=80`. The first build needs internet. A port already in use means change `FACEOFF_PORT` and run `docker compose up -d` again. |
| Settings says the export folder is not writable | Point `EXPORT_HOST_DIR` at a real folder, create it, and set `PUID`/`PGID` to that folder's owner. Then `docker compose up -d`. |
| Upload never finishes | Large batches are queued (`WORKERS` at a time). On a Pi, leave workers at 2. Non-images are skipped. |
| Docs will not take the file | Tick a preset or add a keyword line. The file must be a PDF or an image of text. Password-protected PDFs are rejected. PDFs longer than 40 pages are rejected unless you raise `DOC_MAX_PAGES`. |
| A word you expected is still visible | Presets and OCR miss a lot. See [Keyword redaction](README.md#keyword-redaction-docs) before assuming the job failed. |
| **Sync now** is greyed out, or the popup says **not paired** | Save both a Faceoff URL and a pairing token, and choose **Allow** on **Save & test**. The button stays disabled until both are saved. After an update, reload the unpacked extension and pair again. |
| **Sync now** says to open Instagram | Leave an instagram.com tab open and visible, then press **Sync now** again. A background tab is not enough. |
| Options page cannot reach Faceoff | Check the URL and port. Click **Save & test** and choose **Allow**. Nothing is stored if you dismiss that prompt. Faceoff has to be running. |
| Popup says paused, or a warning | Press **Resume** in Faceoff Settings only when you mean to. After an Instagram warning, wait before resuming. New installs start paused. |
| You want to paste a session cookie | Do not. This build has no cookie field and will not use one. Pair the extension, or skip Instagram. |
| A saved video is not blurred | Expected. Only photos are redacted. Videos stay raw. |

---

## Where to go next

- [README.md](README.md) — configuration table, how detection and export names work, preset-by-preset limits, API, security.
- **Settings** in the app — blur strength, padding, sensitivity, monthly export folders, pause/resume, backfill.
- Updating: replace the pack files, keep `.env` and your `data/` and `export/` folders, then `docker compose up -d --build`.
