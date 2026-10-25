<p align="center"><img src="app/static/icons/icon-192.png" width="96" alt="Faceoff logo"></p>

# Faceoff

**Faceoff** is a self-hosted, mobile-first web app that hides faces in photos. Each face can be blurred with a tight, feathered oval or covered with a solid black box. The rest of the image is kept. Every processed photo is written automatically to an export folder of your choice.

The same app also has a **Docs** tool that blacks out keywords, phrases, and common personal details (names, money, addresses, emails, phone numbers, locations) in PDFs and in photos of text. Face detection and document redaction are separate tools: Docs does not look for faces, and the photo Blur/Box flow does not search for words.

Creators building AI or art reference libraries use it to collect reference images (including optional Instagram saves), redact faces, keep the rest of the image, and drop the clean refs into a library or export folder. Built for reference workflows — pull saves, redact faces, drop clean refs into your library.

Instagram sync is **extension-only**. The optional browser extension, **Faceoff Sync**, copies the posts you save on Instagram into Faceoff's **Saved** library, so you can pick which ones to redact. The extension runs in your own logged-in browser. The Faceoff server never talks to Instagram and never accepts a session cookie or sessionid. Photos can be redacted; videos stay raw. Automated access may be against Instagram's terms — use the extension at your own risk, or skip it. Faceoff works without it.

Faceoff runs in one Docker container (FastAPI + OpenCV + SQLite). It works on a Raspberry Pi 4/5 (arm64) and on any x86-64 machine. The UI is plain HTML/CSS/JS, with no front-end build step.

---

## Contents

- [Use cases](#use-cases)
- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration) (env vars and volumes)
- [Faceoff Sync browser extension](#faceoff-sync-browser-extension)
- [Using Faceoff](#using-faceoff)
- [Keyword redaction (Docs)](#keyword-redaction-docs)
- [Build from source](#build-from-source)
- [API overview](#api-overview)
- [Security and privacy notes](#security-and-privacy-notes)
- [Tests](#tests)
- [Project layout](#project-layout)
- [License and credits](#license-and-credits)

---

## Use cases

- **AI and art reference libraries.** Collect reference images (including optional Instagram saves), redact faces, keep the rest of the image, and drop the clean refs into your library or export folder. Saved videos stay raw and are not redacted.
- **Document redaction.** Black out phrases you type, or tick presets for names, money, addresses, emails, phone numbers, and locations, in a PDF or a photo of a page. The flattened copy exports beside your other files. This does not replace face blur. Presets are patterns and short lists, not a full reading of the page.

The same blur-or-box export is what the app does for any photo you process. Originals stay in the private data folder; only the redacted copies are written to the export folder.

## Features

- **Blur or Box.** Choose per upload or per selection, and redo an item in the other mode later. Blur strength, padding (0–30%) and detection sensitivity (Low/Medium/High) are adjustable in Settings.
- **Docs (keyword and preset redaction).** A separate tab. Tick presets (names, money, addresses, emails, phone numbers, locations) and, if you want, type extra phrases. Faceoff covers each match with a solid black box. Originals stay in the private data folder. The redacted copy is written to the same export folder as photos. See [Keyword redaction](#keyword-redaction-docs) for formats and accuracy limits.
- **Accurate face detection.** Uses OpenCV's **YuNet** detector in several passes: a downscaled pass for close-ups, a mid-resolution pass, and high-resolution tiles for small faces in crowds. Boxes are mapped back to full resolution, and low-confidence large hits are checked again to cut down pet and object false positives. EXIF orientation is applied, and EXIF/GPS metadata is stripped from the output.
- **Bulk upload.** Pick many photos on a phone, or drag and drop files or whole folders (subfolders included) on desktop. Non-images and system files are skipped. Uploads are chunked with automatic retry, and a background queue processes them (2 workers by default).
- **Auto-export.** Every processed photo is written to the export folder you mount. Names look like `20261023-173901_upload_IMG_1234_blur.jpg`, with optional `YYYY-MM/` sub-folders and an optional source-folder prefix. Writes are atomic, with configurable permissions and owner (`PUID`/`PGID`, `UMASK`). This works well with a network share.
- **Gallery.** Shows the latest 100 results (configurable). Tap a photo to flip between before and after, or open the viewer for a before/after slider, redo, or remove. Older items drop out of the gallery, but **exported files are always kept**.
- **Saved library.** Holds Instagram saves sent by the extension. Items are stored **unprocessed**, with no size limit and infinite scroll.
  - Filters: Photos/Videos and Unprocessed/Processed/Working. Sorts: newest, oldest, or by account. There is also a **By user** view listing every account you've saved from.
  - Multi-select (tap *Select*, long-press, or Shift/Ctrl-click), then **Blur**, **Box**, **Save** or **Delete**.
  - Processed items also appear in the Gallery with an `@account` badge, and their output is exported like any upload.
- **Video feed.** Saved videos play in a vertical, swipeable, Reels-style feed with autoplay, mute memory, tap-to-pause, and per-account filtering. HTTP Range is supported, so seeking works on iOS Safari. Videos are kept as-is and never redacted.
- **Save to device.** A ⬇ Save button sits on Saved tiles, Gallery tiles, the viewer and the video feed.
  - Android and desktop: a normal download, named like `@account_SHORTCODE.mp4`.
  - iPhone over HTTPS: a share sheet that offers *Save Video / Save Image* (to Photos) or *Save to Files*.
  - iPhone over plain HTTP: the file goes to Files › Downloads.
- **Optional raw copy of Instagram saves.** Saves can also be copied to a separate folder as `<account>/<YYYY-MM-DD>_<account>_<shortcode>_<n>.<ext>`.
- **Installable PWA.** Dark UI with an animated "faceless" logo (static when *reduce motion* is on). It has bottom navigation and supports iPhone safe areas.
- **Optional password** (HTTP basic auth).

## Requirements

- **Docker** with the Compose plugin (`docker compose`).
- About 1 GB RAM for the container (the default `mem_limit`), plus disk space for your photos.
- **Raspberry Pi:** Pi 4 or 5 on a **64-bit** OS (arm64). `python:3.12-slim` and `opencv-python-headless` publish arm64 wheels, so the image builds natively on the Pi. The first build takes a few minutes. Keep `WORKERS=2` on a Pi 4.
- **x86-64:** any Linux, macOS or Windows host with Docker.
- Internet access **at build time** to download Python packages, the YuNet model, and Tesseract's English data (used only by Docs). No outbound access is needed at runtime.
- For the extension: a Chromium browser (Chrome, Brave, Edge, …) on a computer.

## Quick start

```bash
git clone https://github.com/Unaidedcoyote10/faceoff.git
cd faceoff
cp .env.example .env          # then edit paths / PUID / PGID (see Configuration)
mkdir -p data export          # or point DATA_HOST_DIR / EXPORT_HOST_DIR elsewhere
docker compose up -d --build
```

Open `http://<your-server>:8088` (or the port you set in `FACEOFF_PORT`). On a phone, use *Add to Home Screen* to install it as an app.

To update after pulling new code, run `docker compose up -d --build`. Your data and exports live in the mounted folders and are not affected.

### HTTPS (recommended for phones)

Faceoff serves plain HTTP on its port. Put it behind any HTTPS reverse proxy (Caddy, Traefik, nginx, or your VPN's built-in HTTPS proxy) if you want:

- iPhone **Save to Photos** (iOS only allows the share sheet on HTTPS pages),
- a cleaner PWA install,
- or access beyond your own network. In that case, also set `APP_PASSWORD`.

## Configuration

All host-specific settings go in `.env`, which `docker-compose.yml` reads. Nothing about your machine is hard-coded in the image.

### Volumes

| Container path | Host path from `.env` | Required | What it holds |
|---|---|---|---|
| `/data` | `DATA_HOST_DIR` (default `./data`) | **yes** | SQLite DB, originals, previews, thumbnails, the Saved library, settings. Private; keep it off shared folders. |
| `/export` | `EXPORT_HOST_DIR` (default `./export`) | **yes** | Processed (blurred/boxed) photos are written here automatically. Can be a network share. |
| `/export-ig` | `IG_EXPORT_HOST_DIR` (default `./export-ig`) | no | Raw copies of Instagram saves, only when `IG_EXPORT_ENABLED=1`. |

Paths containing spaces are fine; quote them in `.env` (`EXPORT_HOST_DIR="/srv/photos/My Drop"`).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `FACEOFF_PORT` | `8088` | Host port the web UI is published on. |
| `TZ` | `UTC` | Time zone used for timestamps in export file names. |
| `DATA_HOST_DIR` | `./data` | Host folder mounted at `/data`. |
| `EXPORT_HOST_DIR` | `./export` | Host folder mounted at `/export` (processed output). |
| `EXPORT_LABEL` | *(empty)* | How the export folder is described in the UI, e.g. `'\\nas\Photos\Faceoff'`. Display only. |
| `PUID` / `PGID` | `1000` / `1000` | User and group the container runs as, and so the owner of exported files. Match the owner of your export folder or share (`stat -c '%u:%g' /path`). |
| `UMASK` | `002` | Umask for created folders (group-writable by default). |
| `EXPORT_FILE_MODE` | `664` | Permission bits for exported files. |
| `WORKERS` | `2` | Photos processed in parallel. |
| `CV_THREADS` | `2` | OpenCV threads per worker. |
| `MEM_LIMIT` | `1g` | Container memory limit. |
| `DEFAULT_MODE` | `blur` | `blur` or `box`. Initial default only; change it later in Settings. |
| `DEFAULT_PADDING` | `0.08` | Extra margin around each face (0–0.30). Initial default. |
| `DEFAULT_SENSITIVITY` | `medium` | `low`, `medium` or `high`. Initial default. |
| `APP_PASSWORD` | *(empty)* | If set, the UI and API require HTTP basic auth. The pairing token still protects extension endpoints. |
| `APP_USER` | *(empty)* | Optional required username for basic auth. Any username is accepted if empty. |
| `IG_EXPORT_ENABLED` | `0` | `1` copies raw Instagram saves to `/export-ig`. |
| `IG_EXPORT_HOST_DIR` | `./export-ig` | Host folder mounted at `/export-ig`. |
| `IG_EXPORT_LABEL` | *(empty)* | How the IG folder is described in the UI. Display only. |

Advanced variables can be set in the `environment:` section of `docker-compose.yml`; they have sensible defaults in the image.

| Variable | Default | Description |
|---|---|---|
| `GALLERY_LIMIT` | `100` | Number of results kept in the Gallery. |
| `MAX_UPLOAD_MB` | `40` | Per-file upload limit. |
| `AUTO_EXPORT` | `1` | Initial value of the auto-export setting. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `DATA_DIR` / `EXPORT_DIR` / `IG_EXPORT_DIR` | `/data` / `/export` / `/export-ig` | Container-side paths. Change only if you change the mounts. |
| `YUNET_MODEL` | `/app/models/face_detection_yunet_2023mar.onnx` | Path to the face-detection model. |
| `DOC_MAX_PAGES` | `40` | Page cap for keyword redaction. |
| `DOC_DPI` | `144` | Resolution used when a redacted PDF is flattened. |
| `DOC_OCR` | `1` | `0` turns off Tesseract. Text PDFs still work. |
| `DOC_OCR_MAX_SIDE` | `2200` | Longest side, in pixels, for an OCR pass. |

Everything else (blur strength, export sub-folders and prefixes, extension pacing, pause state) is set in the **Settings** tab and stored in `/data`.

## Faceoff Sync browser extension

The extension (Manifest V3) is in [`extension/`](extension/). The server also serves it as a zip under **Settings → Browser extension → Download extension (.zip)**.

### Install (load unpacked)

1. Download the zip from Faceoff Settings, or build it yourself (`python3 scripts/build_extension.py`, output in `dist/faceoff-sync.zip`). You can also use the `extension/` folder directly.
2. Extract the zip. You get a `faceoff-sync/` folder.
3. Open `chrome://extensions` (or `brave://extensions` / `edge://extensions`) and turn on **Developer mode**.
4. Click **Load unpacked** and choose the `faceoff-sync` folder. Pin the extension if you like.

### Pair it with your server

1. In Faceoff, go to **Settings → Browser extension → Generate pairing token**. The token is shown once and can be rotated or revoked at any time.
2. The extension's options page opens on install. Enter **your Faceoff URL** (for example `http://my-server:8088`; there is no built-in default) and paste the token.
3. Click **Save & test**. The browser asks for permission to reach that one address. Accept it.
4. Sync starts **paused**. When you're ready, press **Resume** in Faceoff Settings.

### How syncing works (and how it stays gentle)

- The extension only makes Instagram requests while an instagram.com tab is **visible and focused**. It never opens tabs by itself. Requests are same-origin calls from that tab, the same ones instagram.com makes.
- **Before every Instagram request** it checks Faceoff's **pause flag** (`GET /api/ext/control`). If the server is paused or unreachable, it does nothing.
- **New saves:** the first run only records what's already saved. After that it checks the first page every 3–6 hours, with jitter.
- **Backfill** of older saves is **off** unless you enable it in Settings. When on, it takes one page every 45–120 s, a 20–40 min break every 10 pages, and at most 40–50 pages per 24 h. Media downloads happen one at a time.
- On any warning from Instagram (rate limit, "automated behaviour", challenge, login wall, unexpected response), it **stops for 48 h or more**. It also reports the warning, which **pauses Faceoff server-side** until you resume it.
- Faceoff dedupes by `shortcode:index`, so nothing is sent twice.

Use it at your own risk. Automated access may be against Instagram's terms; the conservative pacing above is deliberate.

## Using Faceoff

- **Upload tab:** pick photos or a folder, choose Blur or Box, and watch the queue. Results land in the Gallery and the export folder. This tab is photos only.
- **Docs tab:** tick the presets (names, money, addresses, emails, phone numbers, locations) and, if you want, add custom keyword lines (one phrase per line). Faceoff blacks those out in a PDF or a photo of text. This does not blur faces. See below.
- **Gallery:** tap to flip before/after, use ⤢ for the viewer and slider, ⬇ to save to the device, or *Redo as box/blur*.
- **Saved tab:** browse Instagram saves by post or by account. *Select* items, then Blur / Box (photos), Save, or Delete. *▶ Watch as feed* opens the video feed.
- **Settings:**
  - redaction defaults and export options;
  - extension pairing, pause/resume, and backfill toggle (Instagram sync is extension-only);
  - an optional raw IG copy.

### Instagram sync is extension-only

Saved posts reach Faceoff only through **Faceoff Sync**. There is no cookie paste and no server-side Instagram login: the server never accepts a session cookie or sessionid. Generate a pairing token under **Settings → Browser extension**, install the extension, and press **Resume** when you are ready. The Saved library is unchanged. On startup, Faceoff deletes a leftover `ig_cookie.json` in the data folder if one is still there from an older version. It does not read that file.

## Keyword redaction (Docs)

Docs is a second tool in the same app. It does not run face detection, and the photo Blur/Box settings (padding, sensitivity, blur strength) do not apply to it. Instagram sync is unchanged: the extension notes above still apply to the Saved library, and Docs never talks to Instagram.

### Presets and custom lines

The Docs tab has six tick boxes and a text box. Whatever you tick and whatever you type are combined for that file. Leave both empty and the upload is rejected. Either one is enough.

Custom lines are still whole words and phrases, one per line, case-insensitive, up to 40 lines of 80 characters. `cat` does not match `category`. A phrase split by a line break is often missed.

Presets do not use a large language model. Emails, phone numbers, and money are regular expressions. Names, addresses, and locations are short rules plus a fixed word list. That keeps the container small enough for a Pi, and it means the presets are wrong sometimes. Review every page.

| Preset | What it tries to cover | What it misses, and what it may cover by mistake |
|---|---|---|
| **Names** | A title plus a name (`Dr. Jane Smith`, `Mr John Smith`), a labeled field (`Name: Ada Lovelace`, `Patient: Dr. Jane Smith`), or a common given name plus a capitalized surname (`Jane Smith`, `Maria de la Cruz`). | Unusual names, a single name with no title or label, all-lowercase text, and a name split across two lines. A title plus an ordinary capitalized word is covered on purpose of the rule (`Dr. Pepper`). There is no census and no neural name model. |
| **Money** | `$1,234.56`, `€99`, `£50`, `EUR 50`, `USD 1,200`, `50 dollars`, `($1,234.56)`. | An amount with no currency symbol or word. `1.234,56 €` (dot as the thousands separator, comma as the decimal) is often missed. |
| **Addresses** | A US-style street line (`742 Evergreen Terrace`, `10 N. Main St.`), a PO Box, and a `City, ST 12345` line. | A street with no suffix (`123 Main`), most addresses outside the US, and a street on one line with the city on the next. Each line is judged on its own. |
| **Emails** | `jane@example.com`, including a space around `@` that OCR sometimes inserts. | `name at domain dot com`. An address that OCR splits into pieces it cannot rejoin. |
| **Phone numbers** | US numbers that already have separators (`(415) 555-2671`, `415-555-2671`, `415 555 2671`) and international numbers that start with `+` (`+44 20 7946 0958`). | A bare run of digits. Those are left alone on purpose so an account number is not treated as a phone. A local number with no area code (`555-2671`) is missed. |
| **Locations** | A fixed list of countries, US states (full names), Canadian provinces, and major cities (`Chicago`, `New York`, `London`), plus a `City, ST ZIP` line. `Chicago, IL` is covered because Chicago is on the list. | Small towns, most places that are not on the list (`Springfield, IL` with no ZIP), lowercase text, and two-letter abbreviations sitting on their own. `or` and `IN` are not treated as places. A few country names that are also personal names are omitted (Jordan, Chad, Israel, Turkey). Names that are also places can be covered (`Georgia`, `Sydney`). |

The result note counts presets by category (`Emails ×1`). It does not repeat the address or number that was found.

### What you can upload

| Input | How matches are found |
|---|---|
| **PDF with selectable text** (you can highlight the words) | PDFium reads the text layer and matches whole words and phrases. Case-insensitive. |
| **Scanned PDF** (no text layer) | The page is rendered and read with **Tesseract OCR**, English only. |
| **Image of text** — JPEG, PNG, WEBP, HEIC, TIFF, BMP | Same English OCR pass. |

A page that already has selectable text is not also OCR'd. Words that exist only inside a picture on that page are missed.

### What you get

- Each match is covered with a **solid black box**.
- The original upload is stored under the private data folder and is not modified.
- A new file is written to the same export folder as photos (including `YYYY-MM/` sub-folders when that setting is on). Names look like `20261025-204500_doc_report_redact.pdf`. The keywords are not put in the file name.
- PDF output is a **flattened image PDF**: the covered words are not left behind as text you can select or copy. The rest of the page is pixels too, so links, form fields, and the original metadata are not kept. Image uploads come back as JPEG, or PNG when the upload was PNG or WEBP.

### Limits (read these)

This is a practical first cut for a Pi-friendly app, not a certified or forensic redaction tool. Review every output.

- **OCR is imperfect.** Tesseract's English model misses handwriting, rotated or skewed pages, tiny type, stylized fonts, and text that is not English. A match it misses stays visible.
- **Phrases and preset matches should sit on one line.** A phrase split across a line break is often missed. `cat` does not match `category` (whole words only). The black box is padded so glyph edges do not peek out, which can also cover a very short word sitting right against a match.
- **Presets are not a complete PII pass.** See the table above. Names, addresses, and locations in particular will miss real details and will sometimes black out an ordinary word. Emails, phones, and money only match the patterns in that table.
- **Page cap.** PDFs longer than `DOC_MAX_PAGES` (40 by default) are rejected so a small machine can finish the job. One document is processed at a time. Render size is capped (`DOC_DPI`, `DOC_OCR_MAX_SIDE`) so memory stays modest. There is no large neural OCR model.
- **Password-protected PDFs** are rejected.
- **Not a full sanitizer.** Flattening removes the text layer of the pages we rewrite, but this does not promise that every hidden copy of a word is gone (for example, text OCR failed to see). Do not treat it as legally sufficient redaction.
- **JPEG output** is visually black over the match. Very small compression traces can remain at the edge of a box; the box is padded so the glyphs themselves are covered. It is still not a pixel-forensic wipe.
- Face Blur/Box and Docs do not run on the same upload. A photo sent to Docs is only searched for words. A photo sent to Upload is only searched for faces.

### Manual check

1. Open **Docs**. Confirm Upload still offers only Blur / Box for photos.
2. Enter a keyword that appears in a text PDF, and tick one preset (for example Emails) if the file also has an address you did not type. Upload it, and wait until the card shows a match count. The note should list preset totals without quoting the address back at you.
3. Download the PDF and try to select text: the covered word should be a black bar, and the original file on disk should be unchanged.
4. Confirm a new `*_doc_*_redact.pdf` appeared in the export folder, and that the Gallery did not gain the document.
5. Optional: photograph a printed line, or upload a scan, and expect OCR to miss some words.

## Build from source

The Docker image is the reference build:

```bash
docker compose build                      # or: docker build -t faceoff .
# multi-arch (e.g. build on x86 for a Pi):
docker buildx build --platform linux/arm64,linux/amd64 -t faceoff .
```

The [`Dockerfile`](Dockerfile):

1. Installs Tesseract OCR (English) and `requirements.txt` on `python:3.12-slim`.
2. Downloads the YuNet model from the OpenCV Zoo and checks that it loads.
3. Copies `app/`, `extension/` and `tests/`.
4. Runs as an unprivileged user with a health check on `/api/health`.

### Run without Docker (development)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Docs OCR (optional for text PDFs, required for scans and photos of text):
#   sudo apt install tesseract-ocr tesseract-ocr-eng
./scripts/get_model.sh                    # downloads models/face_detection_yunet_2023mar.onnx
mkdir -p data export
DATA_DIR=./data EXPORT_DIR=./export uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

### Rebuild the extension zip

```bash
python3 scripts/build_extension.py        # -> dist/faceoff-sync.zip (top folder faceoff-sync/)
```

There is no JS build step: edit files in `extension/` and reload the extension in `chrome://extensions`. Bump `version` in `extension/manifest.json` when you change it.

### Regenerate the logo / icons

`tools/mark.py` holds the logo geometry. `python3 tools/make_icons.py` renders the PWA icons, favicon and extension icons; it needs Playwright and Chrome. The animated header logo is inline SVG + CSS in `app/static/index.html` and `style.css`.

## API overview

All endpoints are under the same origin as the UI. If `APP_PASSWORD` is set, everything except the health check, icons and the extension endpoints requires basic auth. Extension endpoints require the `X-Faceoff-Token` header.

| Area | Endpoints |
|---|---|
| Health | `GET /api/health` |
| Upload and gallery | `POST /api/upload` (multipart), `GET /api/images`, `GET /api/images/{id}`, `GET /api/images/{id}/file/{thumb-before\|thumb-after\|before\|after\|full}`, `POST /api/images/{id}/reprocess`, `DELETE /api/images/{id}` |
| Docs (keywords and presets) | `POST /api/docs` (multipart: `file`, `keywords` one phrase per line, `presets` comma-separated: `names,money,addresses,emails,phones,locations`), `GET /api/docs`, `GET /api/docs/{id}`, `GET /api/docs/{id}/file/{preview\|download}`, `DELETE /api/docs/{id}` |
| Settings | `GET /api/settings`, `POST /api/settings` |
| Saved library | `GET /api/saved` (filter, sort, kind, owner, paging), `GET /api/saved/owners`, `GET /api/saved/ids`, `GET /api/saved/batch?ids=`, `GET /api/saved/{id}`, `GET /api/saved/{id}/file/{thumb\|original\|download}` (Range supported), `POST /api/saved/process`, `POST /api/saved/delete` |
| Extension (token) | `GET /api/ext/control` (pause flag and pacing limits), `POST /api/ext/report` (warning → server pause), `GET /api/ext/ping`, `POST /api/ingest/known`, `POST /api/ingest` (multipart: `meta` JSON + `file`) |
| Extension admin (UI) | `POST /api/ext/token` (rotate), `DELETE /api/ext/token` (revoke), `POST /api/ext/pause`, `GET /api/ext/download` |

While sync is paused, `POST /api/ingest` and `/api/ingest/known` return `423 Locked`.

## Security and privacy notes

- **Extension-only Instagram sync.** The server never contacts Instagram and never accepts a session cookie or sessionid. The extension never reads or sends cookies; it uses the logged-in tab's own same-origin requests. Media goes from Instagram's CDN to your browser to your Faceoff server, and nowhere else.
- **Pairing token.** A random token authorizes the extension and is stored server-side in `/data`. Rotate or revoke it in Settings; revoking it instantly cuts the extension off.
- **Server-side pause flag.** One switch in Settings stops all extension activity before its next request. Warnings reported by the extension flip it automatically. New installs start paused.
- **Metadata.** Exported photos have EXIF/GPS stripped. Originals stay in `/data`, so treat that folder as private.
- **Exposure.** Faceoff is built for a private network. If you expose it, put it behind HTTPS and set `APP_PASSWORD`.
- **Video is never redacted.** Only photos are processed by face detection. Keyword redaction accepts PDFs and still images, and writes a new file; it does not change that rule.
- **Docs output.** Keyword jobs keep the upload in `/data` and write a separate flattened copy to the export folder. Treat `/data` as private. The Docs tool is not a certified sanitizer; see [Keyword redaction](#keyword-redaction-docs).

## Tests

The tests use fixtures and local mocks only, with no real accounts or network access.

| Test | What it checks | How to run |
|---|---|---|
| `tests/ingest_test.py` | Pairing and token auth, CORS, the pause flag (423 while paused, auto-pause on report), ingest → Saved, dedupe, raw IG export layout, thumbnails, Range/416, download attachment headers, processing → export folder, gallery integration and pruning | `DATA_DIR=/tmp/fo EXPORT_DIR=/tmp/fo/x IG_EXPORT_ENABLED=1 IG_EXPORT_DIR=/tmp/fo/ig GALLERY_LIMIT=3 python -m tests.ingest_test` |
| `tests/docs_redact_test.py` | Keyword parsing, preset detection (email, phone, money, address, name, location) and its known misses, whole-word PDF redaction (text layer removed, kept words still visible), presets combined with a custom line, scanned-page OCR when Tesseract is installed, export filename, original bytes untouched, Docs kept out of the photo gallery | `python -m tests.docs_redact_test` |
| `tests/ext/e2e_extension.py` + `tests/ext/mock_ig.py` | Loads the unpacked extension in Chromium against a local HTTPS mock of instagram.com and its CDN. Covers zero requests while paused, baseline, the 3–6 h schedule, backfill gated by the server, a single paced page, and a 429 triggering a 48 h stop plus server auto-pause. | start Faceoff on `127.0.0.1:8088`, run `python3 tests/ext/mock_ig.py`, then `python3 tests/ext/e2e_extension.py` (needs Playwright + Chromium; the mock creates its own throwaway TLS cert) |
| `tests/*_shots.py` | Phone-width UI checks with Playwright (feed, by-user, save, logo, folder drop) against a running instance with some data | `python3 tests/save_shots.py http://localhost:8088 /tmp/shots` |

## Project layout

```
app/            FastAPI app (main.py), face redaction (redact.py), document redaction (keywords.py, presets.py, docwork.py),
                queue (worker.py), SQLite store (store.py), Saved library (saved.py), static UI (static/)
extension/      Faceoff Sync MV3 extension (background.js, content.js, popup, options, icons)
scripts/        get_model.sh (YuNet download for local dev), build_extension.py (zip)
tools/          logo geometry + icon generator
tests/          tests, mocks and public-domain sample images
Dockerfile, docker-compose.yml, .env.example
```

## License and credits

- Faceoff is released under the [MIT License](LICENSE).
- Face detection uses **YuNet** from the [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT License). The model is downloaded at build time and not stored in this repository.
- Document redaction uses [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) / PDFium (Apache-2.0 or BSD-3-Clause) and, for scans and photos, [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache-2.0) via pytesseract. English trained data is installed in the image. Presets are regular expressions and a fixed word list shipped in `app/presets.py`. No extra model download.
- Test images (`tests/samples/`) are public-domain historical photographs from Wikimedia Commons: Albert Einstein (1921), Abraham Lincoln (1863), and the 1927 Solvay Conference. `tests/video/clip4s_small.mp4` is a synthetic test pattern.
