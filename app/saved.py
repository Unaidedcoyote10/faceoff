"""Saved library: raw Instagram saves from the Faceoff Sync extension.

Items (photos and videos) are stored unprocessed with a small grid thumbnail made on arrival, and the raw original is
copied to the IG folder (if configured). Nothing is redacted until you select photos and choose Blur / Box; those run
through the normal queue and the processed output exports to the export folder like a manual upload.
Videos are kept raw only."""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import datetime

import cv2
import numpy as np

from . import redact, store, worker

log = logging.getLogger("faceoff.saved")
THUMB = 360
IMG_EXT = {b"\xff\xd8": ".jpg", b"\x89P": ".png", b"RI": ".webp"}


def _ext_for(data: bytes, kind: str) -> str:
    if kind == "video":
        return ".mp4"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return ".heic"
    return IMG_EXT.get(data[:2], ".jpg")


def _slug(s: str, n=40) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", s or "").strip("-.") or "x")[:n]


def _thumb_image(data: bytes, dst: str):
    img = redact.load_image(data)
    h, w = img.shape[:2]
    worker._atomic_write(dst, redact.thumbnail(img, THUMB))
    return w, h


def _thumb_video(src: str, dst: str):
    """Poster frame + size + duration with OpenCV (no ffmpeg needed). Videos are stored raw, never redacted."""
    cap = cv2.VideoCapture(src)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        dur = n / fps if fps > 0 else None
        if n > 3:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(int(fps or 30), int(n // 3)))
        ok, frame = cap.read()
        if not ok:
            raise ValueError("unreadable video")
        h, w = frame.shape[:2]
        worker._atomic_write(dst, redact.thumbnail(frame, THUMB))
        return w, h, dur
    finally:
        cap.release()


def _raw_export(rec: dict, src: str) -> str | None:
    """Copy the untouched original to the IG folder: <owner>/<YYYY-MM-DD>_<owner>_<shortcode>_<n>.<ext>."""
    st = worker.ig_export_status()
    if not st["enabled"] or not store.load_settings().get("ig_export", True):
        return None
    when = datetime.fromtimestamp(rec.get("taken_at") or rec["saved_at"])
    owner = _slug(rec.get("owner"), 60) if rec.get("owner") else "_unknown"
    name = f"{when:%Y-%m-%d}_{owner}_{_slug(rec['shortcode'])}_{rec['idx']}{rec['ext']}"
    path = os.path.join(worker.IG_EXPORT_DIR, owner, name)
    if os.path.exists(path):
        return path
    with open(src, "rb") as f:
        worker._atomic_write(path, f.read())
    return path


def add(data: bytes, key: str, shortcode: str, idx: int, owner: str, caption: str, taken_at, kind: str) -> int:
    ext = _ext_for(data, kind)
    rec = dict(key=key, shortcode=shortcode, idx=idx, owner=owner, caption=caption, taken_at=taken_at,
               saved_at=time.time(), kind=kind, ext=ext, size=len(data))
    sid = store.saved_insert(**rec)
    rec["id"] = sid
    p = store.saved_paths(sid, ext)
    try:
        worker._atomic_write(p["original"], data)
        if kind == "video":
            w, h, dur = _thumb_video(p["original"], p["thumb"])
            store.saved_update(sid, width=w, height=h, duration=dur)
        else:
            w, h = _thumb_image(data, p["thumb"])
            store.saved_update(sid, width=w, height=h)
    except Exception as e:
        log.warning("saved #%s: thumbnail failed: %s", sid, e)
    try:
        store.saved_update(sid, raw_export_path=_raw_export(rec, p["original"]))
    except Exception as e:
        log.warning("saved #%s: raw export failed: %s", sid, e)
    return sid


def process(sid: int, mode: str):
    """Queue a saved item for redaction (re-uses its previous result row when re-processing)."""
    r = store.saved_get(sid)
    if not r:
        return None
    if r["kind"] == "video":
        return "video"           # videos are kept raw only (not redacted)
    if r.get("image_id") and store.get_image(r["image_id"]):
        if r.get("p_status") in ("queued", "processing"):
            return r["image_id"]
        store.update_image(r["image_id"], mode=mode, status="queued", error=None, padding=None, sensitivity=None)
        worker.enqueue(r["image_id"])
        return r["image_id"]
    name = f"{_slug(r['owner'] or 'ig')}_{_slug(r['shortcode'])}_{r['idx']}"
    id_ = store.insert_image(source="saved", orig_name=name, mode=mode, kind=r["kind"], ig_shortcode=r["shortcode"],
                             ig_owner=r["owner"], caption=r["caption"], taken_at=r["taken_at"], ext_key=r["key"])
    src = store.saved_paths(sid, r["ext"])["original"]
    dst = store.file_paths(id_)["original"]
    try:
        os.link(src, dst)        # no extra disk for big videos
    except OSError:
        shutil.copyfile(src, dst)
    store.saved_update(sid, image_id=id_)
    worker.enqueue(id_)
    return id_


def pub(r: dict) -> dict:
    proc = None
    if r.get("image_id") and r.get("p_status"):
        proc = {"id": r["image_id"], "status": r["p_status"], "mode": r["p_mode"], "faces": r["p_faces"],
                "error": r["p_error"], "at": r["p_at"], "exported": os.path.basename(r["p_export"]) if r["p_export"] else None,
                "progress": r["p_progress"], "v": int(r["p_at"] or 0)}
    return {"id": r["id"], "shortcode": r["shortcode"], "index": r["idx"], "owner": r["owner"],
            "caption": (r["caption"] or "")[:160], "taken_at": r["taken_at"], "saved_at": r["saved_at"], "kind": r["kind"],
            "width": r["width"], "height": r["height"], "duration": r["duration"], "size": r["size"],
            "raw_exported": bool(r["raw_export_path"]), "ext": r["ext"], "proc": proc}
