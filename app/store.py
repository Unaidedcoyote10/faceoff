"""SQLite + settings storage."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "faceoff.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
GALLERY_LIMIT = int(os.environ.get("GALLERY_LIMIT", "100"))

DEFAULT_SETTINGS = {
    "default_mode": os.environ.get("DEFAULT_MODE", "blur"),  # blur | box
    "padding": float(os.environ.get("DEFAULT_PADDING", "0.08")),  # fraction of face box
    "sensitivity": os.environ.get("DEFAULT_SENSITIVITY", "medium"),  # low | medium | high
    "blur_strength": 3,  # 1..5
    "auto_export": os.environ.get("AUTO_EXPORT", "1") not in ("0", "false", "no"),
    "export_subfolders": True,  # export/YYYY-MM/...
    "export_folder_prefix": True,  # folder uploads: prefix export names with the source folder
    # Instagram
    "ig_enabled": False,  # legacy server-side pull: opt-in only
    "ext_token": "",
    # Instagram safety: server-side kill switch the extension (v2) checks before EVERY Instagram request.
    "ext_paused": True,
    "ext_pause_reason": "Sync starts paused. Pair the extension, then press Resume in Settings.",
    "ext_paused_at": 0,
    "ext_backfill_enabled": False,     # backfill must be re-allowed explicitly
    "ext_page_min_s": 45, "ext_page_max_s": 120,
    "ext_pages_per_day": 40, "ext_new_check_min_h": 3, "ext_new_check_max_h": 6,
    "ext_stop_hours": 48,
    "ig_export": True,  # export extension/IG items to the IG export folder (if one is mounted)
    "ig_interval_hours": 6.0,
    "ig_per_run_cap": 8,
    "ig_first_run": "baseline",  # baseline | grab
    "ig_first_run_grab": 5,
    "ig_mode": "",  # '' = use default_mode
}

_lock = threading.RLock()


def ensure_dirs():
    os.makedirs(os.path.join(DATA_DIR, "saved"), exist_ok=True)
    for d in ("originals", "processed", "thumbs"):
        os.makedirs(os.path.join(DATA_DIR, d), exist_ok=True)
    for d in ("originals", "processed", "thumbs"):
        os.makedirs(os.path.join(DATA_DIR, "docs", d), exist_ok=True)


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    ensure_dirs()
    with _lock, conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created REAL NOT NULL,
            source TEXT NOT NULL,
            orig_name TEXT,
            mode TEXT NOT NULL,
            padding REAL,
            sensitivity TEXT,
            status TEXT NOT NULL,
            faces INTEGER DEFAULT 0,
            boxes TEXT,
            error TEXT,
            width INTEGER, height INTEGER,
            export_path TEXT,
            ig_shortcode TEXT,
            ig_owner TEXT,
            processed_at REAL
        )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_created ON images(created)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(images)")}
        for col, typ in (("kind", "TEXT DEFAULT 'image'"), ("caption", "TEXT"), ("taken_at", "REAL"), ("ext_key", "TEXT"),
                         ("duration", "REAL"), ("progress", "REAL")):
            if col not in cols:
                c.execute(f"ALTER TABLE images ADD COLUMN {col} {typ}")
        # extension ingest dedupe: one row per shortcode:index ever received (survives gallery pruning)
        c.execute("""CREATE TABLE IF NOT EXISTS ingested (key TEXT PRIMARY KEY, image_id INTEGER, at REAL NOT NULL)""")
        # "Saved" library: raw Instagram saves from the extension, kept unprocessed until you pick them (no limit)
        c.execute("""CREATE TABLE IF NOT EXISTS saved (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, shortcode TEXT NOT NULL, idx INTEGER NOT NULL,
            owner TEXT, caption TEXT, taken_at REAL, saved_at REAL NOT NULL, kind TEXT NOT NULL, ext TEXT NOT NULL,
            size INTEGER, width INTEGER, height INTEGER, duration REAL, raw_export_path TEXT, image_id INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_saved_at ON saved(saved_at)")
        # Keyword redaction jobs. Separate from the photo gallery so face blur never sees them.
        c.execute("""CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created REAL NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            ext TEXT NOT NULL,
            out_ext TEXT,
            status TEXT NOT NULL,
            keywords TEXT NOT NULL,
            pages INTEGER DEFAULT 0,
            hits INTEGER DEFAULT 0,
            hit_detail TEXT,
            ocr_pages INTEGER DEFAULT 0,
            note TEXT,
            error TEXT,
            width INTEGER, height INTEGER,
            export_path TEXT,
            preview_page INTEGER DEFAULT 1,
            progress REAL,
            processed_at REAL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created)")
        # recover jobs interrupted by a restart
        c.execute("UPDATE images SET status='queued' WHERE status='processing'")
        c.execute("UPDATE documents SET status='queued' WHERE status='processing'")


def row(r):
    if r is None:
        return None
    d = dict(r)
    d["boxes"] = json.loads(d["boxes"]) if d.get("boxes") else []
    return d


def insert_image(**kw) -> int:
    kw.setdefault("created", time.time())
    kw.setdefault("status", "queued")
    cols = ",".join(kw)
    q = ",".join("?" * len(kw))
    with _lock, conn() as c:
        cur = c.execute(f"INSERT INTO images ({cols}) VALUES ({q})", list(kw.values()))
        return cur.lastrowid


def update_image(id_: int, **kw):
    if "boxes" in kw and not isinstance(kw["boxes"], str):
        kw["boxes"] = json.dumps(kw["boxes"])
    sets = ",".join(f"{k}=?" for k in kw)
    with _lock, conn() as c:
        c.execute(f"UPDATE images SET {sets} WHERE id=?", [*kw.values(), id_])


def get_image(id_: int):
    with conn() as c:
        return row(c.execute("SELECT * FROM images WHERE id=?", (id_,)).fetchone())


def list_images(limit=GALLERY_LIMIT):
    with conn() as c:
        # uploads + photos processed from the Saved library, newest first
        return [row(r) for r in c.execute("SELECT * FROM images ORDER BY created DESC, id DESC LIMIT ?", (limit,))]


def queued_ids():
    with conn() as c:
        return [(r[0], r[1] or "image") for r in c.execute("SELECT id, kind FROM images WHERE status='queued' ORDER BY id")]


def ingest_known(keys) -> set:
    keys = list(keys)[:500]
    if not keys:
        return set()
    with conn() as c:
        q = ",".join("?" * len(keys))
        return {r[0] for r in c.execute(f"SELECT key FROM ingested WHERE key IN ({q})", keys)}


def ingest_claim(key: str) -> bool:
    """True if this key is new (and now claimed)."""
    with _lock, conn() as c:
        try:
            c.execute("INSERT INTO ingested (key, at) VALUES (?, ?)", (key, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False


def ingest_set_image(key: str, image_id: int):
    with _lock, conn() as c:
        c.execute("UPDATE ingested SET image_id=? WHERE key=?", (image_id, key))


def ingest_release(key: str):
    with _lock, conn() as c:
        c.execute("DELETE FROM ingested WHERE key=?", (key,))


def ingest_stats() -> dict:
    with conn() as c:
        n, last = c.execute("SELECT COUNT(*), MAX(at) FROM ingested").fetchone()
    return {"total": n or 0, "last": last}


def counts():
    with conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM images GROUP BY status")}


def file_paths(id_: int):
    return {
        "original": os.path.join(DATA_DIR, "originals", f"{id_}.bin"),
        "processed": os.path.join(DATA_DIR, "processed", f"{id_}.jpg"),
        "view_orig": os.path.join(DATA_DIR, "thumbs", f"{id_}_o.jpg"),   # ~1600px, viewer
        "view_proc": os.path.join(DATA_DIR, "thumbs", f"{id_}_p.jpg"),
        "thumb_orig": os.path.join(DATA_DIR, "thumbs", f"{id_}_os.jpg"),  # ~480px, grid
        "thumb_proc": os.path.join(DATA_DIR, "thumbs", f"{id_}_ps.jpg"),
        "processed_video": os.path.join(DATA_DIR, "processed", f"{id_}.mp4"),
    }


def delete_image(id_: int):
    """Remove from gallery + data volume. Exported copies are never touched."""
    with _lock, conn() as c:
        c.execute("DELETE FROM images WHERE id=?", (id_,))
    for p in file_paths(id_).values():
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def prune():
    """Keep only the newest GALLERY_LIMIT gallery items (never prunes queued/processing).
    Results of Saved-library items are never deleted here: they only scroll out of the gallery view; the Saved item,
    its raw file and its processed copy stay."""
    with conn() as c:
        old = [r[0] for r in c.execute(
            "SELECT id FROM images WHERE source != 'saved' AND status NOT IN ('queued','processing') AND id NOT IN "
            "(SELECT id FROM images ORDER BY created DESC, id DESC LIMIT ?)", (GALLERY_LIMIT,))]
    for i in old:
        delete_image(i)
    return len(old)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except (FileNotFoundError, ValueError):
        pass
    return s


def save_settings(new: dict, _internal: bool = False) -> dict:
    with _lock:
        s = load_settings()
        for k, v in new.items():
            if k in DEFAULT_SETTINGS and (k != "ext_token" or _internal):
                s[k] = v
        # validation / clamps
        s["default_mode"] = s["default_mode"] if s["default_mode"] in ("blur", "box") else "blur"
        s["ig_mode"] = s["ig_mode"] if s["ig_mode"] in ("", "blur", "box") else ""
        s["sensitivity"] = s["sensitivity"] if s["sensitivity"] in ("low", "medium", "high") else "medium"
        s["padding"] = max(0.0, min(0.4, float(s["padding"])))
        s["blur_strength"] = max(1, min(5, int(s["blur_strength"])))
        from . import instagram  # floor lives there

        s["ig_interval_hours"] = max(instagram.MIN_INTERVAL_HOURS, min(72.0, float(s["ig_interval_hours"])))
        s["ig_per_run_cap"] = max(1, min(instagram.MAX_PER_RUN, int(s["ig_per_run_cap"])))
        s["ig_first_run"] = s["ig_first_run"] if s["ig_first_run"] in ("baseline", "grab") else "baseline"
        s["ig_first_run_grab"] = max(0, min(20, int(s["ig_first_run_grab"])))
        s["ext_token"] = str(s.get("ext_token") or "")
        s["ext_page_min_s"] = max(30, int(s.get("ext_page_min_s") or 45))
        s["ext_page_max_s"] = max(s["ext_page_min_s"] + 10, int(s.get("ext_page_max_s") or 120))
        s["ext_pages_per_day"] = max(0, min(100, int(s.get("ext_pages_per_day") or 0)))
        s["ext_new_check_min_h"] = max(2, float(s.get("ext_new_check_min_h") or 3))
        s["ext_new_check_max_h"] = max(s["ext_new_check_min_h"], float(s.get("ext_new_check_max_h") or 6))
        s["ext_stop_hours"] = max(24, min(168, float(s.get("ext_stop_hours") or 48)))
        for b in ("auto_export", "export_subfolders", "export_folder_prefix", "ig_enabled", "ig_export", "ext_paused",
                  "ext_backfill_enabled"):
            s[b] = bool(s[b])
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return s


# ------------------------------------------------------------------ Saved library
SAVED_DIR = os.path.join(DATA_DIR, "saved")
SAVED_FILTERS = {
    "all": "1=1", "images": "s.kind='image'", "videos": "s.kind='video'",
    "unprocessed": "(i.status IS NULL)", "processed": "(i.status='done')",
    "working": "(i.status IN ('queued','processing'))", "errors": "(i.status='error')",
}


def saved_paths(sid: int, ext: str = "") -> dict:
    return {"original": os.path.join(SAVED_DIR, f"{sid}{ext}"), "thumb": os.path.join(SAVED_DIR, f"{sid}_t.jpg")}


def saved_insert(**kw) -> int:
    with _lock, conn() as c:
        cols = ",".join(kw)
        cur = c.execute(f"INSERT INTO saved ({cols}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))
        return cur.lastrowid


def saved_update(sid: int, **kw):
    if not kw:
        return
    with _lock, conn() as c:
        c.execute(f"UPDATE saved SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), sid])


_SAVED_SELECT = """SELECT s.*, i.status AS p_status, i.mode AS p_mode, i.faces AS p_faces, i.error AS p_error,
    i.processed_at AS p_at, i.export_path AS p_export, i.progress AS p_progress
    FROM saved s LEFT JOIN images i ON i.id = s.image_id"""


def saved_by_image(image_id: int):
    with conn() as c:
        r = c.execute("SELECT owner, shortcode, idx FROM saved WHERE image_id=? LIMIT 1", (image_id,)).fetchone()
    return dict(r) if r else None


def saved_get(sid: int):
    with conn() as c:
        r = c.execute(_SAVED_SELECT + " WHERE s.id=?", (sid,)).fetchone()
        return dict(r) if r else None


SAVED_SORTS = {"newest": "s.saved_at DESC, s.id DESC", "oldest": "s.saved_at ASC, s.id ASC",
               "owner": "lower(coalesce(nullif(s.owner,''),'~')) ASC, s.saved_at DESC, s.id DESC"}


def _owner_where(where, args, owner, kind=None):
    if kind in ("image", "video"):
        where, args = where + " AND s.kind=?", args + [kind]
    if owner == "_unknown":
        return where + " AND (s.owner IS NULL OR s.owner='')", args
    if owner:
        return where + " AND s.owner=?", args + [owner]
    return where, args


def saved_list(flt="all", offset=0, limit=60, owner=None, sort="newest", kind=None):
    where, args = _owner_where(SAVED_FILTERS.get(flt, "1=1"), [], owner, kind)
    order = SAVED_SORTS.get(sort, SAVED_SORTS["newest"])
    with conn() as c:
        rows = [dict(r) for r in c.execute(_SAVED_SELECT + f" WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                                           (*args, limit, offset))]
        total = c.execute(f"SELECT COUNT(*) FROM saved s LEFT JOIN images i ON i.id=s.image_id WHERE {where}", args).fetchone()[0]
    return rows, total


def saved_ids(flt="all", owner=None, sort="newest", kind=None):
    where, args = _owner_where(SAVED_FILTERS.get(flt, "1=1"), [], owner, kind)
    order = SAVED_SORTS.get(sort, SAVED_SORTS["newest"])
    with conn() as c:
        return [r[0] for r in c.execute(f"SELECT s.id FROM saved s LEFT JOIN images i ON i.id=s.image_id WHERE {where} "
                                        f"ORDER BY {order}", args)]


def saved_counts(owner=None, kind=None):
    with conn() as c:
        out = {}
        for k, w in SAVED_FILTERS.items():
            w, a = _owner_where(w, [], owner, kind if k not in ("images", "videos") else None)
            out[k] = c.execute(f"SELECT COUNT(*) FROM saved s LEFT JOIN images i ON i.id=s.image_id WHERE {w}", a).fetchone()[0]
        return out


def saved_owners(sort="count", q=""):
    """Accounts in the Saved library: count, latest save, processed count."""
    order = "latest DESC" if sort == "recent" else ("name ASC" if sort == "name" else "n DESC, latest DESC")
    args, where = [], ""
    if q:
        where = "WHERE lower(coalesce(s.owner,'')) LIKE ?"
        args.append("%" + q.lower().replace("%", "").replace("_", "\\_") + "%")
        where += " ESCAPE '\\'"
    with conn() as c:
        rows = c.execute(f"""SELECT coalesce(nullif(s.owner,''),'_unknown') AS name, COUNT(*) AS n, MAX(s.saved_at) AS latest,
            SUM(s.kind='video') AS videos, SUM(i.status='done') AS processed, MIN(s.id) AS first_id,
            MAX(CASE WHEN s.kind='image' THEN s.id END) AS thumb_id
            FROM saved s LEFT JOIN images i ON i.id=s.image_id {where} GROUP BY name ORDER BY {order}""", args).fetchall()
    return [dict(r) for r in rows]


def saved_delete(sid: int):
    """Remove from the library (+ its processed copy). Exported files and Instagram are never touched.
    The dedupe key stays, so the extension won't re-add it."""
    r = saved_get(sid)
    if not r:
        return False
    with _lock, conn() as c:
        c.execute("DELETE FROM saved WHERE id=?", (sid,))
    for p in saved_paths(sid, r["ext"]).values():
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    if r.get("image_id"):
        delete_image(r["image_id"])
    return True


# ------------------------------------------------------------------ Document keyword redaction
DOC_LIBRARY_LIMIT = int(os.environ.get("DOC_LIBRARY_LIMIT", "50"))
DOCS_DIR = os.path.join(DATA_DIR, "docs")


def doc_paths(id_: int, ext: str = "", out_ext: str = "") -> dict:
    return {
        "original": os.path.join(DOCS_DIR, "originals", f"{id_}{ext or ''}"),
        "processed": os.path.join(DOCS_DIR, "processed", f"{id_}{out_ext or ''}"),
        "preview": os.path.join(DOCS_DIR, "thumbs", f"{id_}.jpg"),
    }


def _doc_row(r):
    if r is None:
        return None
    d = dict(r)
    try:
        d["keywords"] = json.loads(d["keywords"]) if d.get("keywords") else []
    except (TypeError, ValueError):
        d["keywords"] = []
    try:
        d["hit_detail"] = json.loads(d["hit_detail"]) if d.get("hit_detail") else {}
    except (TypeError, ValueError):
        d["hit_detail"] = {}
    return d


def doc_insert(**kw) -> int:
    if "keywords" in kw and not isinstance(kw["keywords"], str):
        kw["keywords"] = json.dumps(kw["keywords"])
    kw.setdefault("created", time.time())
    kw.setdefault("status", "queued")
    with _lock, conn() as c:
        cur = c.execute(f"INSERT INTO documents ({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))
        return cur.lastrowid


def doc_update(id_: int, **kw):
    if not kw:
        return
    if "keywords" in kw and not isinstance(kw["keywords"], str):
        kw["keywords"] = json.dumps(kw["keywords"])
    if "hit_detail" in kw and not isinstance(kw["hit_detail"], str):
        kw["hit_detail"] = json.dumps(kw["hit_detail"])
    with _lock, conn() as c:
        c.execute(f"UPDATE documents SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), id_])


def doc_get(id_: int):
    with conn() as c:
        return _doc_row(c.execute("SELECT * FROM documents WHERE id=?", (id_,)).fetchone())


def doc_list(limit=DOC_LIBRARY_LIMIT):
    with conn() as c:
        return [_doc_row(r) for r in c.execute("SELECT * FROM documents ORDER BY created DESC, id DESC LIMIT ?", (limit,))]


def doc_queued_ids():
    with conn() as c:
        return [r[0] for r in c.execute("SELECT id FROM documents WHERE status='queued' ORDER BY id")]


def doc_counts():
    with conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM documents GROUP BY status")}


def doc_delete(id_: int) -> bool:
    """Remove the library row and private files. The exported copy is kept."""
    r = doc_get(id_)
    if not r:
        return False
    with _lock, conn() as c:
        c.execute("DELETE FROM documents WHERE id=?", (id_,))
    for p in doc_paths(id_, r.get("ext") or "", r.get("out_ext") or "").values():
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return True


def doc_prune():
    """Keep the newest DOC_LIBRARY_LIMIT finished jobs. Exports are never deleted."""
    with conn() as c:
        old = [r[0] for r in c.execute(
            "SELECT id FROM documents WHERE status NOT IN ('queued','processing') AND id NOT IN "
            "(SELECT id FROM documents ORDER BY created DESC, id DESC LIMIT ?)", (DOC_LIBRARY_LIMIT,))]
    for i in old:
        doc_delete(i)
    return len(old)
