"""Server-side tests for the extension ingest + Saved library (no Instagram involved).
Run: DATA_DIR=/tmp/x EXPORT_DIR=/tmp/x/drop IG_EXPORT_ENABLED=1 IG_EXPORT_DIR=/tmp/x/ig python -m tests.ingest_test"""
import json
import os
import sys
import time
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)

from fastapi.testclient import TestClient

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("SAMPLES", os.path.join(HERE, "samples"))
VIDEO = os.environ.get("TEST_VIDEO", os.path.join(HERE, "video", "clip4s_small.mp4"))
for d in (os.environ["EXPORT_DIR"], os.environ["IG_EXPORT_DIR"]):
    os.makedirs(d, exist_ok=True)

from app import main, store  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))
    ok += bool(cond)
    fail += (not cond)


os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
cookie_path = os.path.join(os.environ["DATA_DIR"], "ig_cookie.json")
with open(cookie_path, "w") as f:
    f.write('{"sessionid":"should-be-deleted"}')
with open(os.path.join(os.environ["DATA_DIR"], "settings.json"), "w") as f:
    json.dump({"ig_enabled": True, "ig_mode": "box", "default_mode": "blur", "ext_token": ""}, f)

with TestClient(main.app) as c:
    check("leftover Instagram cookie file removed", not os.path.exists(cookie_path))
    # auth
    r = c.post("/api/ingest", data={"meta": "{}"}, files={"file": ("a.jpg", b"x")})
    check("ingest without token → 401", r.status_code == 401)
    tok = c.post("/api/ext/token").json()["token"]
    H = {"X-Faceoff-Token": tok}
    # safety kill switch: paused by default, ingest refused, control endpoint says paused
    ctl = c.get("/api/ext/control", headers=H).json()
    check("extension paused by default", ctl["paused"] and not ctl["backfill"], ctl)
    check("control requires token", c.get("/api/ext/control").status_code == 401)
    r = c.post("/api/ingest", headers=H, data={"meta": json.dumps({"shortcode": "X", "index": 1})}, files={"file": ("a.jpg", b"x")})
    check("ingest refused while paused (423)", r.status_code == 423 and r.json()["paused"])
    check("known refused while paused (423)", c.post("/api/ingest/known", headers=H, json={"keys": []}).status_code == 423)
    c.post("/api/ext/pause", json={"paused": False})
    ctl = c.get("/api/ext/control", headers=H).json()
    check("resume from UI", not ctl["paused"] and ctl["limits"]["page_min_s"] >= 45 and ctl["limits"]["pages_per_day"] <= 100, ctl["limits"])
    rep = c.post("/api/ext/report", headers=H, json={"kind": "challenge", "message": "checkpoint"}).json()
    check("extension warning report pauses server", rep["paused"] and "challenge" in rep["reason"], rep["reason"])
    c.post("/api/ext/pause", json={"paused": False})
    check("ping with token", c.get("/api/ext/ping", headers=H).json()["ok"])
    check("ping with wrong token → 401", c.get("/api/ext/ping", headers={"X-Faceoff-Token": "nope"}).status_code == 401)
    st = c.get("/api/settings").json()
    check("settings never returns token", "ext_token" not in st["settings"] and tok not in json.dumps(st))
    check("legacy pull settings are gone", not any(k.startswith("ig_") and k != "ig_export" for k in st["settings"]))
    cookie_body = {"cookie": "sessionid=123456789%3AFAKEsession%3A1%3Aabc"}
    for method, path in (("GET", "/api/instagram"), ("POST", "/api/instagram/cookie"),
                         ("DELETE", "/api/instagram/cookie"), ("POST", "/api/instagram/check")):
        r = c.request(method, path, json=cookie_body)
        check(f"{method} {path} is gone", r.status_code == 404, r.status_code)
    html = c.get("/").text
    js = c.get("/static/app.js").text
    check("settings UI is extension-only", "extension-only" in html and "cookieIn" not in html
          and "Paste session cookie" not in html and "/api/instagram" not in html)
    check("app script has no cookie paste", "cookieIn" not in js and "/api/instagram" not in js)
    c.post("/api/settings", json={"ig_enabled": True, "default_mode": "box"})
    st2 = c.get("/api/settings").json()["settings"]
    check("server pull cannot be turned back on", "ig_enabled" not in st2 and st2["default_mode"] == "box")
    c.post("/api/settings", json={"default_mode": "blur"})
    pre = c.options("/api/ingest", headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "POST"})
    check("CORS preflight for extension", pre.status_code == 204 and pre.headers.get("access-control-allow-origin") == "chrome-extension://abc")

    img = open(os.path.join(SAMPLES, "einstein.jpg"), "rb").read()
    grp = open(os.path.join(SAMPLES, "solvay1927.jpg"), "rb").read()
    vid = open(VIDEO, "rb").read()

    def ingest(code, idx, data, kind="image", owner="someone"):
        meta = {"shortcode": code, "index": idx, "owner": owner, "caption": "hello", "taken_at": 1700000000, "kind": kind}
        return c.post("/api/ingest", headers=H, data={"meta": json.dumps(meta)},
                      files={"file": (f"{code}.{'mp4' if kind == 'video' else 'jpg'}", data)}).json()

    r1 = ingest("ABC123", 1, img)
    r2 = ingest("ABC123", 2, grp)
    r3 = ingest("VID999", 1, vid, "video", "filmer")
    check("ingest image → saved", r1["status"] == "saved", r1)
    check("ingest video → saved", r3["status"] == "saved", r3)
    check("re-ingest same key → duplicate", ingest("ABC123", 1, img)["status"] == "duplicate")
    known = c.post("/api/ingest/known", headers=H, json={"keys": ["ABC123:1", "ABC123:2", "NEW:1"]}).json()["known"]
    check("known keys", known == ["ABC123:1", "ABC123:2"], known)

    lst = c.get("/api/saved").json()
    check("saved library has 3 items", lst["total"] == 3, lst["total"])
    check("nothing auto-processed", all(i["proc"] is None for i in lst["items"]) and lst["counts"]["unprocessed"] == 3)
    check("uploads gallery stays empty", c.get("/api/images").json()["items"] == [])
    v = next(i for i in lst["items"] if i["kind"] == "video")
    check("video thumb + duration", v["duration"] and abs(v["duration"] - 4) < 0.6 and
          c.get(f"/api/saved/{v['id']}/file/thumb").status_code == 200, v["duration"])
    check("image thumb", c.get(f"/api/saved/{r1['id']}/file/thumb").headers["content-type"] == "image/jpeg")
    igdir = os.environ["IG_EXPORT_DIR"]
    raw = sorted(os.path.relpath(os.path.join(dp, f), igdir) for dp, _, fs in os.walk(igdir) for f in fs)
    check("raw originals written to IG folder by owner", raw == ["filmer/2023-11-14_filmer_VID999_1.mp4",
                                                                 "someone/2023-11-14_someone_ABC123_1.jpg",
                                                                 "someone/2023-11-14_someone_ABC123_2.jpg"], raw)
    check("raw original bytes untouched", open(os.path.join(igdir, "someone/2023-11-14_someone_ABC123_1.jpg"), "rb").read() == img)
    check("filter videos", c.get("/api/saved?filter=videos").json()["total"] == 1)
    check("ids endpoint (select all filtered)", len(c.get("/api/saved/ids?filter=images").json()["ids"]) == 2)

    # type filter (combinable with owner + sort) and feed id list
    kv = c.get("/api/saved?kind=video").json()
    check("kind=video filter", kv["total"] == 1 and kv["items"][0]["kind"] == "video" and kv["counts"]["all"] == 1, kv["total"])
    ki = c.get("/api/saved?kind=image&owner=someone&sort=oldest").json()
    check("kind=image + owner + oldest", [i["index"] for i in ki["items"]] == [1, 2], [i["index"] for i in ki["items"]])
    check("kind=video + other owner → none", c.get("/api/saved?kind=video&owner=someone").json()["total"] == 0)
    check("feed ids (kind=video)", c.get("/api/saved/ids?kind=video&sort=newest").json()["ids"] == [r3["id"]])
    b = c.get(f"/api/saved/batch?ids={r3['id']},{r1['id']},999").json()["items"]
    check("batch metadata", [i["id"] for i in b] == [r3["id"], r1["id"]])
    # HTTP Range (seeking, iOS Safari)
    url = f"/api/saved/{r3['id']}/file/original"
    full = c.get(url)
    check("video full GET advertises ranges", full.status_code == 200 and full.headers.get("accept-ranges") == "bytes", dict(full.headers).get("accept-ranges"))
    rr = c.get(url, headers={"Range": "bytes=0-1"})
    check("iOS probe bytes=0-1 → 206, 2 bytes", rr.status_code == 206 and rr.content == vid[:2] and rr.headers["content-range"] == f"bytes 0-1/{len(vid)}",
          (rr.status_code, rr.headers.get("content-range")))
    mid = len(vid) // 2
    rr = c.get(url, headers={"Range": f"bytes={mid}-"})
    check("open-ended range from middle", rr.status_code == 206 and rr.content == vid[mid:])
    rr = c.get(url, headers={"Range": f"bytes={len(vid) + 10}-"})
    check("unsatisfiable range → 416", rr.status_code == 416, rr.status_code)
    # Save to device: attachment with @owner_shortcode name, Range intact
    dl = c.get(f"/api/saved/{r3['id']}/file/download")
    cd = dl.headers.get("content-disposition", "")
    check("download: attachment + @owner_shortcode.mp4 (@filmer_VID999.mp4)", dl.status_code == 200 and cd.startswith("attachment;") and 'filename="@filmer_VID999.mp4"' in cd and "filename*=UTF-8''" in cd and dl.content == vid, cd)
    rr = c.get(f"/api/saved/{r3['id']}/file/download", headers={"Range": "bytes=0-1"})
    check("download supports Range (206)", rr.status_code == 206 and rr.headers.get("content-disposition", "").startswith("attachment"), rr.status_code)
    d2 = c.get(f"/api/saved/{r2['id']}/file/download")
    check("photo download keeps original bytes + item index in name", d2.headers["content-type"].startswith("image/") and "_2." in d2.headers["content-disposition"], d2.headers.get("content-disposition"))
    check("inline view unchanged (no attachment on /original)", "content-disposition" not in c.get(url).headers)
    q = c.post("/api/saved/process", json={"ids": [r1["id"], r2["id"], r3["id"]], "mode": "blur"}).json()
    check("process selection: 2 photos queued, video skipped", q["queued"] == 2 and q["skipped_videos"] == 1, q)
    t0 = time.time()
    while time.time() - t0 < 240:
        cnt = c.get("/api/saved").json()["counts"]
        if cnt["processed"] + cnt["errors"] >= 2 and not cnt["working"]:
            break
        time.sleep(1)
    lst = c.get("/api/saved").json()
    check("photos processed, video stays unprocessed", lst["counts"]["processed"] == 2 and lst["counts"]["unprocessed"] == 1, lst["counts"])
    by = {i["id"]: i for i in lst["items"]}
    check("faces found (einstein 1, solvay ≥25)", by[r1["id"]]["proc"]["faces"] == 1 and by[r2["id"]]["proc"]["faces"] >= 25,
          (by[r1["id"]]["proc"]["faces"], by[r2["id"]]["proc"]["faces"]))
    drop = sorted(f for _, _, fs in os.walk(os.environ["EXPORT_DIR"]) for f in fs)
    check("processed outputs exported to Drop (2 jpg, no video)", len(drop) == 2 and all(f.endswith(".jpg") for f in drop), drop)
    check("raw video playable from library", c.get(f"/api/saved/{r3['id']}/file/original").headers["content-type"] == "video/mp4")
    g = c.get("/api/images").json()["items"]
    check("processed saved photos also in gallery with IG owner", len(g) == 2 and all(i["ig"] and i["ig"]["owner"] == "someone" for i in g), len(g))

    gd = c.get(f"/api/images/{by[r1['id']]['proc']['id']}/file/full")
    check("gallery Save: processed jpg attachment named @owner_shortcode_blur.jpg", gd.status_code == 200 and 'filename="@someone_ABC123_blur.jpg"' in gd.headers.get("content-disposition", ""), gd.headers.get("content-disposition"))
    q = c.post("/api/saved/process", json={"ids": [r1["id"]], "mode": "box"}).json()
    t0 = time.time()
    while time.time() - t0 < 60 and c.get(f"/api/saved/{r1['id']}").json()["proc"]["status"] != "done":
        time.sleep(0.5)
    one = c.get(f"/api/saved/{r1['id']}").json()
    check("reprocess as box reuses row", one["proc"]["mode"] == "box" and one["proc"]["id"] == by[r1["id"]]["proc"]["id"])

    # gallery pruning (GALLERY_LIMIT=3 in this test) must never touch Saved results
    up = c.post("/api/upload", data={"mode": "blur"}, files=[("files", (f"u{i}.jpg", img)) for i in range(3)]).json()
    t0 = time.time()
    while time.time() - t0 < 60 and c.get("/api/images").json()["counts"].get("queued", 0) + c.get("/api/images").json()["counts"].get("processing", 0):
        time.sleep(0.5)
    time.sleep(1)
    gal = c.get("/api/images").json()["items"]
    lst = c.get("/api/saved").json()
    check("gallery capped at limit, newest uploads shown", len(gal) == 3 and all(i["source"] == "upload" for i in gal), [i["source"] for i in gal])
    check("prune kept Saved items + their processed results", lst["total"] == 3 and lst["counts"]["processed"] == 2, lst["counts"])
    check("raw saved files intact after prune", all(os.path.exists(store.saved_paths(i["id"], ".jpg" if i["kind"] == "image" else ".mp4")["original"])
                                                    for i in lst["items"]))
    d = c.post("/api/saved/delete", json={"ids": [r2["id"]]}).json()
    check("delete from library", d["deleted"] == 1 and c.get("/api/saved").json()["total"] == 2)
    check("deleted item not re-added", ingest("ABC123", 2, grp)["status"] == "duplicate")
    check("Drop + IG raw files kept after delete", sum(len(fs) for _, _, fs in os.walk(os.environ["EXPORT_DIR"])) >= 2 and
          os.path.exists(os.path.join(igdir, "someone/2023-11-14_someone_ABC123_2.jpg")))
    z = c.get("/api/ext/download")
    check("extension zip downloads", z.status_code == 200 and z.content[:2] == b"PK", len(z.content))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
