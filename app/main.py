from __future__ import annotations

import base64
import io
import json
import re
import zipfile
import logging
import os
import secrets
import threading
import time

from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import instagram, redact, saved, store, worker

APP_NAME = "Faceoff"
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("faceoff")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
APP_USER = os.environ.get("APP_USER", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
_stop = threading.Event()

OPEN_PATHS = ("/api/health", "/manifest.webmanifest", "/icons/", "/favicon.ico",
              "/api/ingest", "/api/ext/ping", "/api/ext/control", "/api/ext/report")  # extension endpoints use the pairing token instead
EXT_DIR = os.path.join(os.path.dirname(__file__), "..", "extension")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    ext = request.url.path.startswith(("/api/ingest", "/api/ext/ping", "/api/ext/control", "/api/ext/report"))
    origin = request.headers.get("origin", "")
    cors = {}
    if ext and (origin.startswith("chrome-extension://") or origin == "https://www.instagram.com"):
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "Content-Type, X-Faceoff-Token, Authorization",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Max-Age": "600"}
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
    if APP_PASSWORD and not request.url.path.startswith(OPEN_PATHS):
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(p, APP_PASSWORD) and (not APP_USER or secrets.compare_digest(u, APP_USER))
            except Exception:
                ok = False
        if not ok:
            return Response("Auth required", 401, {"WWW-Authenticate": f'Basic realm="{APP_NAME}"'})
    resp = await call_next(request)
    for k, v in cors.items():
        resp.headers[k] = v
    return resp


@app.on_event("startup")
def startup():
    store.init_db()
    st = instagram.load_state()
    if st.get("status") == "running":
        instagram._set(status="pending", message="Restarted during a check; will retry on schedule.")
    worker.resume()
    threading.Thread(target=instagram.scheduler_loop, args=(_stop,), daemon=True, name="ig-scheduler").start()
    log.info("%s ready. data=%s export=%s workers=%s auth=%s", APP_NAME, store.DATA_DIR, worker.EXPORT_DIR,
             worker.WORKERS, "on" if APP_PASSWORD else "off")


@app.on_event("shutdown")
def shutdown():
    _stop.set()


def _pub(r: dict) -> dict:
    v = int(r.get("processed_at") or 0)
    return {
        "id": r["id"], "created": r["created"], "source": r["source"], "name": r["orig_name"], "mode": r["mode"],
        "status": r["status"], "faces": r["faces"], "boxes": r["boxes"], "error": r["error"],
        "width": r["width"], "height": r["height"],
        "exported": os.path.basename(r["export_path"]) if r.get("export_path") else None,
        "ig": {"shortcode": r["ig_shortcode"], "owner": r["ig_owner"], "caption": (r.get("caption") or "")[:140]}
        if r.get("ig_shortcode") else None,
        "kind": r.get("kind") or "image", "duration": r.get("duration"), "progress": r.get("progress"),
        "v": v,
    }


@app.get("/api/health")
def health():
    return {"ok": True, "app": APP_NAME}


@app.get("/api/images")
def images():
    rows = store.list_images()
    return {"items": [_pub(r) for r in rows], "counts": store.counts(), "limit": store.GALLERY_LIMIT}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), mode: str = Form(""), padding: str = Form(""),
                 sensitivity: str = Form("")):
    s = store.load_settings()
    mode = mode if mode in ("blur", "box") else s["default_mode"]
    pad = float(padding) if padding else None
    sens = sensitivity if sensitivity in ("low", "medium", "high") else None
    ids, rejected = [], []
    for f in files:
        data = await f.read()
        if not data:
            rejected.append({"name": f.filename, "error": "empty"})
            continue
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            rejected.append({"name": f.filename, "error": f"over {MAX_UPLOAD_MB}MB"})
            continue
        name = (f.filename or "upload").replace("\\", "/").lstrip("/")[-200:]
        ids.append(worker.add_image(data, name, "upload", mode, pad, sens))
    return {"ids": ids, "rejected": rejected, "mode": mode}


def _rec(id_: int) -> dict:
    r = store.get_image(id_)
    if not r:
        raise HTTPException(404, "Not found")
    return r


@app.get("/api/images/{id_}")
def image(id_: int):
    return _pub(_rec(id_))


@app.get("/api/images/{id_}/file/{kind}")
def image_file(id_: int, kind: str):
    r = _rec(id_)
    p = store.file_paths(id_)
    is_video = (r.get("kind") or "image") == "video"
    key = {"thumb-before": "thumb_orig", "thumb-after": "thumb_proc", "before": "view_orig", "after": "view_proc",
           "full": "processed_video" if is_video else "processed",
           "video-before": "original" if is_video else None, "video-after": "processed_video" if is_video else None}.get(kind)
    if not key or not os.path.exists(p[key]):
        raise HTTPException(404, "Not ready")
    headers = {"Cache-Control": "private, max-age=31536000, immutable"}
    if kind.startswith("video-"):
        return FileResponse(p[key], media_type="video/mp4", headers=headers)
    if kind == "full":
        ext = ".mp4" if is_video else ".jpg"
        sv = store.saved_by_image(id_) if r.get("source") == "saved" else None
        if sv:
            name = _dl_name(sv["owner"], sv["shortcode"], sv["idx"], f'_{r["mode"]}{ext}')
        elif r.get("ig_shortcode"):
            name = _dl_name(r.get("ig_owner"), r["ig_shortcode"], 1, f'_{r["mode"]}{ext}')
        else:
            base = os.path.splitext(os.path.basename((r["orig_name"] or f"image{id_}").replace(chr(92), "/")))[0]
            base = "".join(ch for ch in base if ch.isalnum() or ch in "._- ")[:60] or f"image{id_}"
            name = f'{base}_{r["mode"]}{ext}'
        headers["Content-Disposition"] = _attachment(name)
        if is_video:
            return FileResponse(p[key], media_type="video/mp4", headers=headers)
    return FileResponse(p[key], media_type="image/jpeg", headers=headers)


@app.post("/api/images/{id_}/reprocess")
async def reprocess(id_: int, request: Request):
    r = _rec(id_)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    mode = body.get("mode") or ("box" if r["mode"] == "blur" else "blur")
    if mode not in ("blur", "box"):
        raise HTTPException(400, "bad mode")
    kw = {"mode": mode, "status": "queued", "error": None}
    if "padding" in body:
        kw["padding"] = max(0.0, min(0.4, float(body["padding"])))
    if body.get("sensitivity") in ("low", "medium", "high"):
        kw["sensitivity"] = body["sensitivity"]
    if body.get("use_defaults"):
        kw["padding"] = None
        kw["sensitivity"] = None
    store.update_image(id_, **kw)
    worker.enqueue(id_, r.get("kind") or "image")
    return {"ok": True, "mode": mode}


@app.delete("/api/images/{id_}")
def delete(id_: int):
    _rec(id_)
    store.delete_image(id_)
    return {"ok": True, "note": "Removed from gallery; exported file kept."}


@app.get("/api/settings")
def get_settings():
    st = store.load_settings()
    tok = st.pop("ext_token", "")
    return {"settings": st, "export": worker.export_dir_status(), "ig_export": worker.ig_export_status(),
            "workers": worker.WORKERS, "auth": bool(APP_PASSWORD), "gallery_limit": store.GALLERY_LIMIT,
            "extension": {"paired": bool(tok), "token_hint": (tok[:4] + "…" + tok[-4:]) if tok else None,
                          **store.ingest_stats(), **_control(st | {"ext_token": ""})}}


@app.post("/api/settings")
async def post_settings(request: Request):
    body = await request.json()
    body.pop("ext_token", None)
    try:
        s = store.save_settings(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    s.pop("ext_token", None)
    return {"settings": s}


@app.get("/api/instagram")
def ig_status():
    return instagram.public_status()


@app.post("/api/instagram/cookie")
async def ig_cookie(request: Request):
    body = await request.json()
    try:
        c = instagram.save_cookie(body.get("cookie", ""))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"Couldn't read that cookie: {e}")
    log.info("instagram cookie updated (%d fields)", len(c))
    return instagram.public_status()


@app.delete("/api/instagram/cookie")
def ig_cookie_delete():
    instagram.clear_cookie()
    return instagram.public_status()


@app.post("/api/instagram/check")
def ig_check():
    ok, info = instagram.check_now()
    if not ok:
        return JSONResponse(info, status_code=429 if "allowed_at" in info else 400)
    return info


# ---------------------------------------------------------------- browser extension ("Faceoff Sync")
def _ext_auth(request: Request):
    tok = store.load_settings().get("ext_token") or ""
    got = request.headers.get("x-faceoff-token", "")
    if not tok or not got or not secrets.compare_digest(tok, got):
        raise HTTPException(401, "Bad or missing pairing token. Generate one in Faceoff Settings and paste it into the extension.")


@app.post("/api/ext/token")
def ext_token():
    tok = "fo_" + secrets.token_urlsafe(24)
    store.save_settings({"ext_token": tok}, _internal=True)
    log.info("extension pairing token rotated")
    return {"token": tok}


@app.delete("/api/ext/token")
def ext_token_delete():
    store.save_settings({"ext_token": ""}, _internal=True)
    return {"ok": True}


def _control(s=None) -> dict:
    s = s or store.load_settings()
    return {"paused": bool(s["ext_paused"]), "reason": s.get("ext_pause_reason") or "", "paused_at": s.get("ext_paused_at") or 0,
            "backfill": bool(s["ext_backfill_enabled"]) and not s["ext_paused"],
            "limits": {"page_min_s": s["ext_page_min_s"], "page_max_s": s["ext_page_max_s"],
                       "pages_per_day": s["ext_pages_per_day"], "new_check_min_h": s["ext_new_check_min_h"],
                       "new_check_max_h": s["ext_new_check_max_h"], "stop_hours": s["ext_stop_hours"]}}


@app.get("/api/ext/control")
def ext_control(request: Request):
    """Checked by Faceoff Sync v2 before every Instagram request. Any error here = the extension stays stopped."""
    _ext_auth(request)
    return _control()


@app.post("/api/ext/report")
async def ext_report(request: Request):
    """The extension reports an Instagram warning (429/challenge/login/odd reply): pause everything server-side."""
    _ext_auth(request)
    body = await request.json()
    kind = re.sub(r"[^a-z_]", "", str(body.get("kind") or "warning"))[:20]
    msg = str(body.get("message") or "")[:200]
    store.save_settings({"ext_paused": True, "ext_paused_at": time.time(),
                         "ext_pause_reason": f"Auto-paused: Instagram {kind} ({msg}) at {time.strftime('%b %d %H:%M')}"})
    log.warning("extension reported Instagram %s - sync paused server-side", kind)
    return _control()


@app.post("/api/ext/pause")
async def ext_pause(request: Request):
    """From the Faceoff UI: pause/resume the extension, allow backfill."""
    body = await request.json()
    upd = {}
    if "paused" in body:
        upd["ext_paused"] = bool(body["paused"])
        upd["ext_paused_at"] = time.time() if body["paused"] else 0
        upd["ext_pause_reason"] = "Paused from Faceoff Settings" if body["paused"] else ""
    if "backfill" in body:
        upd["ext_backfill_enabled"] = bool(body["backfill"])
    s = store.save_settings(upd)
    return _control(s)


@app.get("/api/ext/ping")
def ext_ping(request: Request):
    _ext_auth(request)
    s = store.load_settings()
    return {"ok": True, "app": APP_NAME, "mode": s["ig_mode"] or s["default_mode"], "ig_export": worker.ig_export_status(),
            **store.ingest_stats()}


@app.post("/api/ingest/known")
async def ingest_known(request: Request):
    _ext_auth(request)
    if store.load_settings()["ext_paused"]:
        return JSONResponse({"status": "paused", **_control()}, status_code=423)
    body = await request.json()
    keys = [str(k)[:80] for k in (body.get("keys") or [])][:500]
    return {"known": sorted(store.ingest_known(keys))}


@app.post("/api/ingest")
async def ingest(request: Request, file: UploadFile = File(...), meta: str = Form(...)):
    _ext_auth(request)
    if store.load_settings()["ext_paused"]:
        return JSONResponse({"status": "paused", **_control()}, status_code=423)
    try:
        m = json.loads(meta)
        code = re.sub(r"[^A-Za-z0-9_-]", "", str(m.get("shortcode") or ""))[:40]
        idx = int(m.get("index") or 1)
    except (ValueError, TypeError):
        raise HTTPException(400, "bad meta")
    if not code:
        raise HTTPException(400, "missing shortcode")
    owner = re.sub(r"[^A-Za-z0-9._]", "", str(m.get("owner") or ""))[:40]
    is_video = str(m.get("kind")) == "video"
    key = f"{code}:{idx}"
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > (500 if is_video else MAX_UPLOAD_MB) * 1024 * 1024:
        raise HTTPException(413, "file too large")
    if not store.ingest_claim(key):
        return {"status": "duplicate", "key": key}
    try:
        taken = m.get("taken_at")
        sid = await run_in_threadpool(saved.add, data, key, code, idx, owner, str(m.get("caption") or "")[:500],
                                      float(taken) if isinstance(taken, (int, float)) else None,
                                      "video" if is_video else "image")
        store.ingest_set_image(key, sid)
    except Exception:
        store.ingest_release(key)
        raise
    return {"status": "saved", "key": key, "id": sid}


# ---------------------------------------------------------------- Saved library
@app.get("/api/saved")
def saved_list(filter: str = "all", offset: int = 0, limit: int = 60, owner: str = "", sort: str = "newest",
               kind: str = "all"):
    k = kind if kind in ("image", "video") else None
    rows, total = store.saved_list(filter, max(0, offset), max(1, min(200, limit)), owner or None, sort, k)
    return {"items": [saved.pub(r) for r in rows], "total": total, "offset": offset,
            "counts": store.saved_counts(owner or None, k)}


@app.get("/api/saved/owners")
def saved_owners(sort: str = "count", q: str = ""):
    return {"owners": store.saved_owners(sort, q.strip()[:60])}


@app.get("/api/saved/ids")
def saved_ids(filter: str = "all", owner: str = "", sort: str = "newest", kind: str = "all"):
    return {"ids": store.saved_ids(filter, owner or None, sort, kind if kind in ("image", "video") else None)}


@app.get("/api/saved/batch")
def saved_batch(ids: str = ""):
    out = []
    for x in ids.split(",")[:50]:
        if x.strip().isdigit():
            r = store.saved_get(int(x))
            if r:
                out.append(saved.pub(r))
    return {"items": out}


def _dl_name(owner, shortcode, idx, tail):
    clean = lambda x: "".join(ch for ch in str(x or "") if ch.isalnum() or ch in "._-")[:60]
    o = clean(owner) if owner and owner != "_unknown" else "instagram"
    return f"@{o}_{clean(shortcode) or 'post'}" + (f"_{idx}" if (idx or 1) > 1 else "") + tail


def _attachment(name: str) -> str:
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@app.get("/api/saved/{sid}")
def saved_one(sid: int):
    r = store.saved_get(sid)
    if not r:
        raise HTTPException(404, "Not found")
    return saved.pub(r)


@app.get("/api/saved/{sid}/file/{kind}")
def saved_file(sid: int, kind: str):
    r = store.saved_get(sid)
    if not r:
        raise HTTPException(404, "Not found")
    p = store.saved_paths(sid, r["ext"])
    headers = {"Cache-Control": "private, max-age=31536000, immutable"}
    if kind == "thumb":
        if not os.path.exists(p["thumb"]):
            raise HTTPException(404, "No thumbnail")
        return FileResponse(p["thumb"], media_type="image/jpeg", headers=headers)
    if kind == "original":
        mt = "video/mp4" if r["kind"] == "video" else {".png": "image/png", ".webp": "image/webp", ".heic": "image/heic"}.get(r["ext"], "image/jpeg")
        if r["ext"] == ".heic":  # browsers can't show HEIC: serve a JPEG preview
            img = redact.load_image(open(p["original"], "rb").read())
            return Response(redact.thumbnail(img, 1600), media_type="image/jpeg", headers=headers)
        return FileResponse(p["original"], media_type=mt, headers=headers)
    if kind == "download":   # untouched original as an attachment (Range still works); download-to-device only
        mt = "video/mp4" if r["kind"] == "video" else {".png": "image/png", ".webp": "image/webp", ".heic": "image/heic"}.get(r["ext"], "image/jpeg")
        headers["Content-Disposition"] = _attachment(_dl_name(r["owner"], r["shortcode"], r["idx"], r["ext"] or (".mp4" if r["kind"] == "video" else ".jpg")))
        return FileResponse(p["original"], media_type=mt, headers=headers)
    raise HTTPException(404, "Unknown file")


@app.post("/api/saved/process")
async def saved_process(request: Request):
    body = await request.json()
    mode = body.get("mode")
    if mode not in ("blur", "box"):
        raise HTTPException(400, "mode must be blur or box")
    ids = [int(i) for i in (body.get("ids") or [])][:20000]
    res = [saved.process(i, mode) for i in ids]
    return {"queued": sum(1 for r in res if isinstance(r, int)), "skipped_videos": res.count("video")}


@app.post("/api/saved/delete")
async def saved_delete(request: Request):
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])][:20000]
    n = sum(1 for i in ids if store.saved_delete(i))
    return {"deleted": n}


@app.get("/api/ext/download")
def ext_download():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(EXT_DIR):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                z.write(full, os.path.join("faceoff-sync", os.path.relpath(full, EXT_DIR)))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="faceoff-sync.zip"', "Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(STATIC, "icons", "favicon-32.png"), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


app.mount("/icons", StaticFiles(directory=os.path.join(STATIC, "icons")), name="icons")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
