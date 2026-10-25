"""Instagram saved-posts puller (session-cookie based, conservative schedule).

- Cookie is stored only in DATA_DIR/ig_cookie.json (chmod 600); never logged, masked in the API.
- Only NEW saved posts are pulled: seen shortcodes are tracked in ig_state.json.
- Schedule: interval (default 6h) + random jitter, a hard minimum floor, small per-run cap,
  random delays between requests, exponential backoff on 429 / challenge, stop on expired cookie.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
import urllib.parse

import requests

from . import store, worker

log = logging.getLogger("faceoff.instagram")

MIN_INTERVAL_HOURS = float(os.environ.get("IG_MIN_INTERVAL_HOURS", "2"))   # floor for the schedule
MIN_CHECK_GAP_MIN = float(os.environ.get("IG_MIN_CHECK_GAP_MIN", "20"))   # floor for "Check now"
MAX_PER_RUN = int(os.environ.get("IG_MAX_PER_RUN", "25"))
SCAN_LIMIT = 36                      # max saved items looked at per run
DELAY_RANGE = (4.0, 11.0)            # seconds between posts
IG_APP_ID = "936619743392459"        # public web app id used by instagram.com
IG_WEB = "https://www.instagram.com"   # overridable in tests only
IG_I = "https://i.instagram.com"
UA = os.environ.get(
    "IG_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)

COOKIE_PATH = os.path.join(store.DATA_DIR, "ig_cookie.json")
STATE_PATH = os.path.join(store.DATA_DIR, "ig_state.json")

_run_lock = threading.Lock()
_state_lock = threading.RLock()


class CookieExpired(Exception):
    pass


class RateLimited(Exception):
    pass


# ---------------------------------------------------------------- cookie parsing
def parse_cookie_input(text: str) -> dict:
    """Accepts: bare sessionid, 'a=b; c=d' header string, Netscape cookies.txt, or JSON export."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty cookie")
    cookies: dict[str, str] = {}
    if text[:1] in "[{":
        data = json.loads(text)
        if isinstance(data, dict) and "cookies" in data:
            data = data["cookies"]
        if isinstance(data, dict):
            cookies = {str(k): str(v) for k, v in data.items()}
        else:
            for c in data:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    if "instagram" in str(c.get("domain", "instagram")):
                        cookies[c["name"]] = str(c["value"])
    elif "\t" in text or text.startswith("# Netscape"):
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and "instagram" in parts[0]:
                cookies[parts[5]] = parts[6].strip()
    elif "=" in text:
        if text.lower().startswith("cookie:"):
            text = text[7:]
        for part in text.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip().strip('"')
    else:
        cookies = {"sessionid": text.strip('"')}
    sid = cookies.get("sessionid", "")
    if not sid or len(sid) < 10:
        raise ValueError("No sessionid found in what you pasted")
    if "ds_user_id" not in cookies:
        m = re.match(r"(\d+)(%3A|:)", sid)
        if m:
            cookies["ds_user_id"] = m.group(1)
    sid = urllib.parse.quote(urllib.parse.unquote(sid), safe="")  # browsers send it %3A-encoded
    cookies["sessionid"] = sid
    return {k: v for k, v in cookies.items() if re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", k) and v}


def mask(v: str) -> str:
    v = urllib.parse.unquote(v or "")
    return (v[:4] + "…" + v[-4:]) if len(v) > 10 else "••••"


def load_cookie() -> dict | None:
    try:
        with open(COOKIE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _store_cookie(c: dict):
    tmp = COOKIE_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(c, f)
    os.replace(tmp, COOKIE_PATH)


def save_cookie(text: str) -> dict:
    c = parse_cookie_input(text)
    _store_cookie(c)
    with _state_lock:
        st = load_state()
        st.update(status="pending", message="Cookie saved. Verifying on next check.", backoff_level=0,
                  rate_limited_until=0, username=None, last_response=None)
        # schedule a verification soon (but respect the check-now floor)
        st["next_run"] = max(time.time() + 15, (st.get("last_run") or 0) + MIN_CHECK_GAP_MIN * 60)
        save_state(st)
    return c


def clear_cookie():
    try:
        os.remove(COOKIE_PATH)
    except FileNotFoundError:
        pass
    with _state_lock:
        st = load_state()
        st.update(status="no_cookie", message="No cookie saved.", username=None)
        save_state(st)


# ---------------------------------------------------------------- state
def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (FileNotFoundError, ValueError):
        st = {}
    st.setdefault("status", "no_cookie")
    st.setdefault("message", "")
    st.setdefault("seen", [])
    st.setdefault("baselined", False)
    st.setdefault("backoff_level", 0)
    st.setdefault("rate_limited_until", 0)
    st.setdefault("total_pulled", 0)
    return st


def save_state(st: dict):
    st["seen"] = st.get("seen", [])[-3000:]
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _set(**kw):
    with _state_lock:
        st = load_state()
        st.update(kw)
        save_state(st)
        return st


def _schedule_next(st: dict, settings: dict, base: float | None = None) -> float:
    hrs = max(MIN_INTERVAL_HOURS, float(settings.get("ig_interval_hours", 6)))
    jitter = random.uniform(-0.1, 0.25) * hrs * 3600 + random.uniform(0, 900)
    return (base or time.time()) + hrs * 3600 + jitter


def public_status() -> dict:
    st = load_state()
    s = store.load_settings()
    c = load_cookie()
    now = time.time()
    status = st["status"]
    if not c:
        status = "no_cookie"
    elif not s["ig_enabled"] and status not in ("cookie_expired", "challenge", "running"):
        status = "paused" if status in ("ok", "pending") else status
    next_check_allowed = max((st.get("last_run") or 0) + MIN_CHECK_GAP_MIN * 60, st.get("rate_limited_until") or 0)
    return {
        "status": status,
        "message": st.get("message", ""),
        "username": st.get("username"),
        "cookie": ({k: mask(v) for k, v in c.items() if k in ("sessionid", "ds_user_id")} if c else None),
        "enabled": s["ig_enabled"],
        "last_run": st.get("last_run"),
        "last_ok": st.get("last_ok"),
        "next_run": st.get("next_run") if s["ig_enabled"] and c and status not in ("cookie_expired", "challenge") else None,
        "cookie_fields": sorted(k for k in (c or {}) if not k.startswith("_")),
        "last_response": {k: v for k, v in (st.get("last_response") or {}).items()},
        "rate_limited_until": st.get("rate_limited_until") or None,
        "check_now_allowed_at": next_check_allowed if next_check_allowed > now else None,
        "seen_count": len(st.get("seen", [])),
        "baselined": st.get("baselined"),
        "total_pulled": st.get("total_pulled", 0),
        "last_new": st.get("last_new", 0),
        "min_interval_hours": MIN_INTERVAL_HOURS,
        "min_check_gap_min": MIN_CHECK_GAP_MIN,
    }


# ---------------------------------------------------------------- Instagram I/O
class Challenge(Exception):
    pass


BROWSER_UA = UA


# ---------------------------------------------------------------- gallery-dl client
# Uses gallery-dl's maintained Instagram REST client:
# gallery-dl's maintained Instagram REST client (Chrome header set + Chrome TLS ciphers, X-IG-App-ID/X-ASBD-ID/
# X-IG-WWW-Claim/X-CSRFToken handling, www.instagram.com/api/v1/...), whoami via /v1/users/{ds_user_id}/info/ and the
# saved feed via /v1/feed/saved/posts/. Retries are off: any refusal stops the run.
GDL_BROWSER = os.environ.get("IG_BROWSER", "chrome:windows")   # browser profile gallery-dl imitates
STOP_MARKERS = ("checkpoint", "challenge", "login_required", "feedback_required", "consent_required", "two_factor")


class GdlClient:
    def __init__(self, cookies: dict, pace=(3.0, 6.0)):
        from gallery_dl.extractor.instagram import InstagramExtractor

        opts = {"cookies": {k: v for k, v in cookies.items() if not k.startswith("_")}, "cookies-update": False,
                "retries": 0, "sleep-request": list(pace), "sleep-429": 0, "timeout": 30.0,
                "user-cache": "memory", "browser": GDL_BROWSER}
        if os.environ.get("IG_USER_AGENT"):
            opts["user-agent"] = os.environ["IG_USER_AGENT"]
        client = self

        class _Extr(InstagramExtractor):
            subcategory = "faceoff"
            pattern = r"faceoff:(saved)"

            def config(self, key, default=None):  # only our options, never a gallery-dl config file
                return opts.get(key, default)

            def request(self, url, **kw):
                client.requests += 1
                return InstagramExtractor.request(self, url, **kw)

        import re as _re
        self.requests = 0
        self.extr = _Extr(_re.match(r"faceoff:(saved)", "faceoff:saved"))
        self.extr.initialize()
        if cookies.get("csrftoken") and not re.fullmatch(r"[0-9a-f]{32}", cookies["csrftoken"]):
            self.extr.csrf_token = cookies["csrftoken"]          # the user's real token, not gallery-dl's made-up one
            self.extr.cookies.set("csrftoken", cookies["csrftoken"], domain=self.extr.cookies_domain)
        self.uid = cookies.get("ds_user_id") or (re.match(r"(\d+)(%3A|:)", cookies.get("sessionid", "")) or [None, None])[1]

    def _call(self, endpoint: str, what: str, **params) -> dict:
        try:
            data = self.extr.api._call(endpoint, params=params or None)
        except Exception as exc:
            raise _classify(exc, what) from None
        _set(last_response={"endpoint": what, "status": 200, "at": time.time()})
        if not isinstance(data, dict):
            raise RuntimeError(f"odd reply from Instagram on {what}")
        msg = str(data.get("message") or "").lower()
        if data.get("status") == "fail" or any(m in msg for m in STOP_MARKERS):
            _set(last_response={"endpoint": what, "status": 200, "message": msg[:120], "at": time.time()})
            _raise_marker(msg, what)
            raise RuntimeError(f"Instagram said the request failed ({what})")
        return data

    def whoami(self) -> str:
        if not self.uid:
            raise CookieExpired("That cookie has no account id (ds_user_id) — paste the full Cookie header.")
        d = self._call(f"/v1/users/{self.uid}/info/", "user_info")
        u = (d.get("user") or {}).get("username")
        if not u:
            raise RuntimeError("Instagram didn't return the account name")
        return u

    def saved(self):
        """Newest-saved first. Yields dicts: shortcode, owner, media=[(url, is_video)]."""
        max_id = None
        n = 0
        while n < SCAN_LIMIT:
            params = {"count": 50 if n == 0 else 50}
            if max_id:
                params["max_id"] = max_id
            d = self._call("/v1/feed/saved/posts/", "saved_posts", **params)
            for it in d.get("items", []):
                m = it.get("media") or it
                code = m.get("code")
                if not code:
                    continue
                media = []
                if m.get("carousel_media"):
                    for cm in m["carousel_media"]:
                        media.append((_best_url(cm), cm.get("media_type") == 2))
                else:
                    media.append((_best_url(m), m.get("media_type") == 2))
                n += 1
                yield {"shortcode": code, "owner": (m.get("user") or {}).get("username", ""), "media": media}
                if n >= SCAN_LIMIT:
                    return
            if not d.get("more_available") or not d.get("next_max_id"):
                return
            max_id = d["next_max_id"]

    def download(self, url: str) -> bytes:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host.endswith((".cdninstagram.com", ".fbcdn.net")) and not host.startswith("127.0.0.1"):
            raise RuntimeError("media host not allowed")
        try:
            r = self.extr.request(url)
        except Exception as exc:
            raise _classify(exc, "media") from None
        return r.content


def _raise_marker(text: str, what: str):
    t = text.lower()
    if "challenge" in t or "checkpoint" in t or "two_factor" in t:
        raise Challenge("Instagram wants a security check. Open instagram.com in your browser, complete it, then paste a fresh cookie.")
    if "login_required" in t or "redirect to login" in t:
        raise CookieExpired("Instagram says this session is logged out (login required). Paste a fresh cookie.")
    if "feedback_required" in t or "consent_required" in t:
        raise Challenge(f"Instagram replied {('feedback' if 'feedback' in t else 'consent')} required — open the app/website and clear it first.")


def _classify(exc: Exception, what: str) -> Exception:
    if isinstance(exc, (CookieExpired, RateLimited, Challenge)):
        return exc
    resp = getattr(exc, "response", None)
    status = int(getattr(exc, "status", 0) or (getattr(resp, "status_code", 0) if resp is not None else 0) or 0)
    text = str(exc)
    body = ""
    info = {"endpoint": what, "status": status or None, "error": type(exc).__name__, "at": time.time()}
    if resp is not None:
        try:
            j = resp.json()
            for k in ("message", "error_type", "require_login"):
                if k in j:
                    info[k] = str(j[k])[:120]
            body = json.dumps(j)[:2000].lower()
        except Exception:
            body = (resp.text or "")[:2000].lower()
    m = re.search(r"redirect to (\w+) page \(([^)]*)\)", text)
    if m:
        info["redirect"] = m.group(1)
        info["location"] = urllib.parse.urlsplit(m.group(2)).path
    elif not info.get("message"):
        info["detail"] = re.sub(r"https?://\S+", "<url>", text)[:160]
    _set(last_response=info)
    log.warning("instagram %s -> %s", what, json.dumps({k: v for k, v in info.items() if k != "at"}))
    low = (text + " " + body).lower()
    if info.get("redirect") == "challenge":
        return Challenge("Instagram redirected to a security check. Open instagram.com in your browser, complete it, then paste a fresh cookie.")
    if info.get("redirect") == "login":
        return CookieExpired("Instagram redirected to the login page — this session is logged out. Paste a fresh cookie.")
    if info.get("redirect") == "home":
        return CookieExpired("Instagram redirected the API call to its home page — it doesn't accept this session as logged in. Paste a fresh cookie.")
    if status == 429 or "wait a few minutes" in low:
        return RateLimited(f"Instagram rate-limited the request (HTTP 429{': ' + info['message'] if info.get('message') else ''})")
    try:
        _raise_marker(low, what)
    except (CookieExpired, Challenge) as e:
        return e
    if status in (401, 403):
        return CookieExpired(f"Instagram refused the session (HTTP {status}). Paste a fresh cookie.")
    if status:
        return RuntimeError(f"Instagram HTTP {status} on {what}")
    return exc


def _best_url(node: dict) -> str | None:
    c = (node.get("image_versions2") or {}).get("candidates") or []
    if not c:
        return None
    c = sorted(c, key=lambda x: x.get("width", 0) * x.get("height", 0), reverse=True)
    return c[0].get("url")


def run_once(manual: bool = False) -> dict:
    """Run a pull. Returns status dict. Never runs concurrently."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "error": "A check is already running"}
    settings = store.load_settings()
    st = load_state()
    try:
        cookies = load_cookie()
        if not cookies:
            _set(status="no_cookie", message="No cookie saved.")
            return {"ok": False, "error": "No cookie saved"}
        now = time.time()
        _set(status="running", message="Checking saved posts…", last_run=now)
        client = GdlClient(cookies)
        if not st.get("last_ok"):
            time.sleep(random.uniform(3.0, 8.0))   # gentle start on a fresh connect
        username = client.whoami()
        _set(username=username)
        seen = set(st.get("seen", []))
        first = not st.get("baselined")
        new = []
        source = "gallery-dl"
        for it in client.saved():
            if it["shortcode"] in seen:
                break  # saved feed is newest-first; everything after this was already handled
            new.append(it)

        mode = settings["ig_mode"] or settings["default_mode"]
        pulled = 0
        if first:
            grab = new[: settings["ig_first_run_grab"]] if settings["ig_first_run"] == "grab" else []
            baseline = [it["shortcode"] for it in new if it not in grab]
            todo = list(reversed(grab))
            st_seen = list(seen) + list(reversed(baseline))
        else:
            todo = list(reversed(new))[: settings["ig_per_run_cap"]]  # oldest-new first, rest next run
            st_seen = list(seen)
        for n, it in enumerate(todo):
            if n:
                time.sleep(random.uniform(*DELAY_RANGE))
            idx = 0
            for url, is_video in it["media"]:
                if not url:
                    continue
                idx += 1
                data = client.download(url)  # for videos this is the cover frame
                name = f"{it['owner'] or 'ig'}_{it['shortcode']}_{idx}" + ("_cover" if is_video else "")
                worker.add_image(data, name, "instagram", mode, ig_shortcode=it["shortcode"], ig_owner=it["owner"])
                pulled += 1
                time.sleep(random.uniform(1.0, 3.0))
            st_seen.append(it["shortcode"])
            _set(seen=st_seen)
        backlog = 0 if first else max(0, len(new) - len(todo))
        msg = (f"Baseline set: {len(new)} existing saved posts marked as seen"
               + (f", grabbed {len(todo)}" if todo else "") + "." if first
               else (f"Pulled {len(todo)} new saved post(s) ({pulled} images)." if todo else "No new saved posts."))
        if backlog:
            msg += f" {backlog} more queued for next run."
        st2 = _set(status="ok", message=msg, username=username, last_ok=time.time(), seen=st_seen, baselined=True,
                   backoff_level=0, rate_limited_until=0, last_new=len(todo),
                   total_pulled=load_state().get("total_pulled", 0) + pulled, feed_source=source)
        nxt = _schedule_next(st2, settings)
        if backlog:  # come back sooner (still >= floor) for the remainder
            nxt = min(nxt, time.time() + MIN_INTERVAL_HOURS * 3600 + random.uniform(0, 900))
        _set(next_run=nxt)
        return {"ok": True, "message": msg, "pulled": pulled}
    except CookieExpired as e:
        _set(status="cookie_expired", message=str(e), next_run=None)
        return {"ok": False, "error": str(e), "status": "cookie_expired"}
    except Challenge as e:
        _set(status="challenge", message=str(e), next_run=None)
        return {"ok": False, "error": str(e), "status": "challenge"}
    except RateLimited as e:
        st = load_state()
        lvl = min(int(st.get("backoff_level", 0)) + 1, 5)
        if not st.get("last_ok"):
            # never succeeded yet: most likely a setup problem, not real throttling -> short back-off
            wait = random.uniform(30 * 60, 60 * 60) * (2 ** (lvl - 1))
        else:
            wait = max(MIN_INTERVAL_HOURS, settings["ig_interval_hours"]) * 3600 * (2 ** (lvl - 1))
            wait += random.uniform(0, 1800)
        wait = min(48 * 3600, wait)
        until = time.time() + wait
        _set(status="rate_limited", message=f"{e}. Backing off ~{wait/3600:.1f}h." if wait >= 5400 else f"{e}. Backing off ~{int(wait/60)} min.", backoff_level=lvl,
             rate_limited_until=until, next_run=until)
        return {"ok": False, "error": str(e), "status": "rate_limited"}
    except Exception as e:
        log.warning("instagram run failed: %s", type(e).__name__)
        msg = str(e)
        if isinstance(e, requests.exceptions.ConnectionError) or "NameResolution" in msg or "Max retries" in msg:
            msg = "Couldn't reach Instagram (network/DNS). Will retry later."
        elif isinstance(e, requests.exceptions.Timeout):
            msg = "Instagram timed out. Will retry later."
        # never echo cookie values back
        for v in (load_cookie() or {}).values():
            if v and len(v) > 6:
                msg = msg.replace(v, "•••")
        _set(status="error", message=f"Check failed: {msg[:200]}", next_run=time.time() + 3600 + random.uniform(0, 900))
        return {"ok": False, "error": msg[:200], "status": "error"}
    finally:
        _run_lock.release()


def check_now() -> tuple[bool, dict]:
    st = load_state()
    now = time.time()
    allowed = max((st.get("last_run") or 0) + MIN_CHECK_GAP_MIN * 60, st.get("rate_limited_until") or 0)
    if not load_cookie():
        return False, {"error": "Paste your Instagram cookie first."}
    if now < allowed:
        return False, {"error": f"Too soon — to stay safe, next check allowed in {int((allowed-now)/60)+1} min.",
                       "allowed_at": allowed}
    threading.Thread(target=run_once, kwargs={"manual": True}, daemon=True, name="ig-manual").start()
    return True, {"message": "Check started"}


def scheduler_loop(stop: threading.Event):
    while not stop.wait(30):
        try:
            s = store.load_settings()
            if not s["ig_enabled"] or not load_cookie():
                continue
            st = load_state()
            if st["status"] in ("cookie_expired", "challenge", "running"):
                continue
            nxt = st.get("next_run")
            if not nxt:
                _set(next_run=time.time() + random.uniform(60, 600))
                continue
            if time.time() >= nxt:
                run_once()
        except Exception:
            log.exception("scheduler tick failed")
