"""Personal URL gatherer.

Visits pages you paste, keeps usable reference images, and hands each one to the
existing Saved library. Saved ingest does not auto-process (Instagram saves wait
until you press Blur or Box), so each gathered still is then queued with
saved.process. That is the same worker that writes redacted files to /export.
Nothing here copies files into Drop on its own.

One page at a time. Same-origin links only. robots.txt is honored. No cookies,
no passwords, no Instagram.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, urljoin
from urllib.robotparser import RobotFileParser

import httpx
from PIL import Image

from . import saved, store

log = logging.getLogger("faceoff.gather")

USER_AGENT = "FaceoffGather/1.0 (personal; self-hosted)"
INSTAGRAM_MSG = "Instagram is skipped. Use the Faceoff Sync extension — this gatherer does not contact Instagram."
RASTER_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
RASTER_TYPES = {"image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp", "image/gif"}
SKIP_EXT = RASTER_EXT | {
    ".svg", ".ico", ".avif", ".heic", ".heif", ".bmp", ".tif", ".tiff",
    ".pdf", ".zip", ".gz", ".mp4", ".webm", ".mov", ".mp3", ".m4a",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot",
}
ICON_NAME = ("favicon", "apple-touch-icon", "sprite")
MAX_HTML = 2_000_000
MAX_ROBOTS = 512_000
MAX_SEEDS = 20
MAX_URL = 2000


class Busy(Exception):
    pass


class BadInput(Exception):
    pass


class _NoStoreCookies(httpx.Cookies):
    """Drop Set-Cookie and never send a Cookie header."""

    def extract_cookies(self, response):  # noqa: ARG002
        return None

    def set_cookie_header(self, request):
        request.headers.pop("cookie", None)


@dataclass
class Limits:
    max_depth: int = 2
    max_pages: int = 25
    max_images: int = 200
    max_fetches: int = 400
    delay_s: float = 1.0
    timeout_s: float = 15.0
    min_px: int = 256
    min_bytes: int = 64
    max_html: int = MAX_HTML


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    error: str | None


@dataclass
class Candidate:
    url: str
    declared_w: int | None = None
    declared_h: int | None = None
    srcset_w: int | None = None


def _clamp_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _clamp_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        n = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def limits_from_env() -> Limits:
    images = _clamp_int("GATHER_MAX_IMAGES", 200, 1, 500)
    return Limits(
        max_depth=_clamp_int("GATHER_MAX_DEPTH", 2, 0, 4),
        max_pages=_clamp_int("GATHER_MAX_PAGES", 25, 1, 100),
        max_images=images,
        max_fetches=_clamp_int("GATHER_MAX_IMAGE_FETCHES", max(400, images), 1, 2000),
        delay_s=_clamp_float("GATHER_DELAY_S", 1.0, 0.0, 30.0),
        timeout_s=_clamp_float("GATHER_TIMEOUT_S", 15.0, 3.0, 60.0),
        min_px=_clamp_int("GATHER_MIN_PX", 256, 32, 4000),
        min_bytes=_clamp_int("GATHER_MIN_BYTES", 64, 0, 1_000_000),
    )


def caps_public() -> dict:
    lim = limits_from_env()
    return {
        "max_depth": lim.max_depth, "max_pages": lim.max_pages, "max_images": lim.max_images,
        "max_fetches": lim.max_fetches, "delay_s": lim.delay_s, "timeout_s": lim.timeout_s,
        "min_px": lim.min_px, "min_bytes": lim.min_bytes,
    }


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def instagram_host(url_or_host: str) -> bool:
    host = url_or_host if "://" not in url_or_host else _host(url_or_host)
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return True
    return "cdninstagram" in host


def canonicalize(url: str) -> str | None:
    raw = (url or "").strip()
    if len(raw) > MAX_URL or not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    try:
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None
    if not host or host == ".":
        return None
    port = parts.port
    netloc = host
    if port and not ((parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


def same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlsplit(a), urlsplit(b)
    except ValueError:
        return False
    if pa.scheme != pb.scheme:
        return False
    if (pa.hostname or "").lower() != (pb.hostname or "").lower():
        return False
    def _port(p):
        if p.port:
            return p.port
        return 443 if p.scheme == "https" else 80
    try:
        return _port(pa) == _port(pb)
    except ValueError:
        return False


def origin_root(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/", "", ""))


def robots_url_for(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))


def _ext(url: str) -> str:
    path = urlsplit(url).path.lower()
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    return "." + path.rsplit(".", 1)[-1]


def skip_image_url(url: str) -> str | None:
    """None when the URL is worth downloading. A short reason otherwise."""
    canon = canonicalize(url)
    if not canon:
        return "not an http(s) image"
    if instagram_host(canon):
        return "instagram"
    ext = _ext(canon)
    name = urlsplit(canon).path.lower().rsplit("/", 1)[-1]
    if ext in {".svg", ".ico"} or any(k in name for k in ICON_NAME):
        return "icon"
    if ext and ext not in RASTER_EXT and ext in SKIP_EXT:
        return "not a reference image"
    return None


def _px(value: str | None) -> int | None:
    if not value:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(px)?\s*$", str(value), re.I)
    if not m:
        return None
    n = int(float(m.group(1)))
    return n if n > 0 else None


def best_srcset(value: str) -> tuple[str, int | None]:
    """Pick the largest candidate. Width descriptors (800w) win over density."""
    best_url, best_score, best_w = "", -1.0, None
    for part in (value or "").split(","):
        bits = part.strip().split()
        if not bits or bits[0].lower().startswith(("data:", "blob:", "javascript:")):
            continue
        url, score, width = bits[0], 1.0, None
        if len(bits) > 1:
            d = bits[-1]
            try:
                if d.endswith("w"):
                    width = int(float(d[:-1]))
                    score = float(width)
                elif d.endswith("x"):
                    score = float(d[:-1]) * 10000
            except ValueError:
                score = 1.0
        if url and score >= best_score:
            best_url, best_score, best_w = url, score, width
    return best_url, best_w


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = ""
        self.images: list[Candidate] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "base" and a.get("href") and not self.base:
            self.base = a["href"].strip()
            return
        if tag == "img":
            srcset_url, srcset_w = "", None
            for key in ("srcset", "data-srcset", "data-lazy-srcset"):
                if a.get(key):
                    srcset_url, srcset_w = best_srcset(a[key])
                    if srcset_url:
                        break
            dw, dh = _px(a.get("width")), _px(a.get("height"))
            raw = srcset_url
            if not raw:
                for key in ("data-src", "data-lazy-src", "data-original", "data-lazy", "src"):
                    if a.get(key) and not a[key].strip().lower().startswith(("data:", "blob:", "javascript:")):
                        raw = a[key].strip()
                        break
            if raw:
                self.images.append(Candidate(raw, dw, dh, srcset_w if srcset_url else None))
            return
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
                if a.get("content"):
                    self.images.append(Candidate(a["content"].strip()))
            return
        if tag == "link":
            rel = a.get("rel", "").lower().split()
            href = a.get("href", "").strip()
            if href and ("image_src" in rel or ("preload" in rel and a.get("as", "").lower() == "image")):
                self.images.append(Candidate(href))
            return
        if tag == "a":
            href = a.get("href", "").strip()
            if not href or "nofollow" in a.get("rel", "").lower().split():
                return
            if href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "blob:")):
                return
            self.links.append(href)


def extract_page(html: str, page_url: str) -> tuple[list[Candidate], list[str]]:
    """Absolute image candidates and absolute http(s) links from one HTML page."""
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    base = page_url
    if parser.base:
        joined = canonicalize(urljoin(page_url, parser.base))
        if joined:
            base = joined
    images, seen = [], set()
    for cand in parser.images:
        absu = canonicalize(urljoin(base, cand.url.strip()))
        if not absu or absu in seen:
            continue
        seen.add(absu)
        images.append(Candidate(absu, cand.declared_w, cand.declared_h, cand.srcset_w))
    links, seen_l = [], set()
    for href in parser.links:
        absu = canonicalize(urljoin(base, href))
        if not absu or absu in seen_l:
            continue
        seen_l.add(absu)
        ext = _ext(absu)
        if ext in RASTER_EXT:
            if absu not in seen:
                seen.add(absu)
                images.append(Candidate(absu))
            continue
        if ext in SKIP_EXT:
            continue
        links.append(absu)
    return images, links


def sniff_raster(data: bytes) -> str | None:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


def raster_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception:
        return None
    if not w or not h or w > 20000 or h > 20000:
        return None
    return int(w), int(h)


def image_rejection(data: bytes, content_type: str, *, min_px: int, min_bytes: int) -> str | None:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in ("image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"):
        return "not a reference image"
    if len(data) < min_bytes:
        return "file is too small"
    ext = sniff_raster(data)
    if ext is None:
        return "not a jpeg, png, webp, or gif"
    if ctype.startswith("image/") and ctype not in RASTER_TYPES:
        return "not a jpeg, png, webp, or gif"
    wh = raster_size(data)
    if not wh:
        return "could not read the image size"
    w, h = wh
    if w < min_px or h < min_px:
        return f"{w}×{h} is under {min_px}px"
    return None


def _http_error(status: int) -> str:
    if status == 401:
        return "401 Unauthorized — login pages are skipped"
    if status == 403:
        return "403 Forbidden"
    if status == 404:
        return "404 Not found"
    if status == 429:
        return "429 Too many requests"
    return f"HTTP {status}"


def _stopped_fetch(url: str) -> FetchResult:
    return FetchResult(url, url, 0, "", b"", None)


def _http_get_once(url: str, timeout: float, max_bytes: int) -> FetchResult:
    """One GET. A fresh client, so cookies are neither stored nor sent.

    The client is registered so Stop can close it.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.8,*/*;q=0.1",
    }
    client = httpx.Client(follow_redirects=True, timeout=timeout, headers=headers)
    # Bypass the cookie jar. Client() would copy a subclass back into a normal jar.
    client._cookies = _NoStoreCookies()
    _remember_client(client)
    try:
        with client:
            with client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                final = str(resp.url)
                status = resp.status_code
                if status >= 400:
                    return FetchResult(url, final, status, ctype, b"", _http_error(status))
                buf = bytearray()
                for chunk in resp.iter_bytes(65536):
                    if not chunk:
                        continue
                    buf += chunk
                    if len(buf) > max_bytes:
                        return FetchResult(url, final, status, ctype, b"", f"Response is over {max_bytes // 1024}KB")
                return FetchResult(url, final, status, ctype, bytes(buf), None)
    except httpx.TimeoutException:
        if _stop.is_set():
            return _stopped_fetch(url)
        return FetchResult(url, url, 0, "", b"", "Timed out")
    except httpx.HTTPError:
        if _stop.is_set():
            return _stopped_fetch(url)
        return FetchResult(url, url, 0, "", b"", "Could not connect")
    finally:
        _forget_client(client)


def http_get(url: str, timeout: float, max_bytes: int) -> FetchResult:
    """Fetch, but return as soon as Stop is pressed instead of waiting out the timeout."""
    box: dict = {}

    def run():
        try:
            box["result"] = _http_get_once(url, timeout, max_bytes)
        except Exception as e:  # the caller turns this into a normal error result
            box["error"] = e

    fetcher = threading.Thread(target=run, name="gather-get", daemon=True)
    fetcher.start()
    deadline = time.monotonic() + max(float(timeout), 0.1)
    while fetcher.is_alive() and time.monotonic() < deadline:
        if _stop.is_set():
            _close_active_client()
            return _stopped_fetch(url)
        fetcher.join(0.2)
    if fetcher.is_alive():
        _close_active_client()
        return _stopped_fetch(url) if _stop.is_set() else FetchResult(url, url, 0, "", b"", "Timed out")
    if "result" in box:
        return box["result"]
    return FetchResult(url, url, 0, "", b"", "Could not connect")


def _is_html(result: FetchResult) -> bool:
    ctype = result.content_type
    if ctype in ("text/html", "application/xhtml+xml") or ctype.startswith("text/html"):
        return True
    if ctype not in ("", "text/plain", "application/octet-stream"):
        return False
    head = result.body[:300].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head or b"<body" in head


def _decode_html(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    m = re.search(r"charset=([A-Za-z0-9._-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def _non_html_message(result: FetchResult) -> str:
    kind = result.content_type or "unknown type"
    return f"Not an HTML page ({kind})"


def parse_start_urls(text, limit: int = MAX_SEEDS) -> tuple[list[str], list[dict]]:
    if isinstance(text, list):
        text = "\n".join(str(x) for x in text)
    elif not isinstance(text, str):
        text = str(text or "")
    if len(text) > 20000:
        raise BadInput("That list is too long.")
    urls, errors, seen = [], [], set()
    overflow = False
    for raw in re.split(r"[\s,]+", text.strip()):
        if not raw:
            continue
        raw = raw.strip().strip("<>\"'")
        if not raw:
            continue
        shown = raw[:300]
        try:
            parts = urlsplit(raw)
            if parts.username or parts.password:
                errors.append({"url": shown, "error": "Links with a username or password are skipped"})
                continue
        except ValueError:
            errors.append({"url": shown, "error": "Only http and https links work"})
            continue
        url = canonicalize(raw)
        if not url:
            if parts.scheme not in ("http", "https"):
                errors.append({"url": shown, "error": "Only http and https links work"})
            else:
                errors.append({"url": shown, "error": "That link could not be read"})
            continue
        if instagram_host(url):
            errors.append({"url": url, "error": INSTAGRAM_MSG})
            continue
        if url in seen:
            continue
        if len(urls) >= limit:
            overflow = True
            continue
        seen.add(url)
        urls.append(url)
    if overflow:
        errors.append({"url": "", "error": f"Only the first {limit} links are used"})
    return urls, errors


def _declared_small(cand: Candidate, min_px: int) -> bool:
    if cand.srcset_w is not None and cand.srcset_w < min_px:
        return True
    if cand.declared_w and cand.declared_h and (cand.declared_w < min_px or cand.declared_h < min_px):
        return True
    return False


def crawl(seeds: list[str], fetch, *, limits: Limits | None = None, stop=None, on_image=None,
          on_progress=None, sleep=time.sleep) -> dict:
    """Crawl seeds with an injected fetch(url, timeout, max_bytes) -> FetchResult.

    on_image(data, page_url, image_url) -> {"status": "ok"|"skip"|"error", ...}
    on_progress is called with the running summary so a job can be shown while it runs.
    """
    limits = limits or limits_from_env()
    stop = stop or (lambda: False)
    on_image = on_image or (lambda data, page, image: {"status": "ok"})
    errors: list[dict] = []
    ingested: list[dict] = []
    pages = found = skipped = n_ok = attempts = 0
    queue: deque[tuple[str, int]] = deque((u, 0) for u in seeds)
    seen_pages: set[str] = set()
    seen_images: set[str] = set()
    robots: dict[str, tuple[str, RobotFileParser | None]] = {}
    last_hit: dict[str, float] = {}
    noted: set[str] = set()

    def err(url: str, message: str, key: str | None = None):
        if key:
            if key in noted:
                return
            noted.add(key)
        if len(errors) < 40:
            errors.append({"url": url[:500], "error": message[:240]})

    def pause(url: str) -> bool:
        host = _host(url)
        delay = limits.delay_s
        rule = robots.get(origin_root(url))
        if rule and rule[1] is not None:
            try:
                extra = rule[1].crawl_delay(USER_AGENT)
            except Exception:
                extra = None
            if extra:
                delay = min(10.0, max(delay, float(extra)))
        if delay <= 0:
            return not stop()
        remain = delay - (time.monotonic() - last_hit.get(host, 0.0))
        while remain > 0:
            if stop():
                return False
            step = min(0.2, remain)
            sleep(step)
            remain -= step
        return not stop()

    def call(url: str, timeout: float, max_bytes: int) -> FetchResult:
        try:
            got = fetch(url, timeout, max_bytes)
        except Exception as e:
            log.warning("gather fetch failed for %s: %s", url, e)
            return FetchResult(url, url, 0, "", b"", "Could not connect")
        if not isinstance(got, FetchResult):
            return FetchResult(url, url, 0, "", b"", "Could not connect")
        return got

    def snapshot() -> dict:
        return {
            "pages": pages, "images_found": found, "images_ingested": n_ok, "images_skipped": skipped,
            "errors": list(errors), "ingested": list(ingested), "stopped": bool(stop()),
        }

    def progress():
        if not on_progress:
            return
        try:
            on_progress(snapshot())
        except Exception:
            log.warning("gather progress update failed", exc_info=True)

    def allowed(url: str) -> bool:
        root = origin_root(url)
        if root not in robots:
            robots[root] = ("loading", None)
            if not pause(robots_url_for(url)):
                robots[root] = ("stopped", None)
                return False
            got = call(robots_url_for(url), limits.timeout_s, MAX_ROBOTS)
            last_hit[_host(url)] = time.monotonic()
            if stop():
                robots[root] = ("stopped", None)
                return False
            if got.status == 404 or (got.status == 200 and not got.body.strip()):
                robots[root] = ("ok", None)
            elif got.error or got.status >= 400:
                robots[root] = (got.error or _http_error(got.status or 0), None)
            else:
                rp = RobotFileParser()
                try:
                    rp.parse(got.body.decode("utf-8", "replace").splitlines())
                except Exception:
                    robots[root] = ("could not parse robots.txt", None)
                else:
                    robots[root] = ("ok", rp)
        state, rp = robots.get(root, ("ok", None))
        if state == "stopped":
            return False
        if state != "ok":
            err(url, f"Could not read robots.txt ({state})", key=f"robots:{root}")
            return False
        if rp is None:
            return True
        try:
            ok = rp.can_fetch(USER_AGENT, url)
        except Exception:
            ok = False
        if not ok:
            err(url, "Blocked by robots.txt", key=f"robots-block:{root}")
        return ok

    def take(data: bytes | None, ctype: str, page_url: str, image_url: str, cand: Candidate | None):
        nonlocal found, skipped, n_ok, attempts
        if image_url in seen_images or n_ok >= limits.max_images:
            return
        if data is None and attempts >= limits.max_fetches:
            return
        seen_images.add(image_url)
        found += 1
        why = skip_image_url(image_url)
        if why == "instagram":
            skipped += 1
            err(image_url, INSTAGRAM_MSG, key="instagram-image")
            return
        if why:
            skipped += 1
            return
        if cand and _declared_small(cand, limits.min_px):
            skipped += 1
            return
        if data is None:
            if not allowed(image_url):
                skipped += 1
                return
            if not pause(image_url):
                return
            attempts += 1
            got = call(image_url, limits.timeout_s, _image_cap())
            last_hit[_host(image_url)] = time.monotonic()
            if got.error or got.status >= 400:
                err(image_url, got.error or _http_error(got.status))
                return
            final = canonicalize(got.final_url) or image_url
            if instagram_host(final):
                skipped += 1
                err(final, INSTAGRAM_MSG, key="instagram-image")
                return
            if final != image_url and final in seen_images:
                skipped += 1
                return
            seen_images.add(final)
            data, ctype, image_url = got.body, got.content_type, final
        rej = image_rejection(data, ctype, min_px=limits.min_px, min_bytes=limits.min_bytes)
        if rej:
            skipped += 1
            return
        try:
            out = on_image(data, page_url, image_url) or {"status": "ok"}
        except Exception as e:
            log.warning("gather ingest failed for %s: %s", image_url, e)
            err(image_url, f"Ingest failed: {e}"[:240])
            return
        status = out.get("status")
        if status == "ok":
            n_ok += 1
            if len(ingested) < 60:
                ingested.append({"saved_id": out.get("saved_id"), "image_id": out.get("image_id"),
                                 "page": page_url, "image": image_url})
        elif status == "error":
            err(image_url, out.get("error") or "Ingest failed")
        else:
            skipped += 1

    while queue and not stop() and pages < limits.max_pages and n_ok < limits.max_images:
        url, depth = queue.popleft()
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            if not allowed(url):
                pages += 1
                continue
            if not pause(url):
                break
            got = call(url, limits.timeout_s, limits.max_html)
            last_hit[_host(url)] = time.monotonic()
            pages += 1
            if stop():
                break
            if got.error or got.status >= 400:
                err(url, got.error or _http_error(got.status))
                continue
            final = canonicalize(got.final_url) or url
            if not same_origin(final, url):
                err(url, f"Redirected off this site ({_host(final) or 'elsewhere'})")
                continue
            if sniff_raster(got.body) or (got.content_type in RASTER_TYPES):
                take(got.body, got.content_type, final, final, None)
                continue
            if not _is_html(got):
                err(url, _non_html_message(got))
                continue
            try:
                images, links = extract_page(_decode_html(got.body, got.content_type), final)
            except Exception:
                log.warning("gather could not read %s", url, exc_info=True)
                err(url, "Could not read that page")
                continue
            for cand in images:
                if stop() or n_ok >= limits.max_images or attempts >= limits.max_fetches:
                    break
                take(None, "", final, cand.url, cand)
            if depth >= limits.max_depth:
                continue
            for link in links:
                if not same_origin(link, url) or link in seen_pages:
                    continue
                if instagram_host(link):
                    err(link, INSTAGRAM_MSG, key="instagram-page")
                    continue
                queue.append((link, depth + 1))
        finally:
            progress()

    return {
        "pages": pages, "images_found": found, "images_ingested": n_ok, "images_skipped": skipped,
        "errors": errors, "ingested": ingested, "stopped": bool(stop()),
    }


def _image_cap() -> int:
    try:
        mb = int(os.environ.get("MAX_UPLOAD_MB", "40"))
    except ValueError:
        mb = 40
    return max(1, mb) * 1024 * 1024


def ingest_image(data: bytes, page_url: str, image_url: str, mode: str) -> dict:
    """Store one still and queue the existing blur/box worker (which exports to /export)."""
    url = canonicalize(image_url) or (image_url or "")[:MAX_URL]
    if store.gather_find(url=url):
        return {"status": "skip", "error": "already gathered"}
    digest = hashlib.sha256(data).hexdigest()
    prev = store.gather_find(sha=digest)
    if prev:
        store.gather_remember(url, digest, prev.get("saved_id"))
        return {"status": "skip", "error": "already gathered"}
    host = _host(url) or "site"
    if mode not in ("blur", "box"):
        mode = store.load_settings().get("default_mode") or "blur"
        if mode not in ("blur", "box"):
            mode = "blur"
    try:
        sid = saved.add(
            data, "url:" + digest, digest[:12], 1, host[:60], (page_url or "")[:500], None, "image",
            source="url", page_url=(page_url or "")[:1000], image_url=url[:1000], raw_export=False,
        )
    except sqlite3.IntegrityError:
        return {"status": "skip", "error": "already gathered"}
    image_id = saved.process(sid, mode)
    if not isinstance(image_id, int):
        return {"status": "error", "error": "Could not queue processing", "saved_id": sid}
    store.gather_remember(url, digest, sid)
    return {"status": "ok", "saved_id": sid, "image_id": image_id}


def _public(job: dict | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job["id"], "status": job["status"], "mode": job.get("mode") or "",
        "urls": job.get("urls") or [], "pages": job.get("pages") or 0,
        "images_found": job.get("images_found") or 0, "images_ingested": job.get("images_ingested") or 0,
        "images_skipped": job.get("images_skipped") or 0, "errors": job.get("errors") or [],
        "ingested": job.get("ingested") or [], "note": job.get("note") or "",
        "created": job.get("created"), "updated": job.get("updated"), "finished": job.get("finished"),
    }


def view() -> dict:
    return {"job": _public(store.gather_latest()), "caps": caps_public(),
            "default_mode": store.load_settings().get("default_mode") or "blur"}


_stop = threading.Event()
_run_lock = threading.Lock()
_client_lock = threading.Lock()
_active_client = None


def _remember_client(client):
    global _active_client
    with _client_lock:
        _active_client = client


def _forget_client(client):
    global _active_client
    with _client_lock:
        if _active_client is client:
            _active_client = None


def _close_active_client():
    with _client_lock:
        client = _active_client
    if client is None:
        return
    try:
        client.close()
    except Exception:
        log.debug("gather client close", exc_info=True)


def recover():
    """A restart cannot resume a crawl safely. Mark it stopped so the UI is honest."""
    try:
        store.gather_mark_interrupted()
    except Exception:
        log.exception("gather recover failed")


def _flush(job_id: int, summary: dict, *, status: str, note: str, finished: bool):
    store.gather_update(
        job_id, status=status, note=note, pages=summary["pages"], images_found=summary["images_found"],
        images_ingested=summary["images_ingested"], images_skipped=summary["images_skipped"],
        errors=summary["errors"], ingested=summary["ingested"], finished=time.time() if finished else None,
    )


def _run(job_id: int, seeds: list[str], mode: str, prior_errors: list[dict]):
    limits = limits_from_env()

    def on_image(data, page, image):
        if _stop.is_set():
            return {"status": "skip", "error": "stopped"}
        return ingest_image(data, page, image, mode)

    def on_progress(snap):
        merged = dict(snap)
        merged["errors"] = (list(prior_errors) + list(snap.get("errors") or []))[:40]
        with _run_lock:
            status = "cancelling" if _stop.is_set() else "running"
            note = "Stopping…" if status == "cancelling" else "Gathering…"
            _flush(job_id, merged, status=status, note=note, finished=False)

    summary = {"pages": 0, "images_found": 0, "images_ingested": 0, "images_skipped": 0,
               "errors": list(prior_errors), "ingested": []}
    try:
        got = crawl(seeds, http_get, limits=limits, stop=_stop.is_set, on_image=on_image, on_progress=on_progress)
        summary.update(pages=got["pages"], images_found=got["images_found"], images_ingested=got["images_ingested"],
                       images_skipped=got["images_skipped"], ingested=got["ingested"])
        summary["errors"] = (list(prior_errors) + list(got["errors"]))[:40]
        with _run_lock:
            if _stop.is_set() or got.get("stopped"):
                status, note = "cancelled", "Stopped."
            else:
                status, note = "done", "Finished."
            _flush(job_id, summary, status=status, note=note, finished=True)
        log.info("gather #%s %s pages=%s ingested=%s", job_id, status, summary["pages"], summary["images_ingested"])
    except Exception as e:
        log.exception("gather #%s failed", job_id)
        summary["errors"] = (summary["errors"] + [{"url": "", "error": f"Gather failed: {e}"[:240]}])[:40]
        with _run_lock:
            status, note = ("cancelled", "Stopped.") if _stop.is_set() else ("error", "Gather failed.")
            _flush(job_id, summary, status=status, note=note, finished=True)


def start(urls, mode: str = "") -> dict:
    text = urls
    if isinstance(urls, list):
        text = "\n".join(str(u) for u in urls)
    if not str(text or "").strip():
        raise BadInput("Paste at least one URL.")
    chosen = mode if mode in ("blur", "box") else (store.load_settings().get("default_mode") or "blur")
    if chosen not in ("blur", "box"):
        chosen = "blur"
    seeds, problems = parse_start_urls(text)
    with _run_lock:
        current = store.gather_latest()
        if current and current["status"] in ("running", "cancelling"):
            raise Busy()
        if not seeds:
            job_id = store.gather_create(seeds, problems, chosen, status="done", note="Nothing to fetch.")
            return view() | {"job": _public(store.gather_get(job_id))}
        _stop.clear()
        job_id = store.gather_create(seeds, problems, chosen, status="running", note="Starting…")
        threading.Thread(target=_run, args=(job_id, seeds, chosen, problems), name="gather", daemon=True).start()
    log.info("gather #%s started (%d urls, %s)", job_id, len(seeds), chosen)
    return view() | {"job": _public(store.gather_get(job_id))}


def cancel() -> dict:
    with _run_lock:
        _stop.set()
        job = store.gather_latest()
        if job and job["status"] in ("running", "cancelling"):
            store.gather_update(job["id"], status="cancelling", note="Stopping…")
    _close_active_client()
    return view()
