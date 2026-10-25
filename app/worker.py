"""Background processing queue (bounded, Pi friendly): images only, WORKERS threads."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import redact, store

log = logging.getLogger("faceoff.worker")
WORKERS = max(1, int(os.environ.get("WORKERS", "2")))
EXPORT_DIR = os.environ.get("EXPORT_DIR", "/export")
IG_EXPORT_DIR = os.environ.get("IG_EXPORT_DIR", "/export-ig")
IG_EXPORT_ENABLED = os.environ.get("IG_EXPORT_ENABLED", "0") in ("1", "true", "yes")
FILE_MODE = int(os.environ.get("EXPORT_FILE_MODE", "664"), 8)
os.umask(int(os.environ.get("UMASK", "002"), 8))
IG_SOURCES = ("instagram", "extension")


_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="redact")
_inflight: set[int] = set()
_lock = threading.Lock()


def _atomic_write(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    try:  # group-writable so the files are usable over SMB/NFS by the share's group
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


def _slug(s: str, n=40) -> str:
    s = os.path.splitext(os.path.basename(s or "image"))[0]
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")
    return (s or "image")[:n]


def _dir_ok(d: str) -> bool:
    return os.path.isdir(d) and os.access(d, os.W_OK)


def export_dir_status() -> dict:
    return {"path": EXPORT_DIR, "label": os.environ.get("EXPORT_LABEL", ""), "writable": _dir_ok(EXPORT_DIR)}


def ig_export_status() -> dict:
    return {"path": IG_EXPORT_DIR, "label": os.environ.get("IG_EXPORT_LABEL", ""), "enabled": IG_EXPORT_ENABLED,
            "writable": IG_EXPORT_ENABLED and _dir_ok(IG_EXPORT_DIR)}


def _target_dir(rec: dict, settings: dict) -> str | None:
    if rec["source"] in IG_SOURCES:
        # legacy server-pull items: own IG folder or not exported at all
        if not (IG_EXPORT_ENABLED and settings.get("ig_export", True)):
            return None
        return IG_EXPORT_DIR
    # uploads and items you process from the Saved library export to the export folder
    if not settings.get("auto_export", True):
        return None
    return EXPORT_DIR


def _export(rec: dict, src_path: str, ext: str, settings: dict) -> str | None:
    base = _target_dir(rec, settings)
    if not base:
        return None
    ts = datetime.fromtimestamp(rec["created"])
    orig = (rec.get("orig_name") or f"img{rec['id']}").replace("\\", "/")
    stem = _slug(orig)
    prefix = ""
    dirs = [d for d in orig.split("/")[:-1] if d not in ("", ".", "..")]
    if dirs and settings.get("export_folder_prefix", True):
        prefix = "-".join(_slug(d, 24) for d in dirs[-2:]) + "_"  # e.g. Trip-Day1_
    label = "ig" if rec["source"] == "saved" else rec["source"]  # saved IG items: ..._ig_<username>_<shortcode>_<n>_blur.jpg
    name = f"{ts:%Y%m%d-%H%M%S}_{label}_{prefix}{stem}_{rec['mode']}{ext}"
    folder = os.path.join(base, f"{ts:%Y-%m}") if settings.get("export_subfolders", True) else base
    path = os.path.join(folder, name)
    i = 2
    while os.path.exists(path):
        path = os.path.join(folder, name[: -len(ext)] + f"_{i}{ext}")
        i += 1
    with open(src_path, "rb") as f:
        _atomic_write(path, f.read())
    return path


def _settings_for(rec, settings):
    pad = rec["padding"] if rec["padding"] is not None else settings["padding"]
    sens = rec["sensitivity"] or settings["sensitivity"]
    return pad, sens


def _finish(id_, rec, settings, src_path, ext, **fields):
    export_path, export_err = None, None
    try:
        export_path = _export(rec, src_path, ext, settings)
    except Exception as e:  # export failure shouldn't lose the processed file
        export_err = f"export failed: {e}"
        log.warning("export failed for %s: %s", id_, e)
    store.update_image(id_, status="done", export_path=export_path, error=export_err, processed_at=time.time(), **fields)


def _process(id_: int):
    try:
        rec = store.get_image(id_)
        if not rec:
            return
        store.update_image(id_, status="processing", error=None)
        settings = store.load_settings()
        paths = store.file_paths(id_)
        with open(paths["original"], "rb") as f:
            data = f.read()
        pad, sens = _settings_for(rec, settings)
        t = time.time()
        img, out, faces = redact.process_bytes(data, rec["mode"], pad, sens, settings["blur_strength"])
        jpeg = redact.encode_jpeg(out, 92)  # EXIF/GPS intentionally stripped
        _atomic_write(paths["processed"], jpeg)
        _atomic_write(paths["view_orig"], redact.thumbnail(img, 1600))
        _atomic_write(paths["view_proc"], redact.thumbnail(out, 1600))
        _atomic_write(paths["thumb_orig"], redact.thumbnail(img, 480))
        _atomic_write(paths["thumb_proc"], redact.thumbnail(out, 480))
        h, w = img.shape[:2]
        _finish(id_, rec, settings, paths["processed"], ".jpg", faces=len(faces),
                boxes=[f.as_dict() for f in faces], width=w, height=h)
        log.info("processed #%s: %d faces, %s, %.2fs", id_, len(faces), rec["mode"], time.time() - t)
        del img, out
    except Exception as e:
        log.exception("processing #%s failed", id_)
        store.update_image(id_, status="error", error=str(e)[:300])
    finally:
        with _lock:
            _inflight.discard(id_)
        store.prune()


def enqueue(id_: int, kind: str = "image"):
    with _lock:
        if id_ in _inflight:
            return
        _inflight.add(id_)
    if kind != "image":
        store.update_image(id_, status="error", error="videos aren't redacted")
        with _lock:
            _inflight.discard(id_)
        return
    _pool.submit(_process, id_)


def resume():
    for i, kind in store.queued_ids():
        enqueue(i, kind)


def add_image(data: bytes, name: str, source: str, mode: str, padding=None, sensitivity=None, kind="image", **extra) -> int:
    id_ = store.insert_image(source=source, orig_name=name, mode=mode, padding=padding, sensitivity=sensitivity,
                             kind=kind, **extra)
    _atomic_write(store.file_paths(id_)["original"], data)
    enqueue(id_, kind)
    return id_
