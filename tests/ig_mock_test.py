"""Exercise the Instagram pull path against a local mock of Instagram's endpoints.

Run inside the container:  docker exec faceoff python /app/tests/ig_mock_test.py
Scenarios: baseline -> new posts (carousel + video cover) -> 429 backoff -> expired cookie.
Leaves the app in the 'cookie_expired' state so the UI error can be checked.
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/app")
from app import instagram as ig, store, worker  # noqa

SAMPLES = os.environ.get("SAMPLES", "/app/tests/samples")
MODE = {"v": "ok"}
REQS = []
HDRS = {}
SAVED = [  # newest-saved first
    {"code": "BASE1", "media_type": 1, "user": {"username": "old"}, "img": "einstein.jpg"},
    {"code": "BASE2", "media_type": 1, "user": {"username": "old"}, "img": "einstein.jpg"},
]

def media(code, mt, user, img, car=None):
    url = f"http://127.0.0.1:8899/cdn/{img}"
    m = {"code": code, "media_type": mt, "user": {"username": user},
         "image_versions2": {"candidates": [{"url": url, "width": 1080, "height": 1350}, {"url": url + "?s", "width": 320, "height": 400}]}}
    if car:
        m["carousel_media"] = [{"media_type": t, "image_versions2": {"candidates": [{"url": f"http://127.0.0.1:8899/cdn/{i}", "width": 1080, "height": 1080}]}} for t, i in car]
    return m

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, code, obj, hdr=None):
        b = json.dumps(obj).encode(); self.send_response(code)
        for k, v in (hdr or {}).items(): self.send_header(k, v)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/cdn/"):
            p = os.path.join(SAMPLES, self.path[5:].split("?")[0]); b = open(p, "rb").read()
            self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        assert self.headers.get("Cookie", "").count("sessionid=") == 1
        if self.path == "/":
            REQS.append("home")
            b = b"<html>ok</html>"; self.send_response(200); self.send_header("Set-Cookie", "csrftoken=MockCsrfTokenABCdef0123456789XYZa; Path=/")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        REQS.append(self.path.split("?")[0])
        if self.path.startswith("/api/"):
            assert self.headers.get("X-IG-App-ID") == "936619743392459" and self.headers.get("X-CSRFToken"), "missing web headers"
            assert "Windows" in self.headers.get("User-Agent", "") and "Chrome" in self.headers.get("User-Agent", ""), "UA"
            HDRS.update(dict(self.headers))
        if MODE["v"] == "challenge":
            return self._j(400, {"message": "challenge_required", "challenge": {"url": "x"}, "status": "fail"})
        if MODE["v"] == "expired":
            self.send_response(302); self.send_header("Location", "/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if MODE["v"] == "429":
            return self._j(429, {"message": "Please wait a few minutes before you try again.", "status": "fail"})
        if "/users/123456789/info/" in self.path:
            return self._j(200, {"user": {"username": "test_user"}})
        if "/feed/saved/posts/" in self.path:
            items = [{"media": media(s["code"], s["media_type"], s["user"]["username"], s["img"], s.get("car"))} for s in SAVED]
            return self._j(200, {"items": items, "more_available": False})
        self._j(404, {})

srv = ThreadingHTTPServer(("127.0.0.1", 8899), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
# route gallery-dl's www.instagram.com / CDN traffic to the mock (keeps its real headers + redirect handling)
import requests.adapters
class Fwd(requests.adapters.HTTPAdapter):
    def send(self, req, **kw):
        orig = req.url
        req.url = "http://127.0.0.1:8899" + orig.split("instagram.com", 1)[1] if "instagram.com" in orig else orig
        r = super().send(req, **kw); r.url = orig
        if r.is_redirect and r.headers.get("Location", "").startswith("/"):
            r.headers["Location"] = "https://www.instagram.com" + r.headers["Location"]
        return r
_init = ig.GdlClient.__init__
def _patched(self, *a, **k):
    _init(self, *a, **k); self.extr.session.mount("https://", Fwd()); self.extr.session.mount("http://", Fwd())
ig.GdlClient.__init__ = _patched
ig.DELAY_RANGE = (0.1, 0.2)
_real_sleep = time.sleep
ig.time.sleep = lambda s: None if s > 0.5 else _real_sleep(s)

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name); assert cond, name

# fresh IG state
for p in (ig.STATE_PATH,):
    if os.path.exists(p): os.remove(p)
store.save_settings({"ig_first_run": "baseline", "ig_per_run_cap": 8})
ig.save_cookie("sessionid=123456789%3AFAKEmocksession%3A12%3AAYmock; csrftoken=FakeCsrfTokenForTests000000; ds_user_id=123456789; ig_did=X; rur=Y")

r = ig.run_once(); st = ig.load_state()
check("first run baselines without downloading", r["ok"] and r["pulled"] == 0 and set(st["seen"]) >= {"BASE1", "BASE2"} and st["status"] == "ok")
check("username resolved", st["username"] == "test_user")
check("gallery-dl path: user_info + 1 saved page only", REQS == ["/api/v1/users/123456789/info/", "/api/v1/feed/saved/posts/"])
check("chrome-like headers sent", all(k in HDRS for k in ("X-IG-WWW-Claim", "X-ASBD-ID", "X-Requested-With")) and any(k.lower() == "sec-ch-ua" for k in HDRS))
check("next run scheduled >= floor", st["next_run"] - time.time() >= ig.MIN_INTERVAL_HOURS * 3600 * 0.89)

SAVED[:0] = [
    {"code": "NEWCAR", "media_type": 8, "user": {"username": "sample_crowd"}, "img": "solvay1927.jpg",
     "car": [(1, "solvay1927.jpg"), (2, "solvay1927.jpg"), (1, "lincoln.png")]},
    {"code": "NEWONE", "media_type": 1, "user": {"username": "sample_user"}, "img": "einstein.jpg"},
]
r = ig.run_once(); st = ig.load_state()
check("pulls only the 2 new posts (3 carousel imgs incl. video cover + 1)", r["ok"] and r["pulled"] == 4)
check("new shortcodes marked seen", {"NEWCAR", "NEWONE"} <= set(st["seen"]))
r = ig.run_once()
check("third run: nothing new", r["ok"] and r["pulled"] == 0 and "No new" in r["message"])

MODE["v"] = "429"; r = ig.run_once(); st = ig.load_state()
check("429 after success -> rate_limited + backoff >= interval", st["status"] == "rate_limited" and st["rate_limited_until"] - time.time() >= 2 * 3600)
check("429 reason recorded", st["last_response"]["status"] == 429 and "wait" in st["last_response"]["message"])
ok, info = ig.check_now()
check("check-now refused during backoff", not ok and "Too soon" in info["error"])

# first-ever 429 backs off short
saved_state = ig.load_state(); ig._set(last_ok=None, backoff_level=0)
r = ig.run_once(); st = ig.load_state()
check("first-ever 429 -> short back-off (<= 60 min)", st["status"] == "rate_limited" and st["rate_limited_until"] - time.time() <= 3600 + 5)
ig._set(last_ok=saved_state.get("last_ok"))
MODE["v"] = "challenge"; r = ig.run_once(); st = ig.load_state()
check("challenge_required -> challenge state (not 429)", st["status"] == "challenge" and not st.get("next_run"))
MODE["v"] = "expired"; r = ig.run_once(); st = ig.load_state()
check("expired cookie -> cookie_expired state, schedule stopped", st["status"] == "cookie_expired" and not st.get("next_run"))
pub = ig.public_status()
check("cookie masked in API", "AYmock" not in json.dumps(pub) and pub["cookie"]["sessionid"].count("…") == 1)

deadline = time.time() + 60
while time.time() < deadline and any(i["status"] in ("queued", "processing") for i in store.list_images()):
    time.sleep(0.5)
print("IG images in gallery:", [(i["orig_name"], i["status"], i["faces"]) for i in store.list_images() if i["source"] == "instagram"])
print("ALL IG MOCK TESTS PASSED")
