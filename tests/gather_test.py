"""Personal URL gatherer. No Instagram, no live sites — fixtures and a local page only.

Run: python -m tests.gather_test
"""
import io
import os
import sys
import tempfile
import threading
import time
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.getLogger("httpx").setLevel(logging.WARNING)

ROOT = tempfile.mkdtemp(prefix="faceoff-gather-")
os.environ["DATA_DIR"] = os.path.join(ROOT, "data")
os.environ["EXPORT_DIR"] = os.path.join(ROOT, "export")
os.environ["IG_EXPORT_DIR"] = os.path.join(ROOT, "ig")
os.environ["IG_EXPORT_ENABLED"] = "1"
os.environ["GALLERY_LIMIT"] = "1"
os.environ["GATHER_DELAY_S"] = "0"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["EXPORT_DIR"], exist_ok=True)
os.makedirs(os.environ["IG_EXPORT_DIR"], exist_ok=True)

MODEL = os.path.join(ROOT, "yunet.onnx")
os.environ["YUNET_MODEL"] = MODEL
if not os.path.exists(MODEL) or os.path.getsize(MODEL) < 200000:
    import urllib.request
    url = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    urllib.request.urlretrieve(url, MODEL)

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import gather, main, store, worker  # noqa: E402
from app.gather import FetchResult, Limits  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))
    ok += bool(cond)
    fail += (not cond)


def jpeg(size, color):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def webp(size, color):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "WEBP", quality=80)
    return buf.getvalue()


def gif(size, color):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "GIF")
    return buf.getvalue()


BIG = jpeg(320, (20, 80, 140))
OTHER = jpeg(320, (180, 40, 40))
SMALL = jpeg(32, (0, 0, 0))

# --- URL parsing -----------------------------------------------------------
urls, errs = gather.parse_start_urls("""
https://ex.test/a
https://ex.test/a
http://ex.test/b
ftp://ex.test/c
javascript:alert(1)
https://user:pass@ex.test/secret
https://www.instagram.com/p/xyz
https://scontent.cdninstagram.com/v/img.jpg
<https://ex.test/c>
""")
check("parse keeps http(s) and drops duplicates", urls == ["https://ex.test/a", "http://ex.test/b", "https://ex.test/c"], urls)
msgs = " ".join(e["error"] for e in errs)
check("instagram hosts are refused with a clear message", "Instagram" in msgs and "extension" in msgs, msgs)
check("password URLs are refused", "password" in msgs, msgs)
check("non-http URLs are refused", "Only http and https" in msgs, msgs)
try:
    gather.parse_start_urls("x" * 20001)
    check("huge paste is rejected", False)
except gather.BadInput:
    check("huge paste is rejected", True)

check("canonical drops fragment and default port",
      gather.canonicalize("https://EX.test:443/a/b#frag") == "https://ex.test/a/b")
check("canonical rejects userinfo", gather.canonicalize("https://a:b@ex.test/") is None)
check("same origin ignores path", gather.same_origin("https://ex.test/a", "https://ex.test/b?q=1"))
check("scheme and host break origin",
      not gather.same_origin("https://ex.test/a", "http://ex.test/a")
      and not gather.same_origin("https://ex.test/a", "https://www.ex.test/a")
      and not gather.same_origin("https://ex.test/a", "https://ex.test:8443/a"))
check("explicit 443 matches default https", gather.same_origin("https://ex.test/a", "https://ex.test:443/a"))

# --- image URL extraction and filtering ------------------------------------
html = """<html><head>
<base href="/sub/">
<meta property="og:image" content="/og.jpg">
<meta name="twitter:image" content="https://cdn.ex.test/tw.jpg">
</head><body>
<img src="/tiny.gif" width="16" height="16">
<img src="/favicon.ico">
<img src="sprite.svg">
<img src="/placeholder.gif" data-src="lazy.jpg">
<img srcset="a.jpg 100w, b.jpg 800w">
<a href="next">next</a>
<a href="https://other.test/nope">out</a>
<a href="/secret" rel="nofollow">no</a>
<a href="mailto:a@b.c">mail</a>
<a href="photo.jpg">file</a>
</body></html>"""
images, links = gather.extract_page(html, "https://ex.test/page")
got_imgs = [c.url for c in images]
check("extracts og, twitter, lazy, srcset winner, linked photo",
      got_imgs == ["https://ex.test/og.jpg", "https://cdn.ex.test/tw.jpg", "https://ex.test/tiny.gif",
                   "https://ex.test/favicon.ico", "https://ex.test/sub/sprite.svg", "https://ex.test/sub/lazy.jpg",
                   "https://ex.test/sub/b.jpg", "https://ex.test/sub/photo.jpg"], got_imgs)
check("srcset keeps the 800w width", [c.srcset_w for c in images if c.url.endswith("/b.jpg")] == [800])
check("tiny declared image is extracted for the size filter",
      any(c.url.endswith("/tiny.gif") and c.declared_w == 16 and c.declared_h == 16 for c in images))
check("page links stay on the extractor (origin filter is the crawl's job)",
      "https://ex.test/sub/next" in links and "https://other.test/nope" in links and "https://ex.test/secret" not in links, links)
check("favicon and svg are not downloaded later",
      gather.skip_image_url("https://ex.test/favicon.ico") == "icon"
      and gather.skip_image_url("https://ex.test/sprite.svg") == "icon")
check("instagram image URL is skipped", gather.skip_image_url("https://scontent.cdninstagram.com/v/a.jpg") == "instagram")

check("big jpeg passes", gather.image_rejection(BIG, "image/jpeg", min_px=256, min_bytes=64) is None)
check("big webp passes", gather.image_rejection(webp(300, (1, 2, 3)), "image/webp", min_px=256, min_bytes=64) is None)
check("big gif passes", gather.image_rejection(gif(300, (4, 5, 6)), "image/gif", min_px=256, min_bytes=64) is None)
rej = gather.image_rejection(SMALL, "image/jpeg", min_px=256, min_bytes=64)
check("32px jpeg is rejected", rej and "32×32" in rej, rej)
check("svg is rejected", gather.image_rejection(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", min_px=256, min_bytes=64))
check("tiny payload is rejected", gather.image_rejection(b"\xff\xd8\xff" + b"x" * 10, "image/jpeg", min_px=256, min_bytes=64))

# --- crawl caps, origin, robots --------------------------------------------
def site(pages, files=None, robots=b"", robots_status=404):
    files = files or {}
    seen = []

    def fetch(url, timeout, max_bytes):
        seen.append(url)
        if url.endswith("/robots.txt"):
            if robots_status >= 400:
                return FetchResult(url, url, robots_status, "text/plain", b"", None)
            return FetchResult(url, url, 200, "text/plain", robots, None)
        if url in files:
            body = files[url]
            return FetchResult(url, url, 200, "image/jpeg", body, None)
        if url in pages:
            return FetchResult(url, url, 200, "text/html; charset=utf-8", pages[url].encode(), None)
        return FetchResult(url, url, 404, "text/html", b"", None)

    return fetch, seen


home = """<html><head><meta property="og:image" content="/og.jpg"></head><body>
<img src="/tiny.gif" width="16" height="16">
<img src="/favicon.ico">
<img src="/sprite.svg">
<img data-src="/lazy.jpg">
<img srcset="/a.jpg 100w, /b.jpg 800w">
<img srcset="/only-small.jpg 80w">
<img src="https://scontent.cdninstagram.com/v/a.jpg">
<a href="/a">a</a>
<a href="https://other.test/no">out</a>
<a href="/secret" rel="nofollow">nf</a>
<a href="https://www.instagram.com/p/abc">ig</a>
</body></html>"""
pages = {
    "https://ex.test/": home,
    "https://ex.test/a": "<html><a href='/a/b'>b</a></html>",
    "https://ex.test/a/b": "<html><a href='/a/b/c'>c</a><img src='/from-deep.jpg'></html>",
    "https://ex.test/a/b/c": "<html>too deep</html>",
}
files = {
    "https://ex.test/og.jpg": BIG,
    "https://ex.test/lazy.jpg": OTHER,
    "https://ex.test/b.jpg": BIG,
    "https://ex.test/from-deep.jpg": OTHER,
}
fetch, seen = site(pages, files)
got_ids = []

def on_image(data, page, image):
    got_ids.append((page, image, len(data)))
    return {"status": "ok", "saved_id": len(got_ids)}

summary = gather.crawl(["https://ex.test/"], fetch, limits=Limits(max_depth=2, max_pages=25, max_images=200,
                       max_fetches=400, delay_s=0, min_px=256, min_bytes=64), on_image=on_image)
fetched = set(seen)
check("does not leave the origin", not any("other.test" in u for u in fetched), sorted(fetched))
check("does not fetch instagram", not any("instagram" in u for u in fetched), sorted(fetched))
check("does not pass depth 2", "https://ex.test/a/b/c" not in fetched, sorted(fetched))
check("does not fetch nofollow, icons, or the small srcset candidate",
      "https://ex.test/secret" not in fetched and "https://ex.test/favicon.ico" not in fetched
      and "https://ex.test/sprite.svg" not in fetched and "https://ex.test/a.jpg" not in fetched
      and "https://ex.test/tiny.gif" not in fetched and "https://ex.test/only-small.jpg" not in fetched)
check("visits the in-depth page and its image",
      "https://ex.test/a/b" in fetched and "https://ex.test/from-deep.jpg" in fetched)
check("ingests the four large images once", summary["images_ingested"] == 4 and len(got_ids) == 4,
      (summary, [i[1] for i in got_ids]))
check("records an instagram skip", any("Instagram" in e["error"] for e in summary["errors"]), summary["errors"])

many = {"https://ex.test/": "".join(f"<a href='/p{i}'>p</a>" for i in range(40))}
for i in range(40):
    many[f"https://ex.test/p{i}"] = "<html>page</html>"
fetch, seen = site(many)
summary = gather.crawl(["https://ex.test/"], fetch, limits=Limits(max_depth=2, max_pages=3, max_images=200,
                       max_fetches=400, delay_s=0, min_px=256, min_bytes=64))
page_hits = [u for u in seen if not u.endswith("/robots.txt")]
check("page cap is 3", summary["pages"] == 3 and len(page_hits) == 3, (summary["pages"], page_hits))

flag = {"n": 0}

def stopping_fetch(url, timeout, max_bytes):
    flag["n"] += 1
    if url.endswith("/robots.txt"):
        return FetchResult(url, url, 404, "text/plain", b"", None)
    return FetchResult(url, url, 200, "text/html", b"<html><a href='/more'>m</a></html>", None)

summary = gather.crawl(["https://ex.test/"], stopping_fetch, limits=Limits(delay_s=0, max_pages=25),
                       stop=lambda: flag["n"] >= 2)
check("stop flag halts before the next page", summary["pages"] == 1 and summary["stopped"] and flag["n"] == 2,
      (summary["pages"], flag["n"], summary["stopped"]))

robots = b"User-agent: *\nDisallow: /private\n"
fetch, seen = site({"https://ex.test/private": "<html>no</html>", "https://ex.test/ok": "<html><img src='/ok.jpg'></html>"},
                   {"https://ex.test/ok.jpg": BIG}, robots, 200)
summary = gather.crawl(["https://ex.test/private", "https://ex.test/ok"], fetch,
                       limits=Limits(delay_s=0, min_px=256, min_bytes=64),
                       on_image=lambda d, p, i: {"status": "ok"})
check("robots.txt blocks /private and allows /ok",
      "https://ex.test/private" not in seen and "https://ex.test/ok" in seen and "https://ex.test/ok.jpg" in seen
      and any("robots.txt" in e["error"] for e in summary["errors"]), (seen, summary["errors"]))

fetch, seen = site({})
summary = gather.crawl(["https://ex.test/missing"], lambda url, timeout, max_bytes: (
    FetchResult(url, url, 404, "text/plain", b"", None) if url.endswith("/robots.txt")
    else FetchResult(url, url, 403, "text/html", b"", None)), limits=Limits(delay_s=0))
check("403 is a clear error and does not raise", summary["pages"] == 1 and any("403" in e["error"] for e in summary["errors"]),
      summary["errors"])
summary = gather.crawl(["https://ex.test/file.pdf"], lambda url, timeout, max_bytes: (
    FetchResult(url, url, 404, "text/plain", b"", None) if url.endswith("/robots.txt")
    else FetchResult(url, url, 200, "application/pdf", b"%PDF-1.4", None)), limits=Limits(delay_s=0))
check("non-HTML is a clear error", any("Not an HTML page" in e["error"] for e in summary["errors"]), summary["errors"])
summary = gather.crawl(["https://ex.test/slow"], lambda url, timeout, max_bytes: (
    FetchResult(url, url, 404, "text/plain", b"", None) if url.endswith("/robots.txt")
    else FetchResult(url, url, 0, "", b"", "Timed out")), limits=Limits(delay_s=0))
check("timeout is a clear error", any(e["error"] == "Timed out" for e in summary["errors"]), summary["errors"])

old_delay = os.environ.pop("GATHER_DELAY_S")
check("default delay is 1s and page cap clamps", gather.limits_from_env().delay_s == 1 and gather.limits_from_env().max_depth == 2)
os.environ["GATHER_MAX_PAGES"] = "99999"
check("page cap cannot be turned off", gather.limits_from_env().max_pages == 100)
os.environ.pop("GATHER_MAX_PAGES")
os.environ["GATHER_DELAY_S"] = old_delay

# --- ingest hook uses the existing queue and Drop export -------------------
check("url source exports to Drop, not the IG folder",
      worker._target_dir({"source": "url"}, store.load_settings()) == worker.EXPORT_DIR
      and worker._target_dir({"source": "extension"}, store.load_settings()) == worker.IG_EXPORT_DIR)

with TestClient(main.app) as c:
    caps = c.get("/api/gather").json()
    check("gather status is idle and reports caps", caps["job"] is None and caps["caps"]["max_images"] == 200, caps["caps"])
    r = c.post("/api/gather", json={"urls": "   "})
    check("empty paste is 400", r.status_code == 400, r.status_code)
    r = c.post("/api/gather", json={"urls": "https://www.instagram.com/p/abc"})
    check("instagram-only job finishes without fetching",
          r.status_code == 200 and r.json()["job"]["status"] == "done" and "Instagram" in r.json()["job"]["errors"][0]["error"],
          r.json()["job"])

    gate = threading.Event()
    started = threading.Event()
    real_get = gather.http_get

    def blocked(url, timeout, max_bytes):
        if str(url).endswith("/robots.txt"):
            return FetchResult(url, url, 404, "text/plain", b"", None)
        started.set()
        gate.wait(5)
        return FetchResult(url, url, 200, "text/html", b"<html></html>", None)

    gather.http_get = blocked
    try:
        r = c.post("/api/gather", json={"urls": "https://ex.test/blocked", "mode": "box"})
        check("start returns a running job", r.status_code == 200 and r.json()["job"]["status"] == "running", r.json()["job"])
        check("blocked fetch actually started", started.wait(3))
        again = c.post("/api/gather", json={"urls": "https://ex.test/other"})
        check("second start is 409", again.status_code == 409, again.status_code)
        stopped = c.post("/api/gather/cancel")
        check("cancel is accepted", stopped.status_code == 200 and stopped.json()["job"]["status"] in ("cancelling", "cancelled", "running"),
              stopped.json()["job"]["status"])
    finally:
        gate.set()
        gather.http_get = real_get
    t0 = time.time()
    status = ""
    while time.time() - t0 < 8:
        status = c.get("/api/gather").json()["job"]["status"]
        if status in ("cancelled", "done", "error"):
            break
        time.sleep(0.05)
    check("cancel stops the job", status == "cancelled", status)

    hang_hit = threading.Event()
    hang_release = threading.Event()

    class HangHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            hang_hit.set()
            hang_release.wait(15)
            try:
                self.send_response(204)
                self.end_headers()
            except Exception:
                pass

        def log_message(self, fmt, *args):
            return

    hangd = ThreadingHTTPServer(("127.0.0.1", 0), HangHandler)
    threading.Thread(target=hangd.serve_forever, daemon=True).start()
    os.environ["GATHER_TIMEOUT_S"] = "30"
    try:
        r = c.post("/api/gather", json={"urls": f"http://127.0.0.1:{hangd.server_address[1]}/slow", "mode": "box"})
        check("hanging gather is running", r.status_code == 200 and hang_hit.wait(3), r.status_code)
        t0 = time.time()
        c.post("/api/gather/cancel")
        st = ""
        while time.time() - t0 < 3:
            st = c.get("/api/gather").json()["job"]["status"]
            if st == "cancelled":
                break
            time.sleep(0.05)
        waited = time.time() - t0
        check("stop interrupts the current request", st == "cancelled" and waited < 2, (st, round(waited, 2)))
    finally:
        hang_release.set()
        hangd.shutdown()
        os.environ["GATHER_TIMEOUT_S"] = "15"

    hooked = gather.ingest_image(BIG, "https://ex.test/page", "https://cdn.ex.test/pic.jpg", "box")
    check("ingest hook queues a saved still", hooked["status"] == "ok" and hooked["saved_id"] and hooked["image_id"], hooked)
    dup = gather.ingest_image(BIG, "https://ex.test/other", "https://cdn.ex.test/other.jpg", "box")
    check("same bytes are not ingested twice", dup["status"] == "skip", dup)
    dup_url = gather.ingest_image(OTHER, "https://ex.test/page", "https://cdn.ex.test/pic.jpg", "box")
    check("same image URL is not ingested twice", dup_url["status"] == "skip", dup_url)
    t0 = time.time()
    rec = None
    while time.time() - t0 < 90:
        rec = store.get_image(hooked["image_id"])
        if rec and rec["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    check("existing worker finished the gathered image", rec and rec["status"] == "done" and rec["source"] == "url",
          None if not rec else (rec["status"], rec.get("error"), rec["source"]))
    files_out = sorted(f for dp, _, fs in os.walk(os.environ["EXPORT_DIR"]) for f in fs)
    check("Drop export uses the url label and box mode",
          any("_url_" in f and f.endswith("_box.jpg") for f in files_out), files_out)
    check("no raw copy was written to the IG folder",
          not any(os.path.isfile(os.path.join(dp, f)) for dp, _, fs in os.walk(os.environ["IG_EXPORT_DIR"]) for f in fs))
    pub = c.get("/api/images").json()["items"]
    # gallery keeps the newest item; the gathered row itself must still be source url
    one = c.get(f"/api/images/{hooked['image_id']}").json()
    check("gallery record keeps the page and image URL",
          one["source"] == "url" and one["ref"]["page"] == "https://ex.test/page" and one["ref"]["image"].endswith("/pic.jpg")
          and not one["ig"], one.get("ref"))
    check("Saved list stays empty of gathered refs", c.get("/api/saved").json()["total"] == 0)

    from app import saved as saved_mod
    saved_mod.add(OTHER, "ext:1", "ABC123", 1, "someone", "hello", None, "image")
    listed = c.get("/api/saved").json()
    check("extension saves still show in Saved", listed["total"] == 1 and listed["items"][0]["owner"] == "someone"
          and listed["items"][0].get("source") != "url", listed["total"])
    ig_files = [f for dp, _, fs in os.walk(os.environ["IG_EXPORT_DIR"]) for f in fs]
    check("extension raw copy still goes to the IG folder", len(ig_files) == 1, ig_files)
    up = c.post("/api/upload", data={"mode": "box"}, files=[("files", ("newer.jpg", OTHER))])
    check("upload still accepts a photo", up.status_code == 200 and up.json()["ids"], up.status_code)
    t0 = time.time()
    while time.time() - t0 < 90:
        newer = store.get_image(up.json()["ids"][0])
        if newer and newer["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    store.prune()
    check("gallery prune keeps the gathered image row", store.get_image(hooked["image_id"]) is not None
          and newer and newer["created"] > store.get_image(hooked["image_id"])["created"])

    cookies = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cookies.append(self.headers.get("Cookie"))
            path = self.path.split("?", 1)[0]
            if path == "/robots.txt":
                self.send_response(404)
                self.send_header("Set-Cookie", "a=b; Path=/")
                self.end_headers()
                return
            if path == "/pic.jpg":
                body, ctype = OTHER, "image/jpeg"
            elif path == "/next":
                body, ctype = b"<html><p>next</p></html>", "text/html; charset=utf-8"
            elif path == "/":
                body = b"""<html><body><img src="/pic.jpg"><a href="/next">n</a>
<a href="http://127.0.0.1:1/off">off</a>
<img src="https://cdninstagram.com/x.jpg"></body></html>"""
                ctype = "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "session=secret; Path=/")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        r = c.post("/api/gather", json={"urls": f"http://127.0.0.1:{port}/\nhttps://instagram.com/p/nope", "mode": "box"})
        check("live gather starts", r.status_code == 200 and r.json()["job"]["status"] == "running", r.status_code)
        t0 = time.time()
        job = r.json()["job"]
        while time.time() - t0 < 20:
            job = c.get("/api/gather").json()["job"]
            if job["status"] in ("done", "cancelled", "error"):
                break
            time.sleep(0.05)
        check("live gather finishes and ingests the picture",
              job["status"] == "done" and job["images_ingested"] == 1 and job["pages"] == 2, job)
        check("live gather reports instagram and not the off-site port",
              any("Instagram" in e["error"] for e in job["errors"]) and not any(":1/" in (e.get("url") or "") for e in job["errors"]),
              job["errors"])
        check("requests did not send cookies", cookies and all(h is None for h in cookies), cookies)
        sid = job["ingested"][0]["saved_id"]
        t0 = time.time()
        while time.time() - t0 < 90:
            row = store.saved_get(sid)
            img = store.get_image(row["image_id"]) if row and row.get("image_id") else None
            if img and img["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        check("live image went through the worker into Drop",
              img and img["status"] == "done" and img["source"] == "url" and bool(img.get("export_path"))
              and os.path.isfile(img["export_path"]), None if not img else (img["status"], img.get("error"), img.get("export_path")))
    finally:
        httpd.shutdown()

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
