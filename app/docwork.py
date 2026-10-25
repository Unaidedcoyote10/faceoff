"""One-at-a-time queue for document keyword redaction. Face jobs keep their own pool."""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import keywords, store, worker

log = logging.getLogger("faceoff.docs")
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="docs")
_inflight: set[int] = set()
_lock = threading.Lock()


def _clean_files(paths: dict):
    for p in paths.values():
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _process(id_: int):
    paths = {}
    try:
        rec = store.doc_get(id_)
        if not rec:
            return
        store.doc_update(id_, status="processing", error=None, progress=0)
        paths = store.doc_paths(id_, rec["ext"], rec.get("out_ext") or "")
        with open(paths["original"], "rb") as f:
            data = f.read()
        t = time.time()

        def prog(frac: float):
            store.doc_update(id_, progress=max(0.0, min(1.0, float(frac))))

        result = keywords.redact_file(
            data, rec["name"], rec["keywords"] or [], on_progress=prog, presets=rec.get("presets") or [],
        )
        if not store.doc_get(id_):
            return
        paths = store.doc_paths(id_, rec["ext"], result.ext)
        worker._atomic_write(paths["processed"], result.data)
        worker._atomic_write(paths["preview"], result.preview_jpeg)
        export_path, export_err = None, None
        if store.doc_get(id_):
            try:
                export_path = worker.export_bytes(rec["created"], rec["name"], "doc", "redact", result.ext, result.data)
            except Exception as e:
                export_err = f"export failed: {e}"
                log.warning("doc export failed for #%s: %s", id_, e)
        if not store.doc_get(id_):
            _clean_files(paths)
            return
        note = result.note
        if export_err:
            note = (note + " " + export_err).strip()
        store.doc_update(
            id_, status="done", pages=result.pages, hits=result.hits, hit_detail=result.by_phrase,
            ocr_pages=result.ocr_pages, note=note, error=export_err, width=result.width, height=result.height,
            export_path=export_path, preview_page=result.preview_page, out_ext=result.ext, progress=1,
            processed_at=time.time(),
        )
        log.info("doc #%s done: %s pages, %s hits, ocr_pages=%s, %.2fs", id_, result.pages, result.hits, result.ocr_pages, time.time() - t)
    except keywords.DocError as e:
        log.info("doc #%s rejected: %s", id_, e)
        if store.doc_get(id_):
            store.doc_update(id_, status="error", error=str(e)[:300])
    except Exception as e:
        log.exception("doc #%s failed", id_)
        if store.doc_get(id_):
            store.doc_update(id_, status="error", error=str(e)[:300])
    finally:
        with _lock:
            _inflight.discard(id_)
        store.doc_prune()


def enqueue(id_: int):
    with _lock:
        if id_ in _inflight:
            return
        _inflight.add(id_)
    _pool.submit(_process, id_)


def resume():
    ids = store.doc_queued_ids()
    for i in ids:
        enqueue(i)
    if ids:
        log.info("resumed %d document job(s)", len(ids))


def add(data: bytes, name: str, phrases: list[str], presets: list[str] | None = None) -> int:
    kind = keywords.kind_for(name)
    ext = os.path.splitext(name)[1].lower()
    if ext == ".jpeg":
        ext = ".jpg"
    id_ = store.doc_insert(
        name=name, kind=kind, ext=ext, status="queued", keywords=phrases, presets=list(presets or []),
    )
    worker._atomic_write(store.doc_paths(id_, ext)["original"], data)
    enqueue(id_)
    return id_
