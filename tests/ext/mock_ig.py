"""Tiny HTTPS mock of instagram.com (saved-posts API + CDN) for the extension end-to-end test.
Checks that requests carry X-IG-App-ID, X-CSRFToken matching the csrftoken cookie, and a sessionid cookie."""
import json
import os
import ssl
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(__file__)
SAMPLES = os.path.join(HERE, "..", "samples")
MEDIA = {"einstein.jpg": os.path.join(SAMPLES, "einstein.jpg"), "solvay.jpg": os.path.join(SAMPLES, "solvay1927.jpg"),
         "profile.jpg": os.path.join(SAMPLES, "einstein_exif_rot6.jpg"), "lincoln.png": os.path.join(SAMPLES, "lincoln.png"),
         "clip.mp4": os.path.join(HERE, "..", "video", "clip4s_small.mp4")}
CDN = "https://scontent-test.cdninstagram.com/media/"
PAGE = 3
state = {"posts": [], "mode": "ok", "log": [], "n": 0}
lock = threading.Lock()


def make_post(kind="image"):
    state["n"] += 1
    n = state["n"]
    imgs = list(MEDIA)[:4]

    def img(name):
        return {"image_versions2": {"candidates": [{"width": 320, "height": 320, "url": CDN + name + "?s=small"},
                                                   {"width": 1080, "height": 1080, "url": CDN + name}]}}
    base = {"code": f"P{n:04d}", "user": {"username": f"owner{n % 3}"}, "caption": {"text": f"post {n}"}, "taken_at": 1700000000 + n}
    if kind == "carousel":
        base["carousel_media"] = [img(imgs[n % 4]), {**img("profile.jpg"), "video_versions": [
            {"width": 480, "height": 270, "url": CDN + "clip.mp4?q=low"}, {"width": 640, "height": 360, "url": CDN + "clip.mp4"}]}]
    elif kind == "video":
        base.update(img("einstein.jpg"))
        base["video_versions"] = [{"width": 640, "height": 360, "url": CDN + "clip.mp4"}]
    else:
        base.update(img(imgs[n % 4]))
    return {"media": base}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        host = self.headers.get("Host", "")
        q = parse_qs(u.query)
        if u.path.startswith("/__ctl/"):
            with lock:
                if u.path == "/__ctl/add":
                    for k in q.get("kind", ["image"]):
                        state["posts"].insert(0, make_post(k))
                elif u.path == "/__ctl/mode":
                    state["mode"] = q["m"][0]
                out = {"posts": len(state["posts"]), "mode": state["mode"], "log": state["log"][-50:],
                       "api_calls": sum(1 for x in state["log"] if x["api"])}
            return self._send(200, json.dumps(out))
        if host.startswith("scontent-test") and u.path.startswith("/media/"):
            name = u.path.split("/")[-1]
            if name not in MEDIA:
                return self._send(404, "nope", "text/plain")
            data = open(MEDIA[name], "rb").read()
            return self._send(200, data, "video/mp4" if name.endswith(".mp4") else "image/jpeg")
        if u.path == "/api/v1/feed/saved/posts/":
            ck = SimpleCookie(self.headers.get("Cookie", ""))
            good = (self.headers.get("X-IG-App-ID") == "936619743392459" and "sessionid" in ck and "csrftoken" in ck
                    and self.headers.get("X-CSRFToken") == ck["csrftoken"].value)
            with lock:
                state["log"].append({"api": True, "max_id": q.get("max_id", [None])[0], "good": good})
                if state["mode"] == "429":
                    return self._send(429, json.dumps({"message": "Please wait a few minutes before you try again.", "status": "fail"}))
                if state["mode"] == "challenge":
                    return self._send(400, json.dumps({"message": "challenge_required", "status": "fail"}))
                if not good:
                    return self._send(403, json.dumps({"message": "login_required", "status": "fail"}))
                off = int(q.get("max_id", ["0"])[0] or 0)
                items = state["posts"][off:off + PAGE]
                more = off + PAGE < len(state["posts"])
                body = {"items": items, "more_available": more, "num_results": len(items), "status": "ok"}
                if more:
                    body["next_max_id"] = str(off + PAGE)
            return self._send(200, json.dumps(body))
        if u.path in ("/", "/accounts/"):
            html = ('<!doctype html><html><head><title>Instagram (mock)</title></head><body><h1>mock instagram</h1>'
                    '<script>window.__cfg={"X-IG-App-ID":"936619743392459"}</script></body></html>')
            return self._send(200, html, "text/html", [("Set-Cookie", "csrftoken=mockcsrf123; Path=/; Secure; SameSite=Lax"),
                                                       ("Set-Cookie", "sessionid=mocksession; Path=/; Secure; HttpOnly")])
        return self._send(404, "nope", "text/plain")


def serve(port=8443):
    os.chdir(HERE)
    if not os.path.exists("mock.pem"):
        os.system('openssl req -x509 -newkey rsa:2048 -nodes -keyout mock.key -out mock.pem -days 30 -subj "/CN=www.instagram.com" '
                  '-addext "subjectAltName=DNS:www.instagram.com,DNS:*.cdninstagram.com" 2>/dev/null')
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("mock.pem", "mock.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8443)
