"""Faceoff Sync v2 safety E2E (Chromium + local HTTPS Instagram mock + local Faceoff on :8088)."""
import json, ssl, sys, time, urllib.request, os
from playwright.sync_api import sync_playwright
EXT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "extension"))
FO, MOCK = "http://127.0.0.1:8088", "https://127.0.0.1:8443"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
ok = fail = 0
def check(n, c, x=""):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n + (f"  ({x})" if x != "" else ""), flush=True); ok += bool(c); fail += (not c)
mock = lambda p: json.loads(urllib.request.urlopen(MOCK + p, context=CTX).read())
def fo(p, m="GET", body=None):
    r = urllib.request.Request(FO + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())
calls = lambda: mock("/__ctl/log")["api_calls"]
def wait(fn, t=30):
    e = time.time() + t
    while time.time() < e:
        v = fn()
        if v: return v
        time.sleep(0.5)
for k in ["image", "image", "carousel", "image", "video"]: mock(f"/__ctl/add?kind={k}")
token = fo("/api/ext/token", "POST")["token"]
fo("/api/ext/pause", "POST", {"paused": True, "backfill": False})
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context("/tmp/fo-ext-v2", channel="chromium", headless=True, ignore_https_errors=True,
        args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--ignore-certificate-errors",
              "--host-resolver-rules=MAP www.instagram.com 127.0.0.1:8443, MAP scontent-test.cdninstagram.com 127.0.0.1:8443"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    eid = sw.url.split("/")[2]
    st = lambda k: sw.evaluate(f"chrome.storage.local.get('{k}').then(r => r['{k}'])")
    check("v2 loaded", sw.evaluate("chrome.runtime.getManifest().version") == "2.0.0")
    # configure like the options page would (headless Chromium can't click the host-permission prompt)
    sw.evaluate(f"chrome.storage.local.get('cfg').then(r => chrome.storage.local.set({{cfg: {{...(r.cfg || {{}}), url: 'http://127.0.0.1:8088', token: '{token}', auto: true, firstSend: 0}}}}))")
    ctl = sw.evaluate("fo('/api/ext/control')")
    check("paired with server; sync starts PAUSED", ctl.get("paused") is True, ctl.get("reason"))
    ig = ctx.new_page(); ig.goto("https://www.instagram.com/"); ig.bring_to_front()
    time.sleep(14)
    check("server paused → ZERO Instagram requests on visit", calls() == 0, calls())
    fo("/api/ext/pause", "POST", {"paused": False})
    ig.reload(); ig.bring_to_front()
    check("resumed + tab visible/focused → baseline (1 request)", wait(lambda: st("baselined"), 30) and calls() == 1, calls())
    check("first run sent nothing", fo("/api/saved")["total"] == 0)
    na = st("nextAuto")
    check("next check 3–6 h out", na and 2.9 * 3600 <= na / 1000 - time.time() <= 6.1 * 3600, round((na / 1000 - time.time()) / 3600, 2))
    # due alarm while tab focused but not yet due → nothing; force due
    mock("/__ctl/add?kind=image")
    sw.evaluate("chrome.storage.local.set({nextAuto: Date.now() - 1000}).then(() => chrome.alarms.create('faceoff-auto', {when: Date.now() + 500}))")
    check("due check → 1 request, new save sent", wait(lambda: fo("/api/saved")["total"] == 1, 40) and calls() == 2, (calls(), fo("/api/saved")["total"]))
    # backfill: off in server settings → no request even when asked
    sw.evaluate("chrome.storage.local.set({bf: {active: true, pages: 0, posts: 0, sent: 0}}).then(() => chrome.alarms.create('faceoff-bf', {when: Date.now() + 500}))")
    time.sleep(6)
    check("backfill not allowed by server → no request", calls() == 2, calls())
    fo("/api/ext/pause", "POST", {"backfill": True})
    sw.evaluate("chrome.storage.local.get('bf').then(r => chrome.storage.local.set({bf: {...r.bf, pausedUntil: 0}})).then(() => chrome.alarms.create('faceoff-bf', {when: Date.now() + 500}))")
    wait(lambda: calls() == 3, 30)
    b = wait(lambda: (lambda v: v if v and (v.get("pausedUntil") or v.get("done")) else None)(st("bf")), 15) or {}
    gap = (b.get("pausedUntil", 0) / 1000) - time.time()
    check("backfill allowed → exactly 1 page, then next ≥45 s later (or finished)", calls() == 3 and (b.get("done") or 40 <= gap <= 121), (calls(), b.get("reason"), round(gap)))
    time.sleep(5); check("no burst after that page", calls() == 3, calls())
    # 429 → stop locally 48 h + server auto-paused
    ig.bring_to_front(); mock("/__ctl/mode?m=429")
    sw.evaluate("chrome.storage.local.set({nextAuto: Date.now() - 1000, bf: {active: false}}).then(() => chrome.alarms.create('faceoff-auto', {when: Date.now() + 500}))")
    s = wait(lambda: (lambda v: v if v and v.get("stopUntil", 0) > time.time() * 1000 else None)(st("status")), 30)
    check("429 → stopped ≥47 h", s and (s["stopUntil"] / 1000 - time.time()) / 3600 > 47, s and s.get("lastError"))
    ctl = fo("/api/settings")["extension"]
    check("429 → Faceoff auto-paused server-side", ctl["paused"] and "rate_limit" in ctl["reason"], ctl["reason"])
    n = calls(); mock("/__ctl/mode?m=ok")
    sw.evaluate("chrome.storage.local.set({nextAuto: Date.now() - 1000}).then(() => chrome.alarms.create('faceoff-auto', {when: Date.now() + 500}))")
    ig.reload(); time.sleep(15)
    check("after warning: no further Instagram requests", calls() == n, calls() - n)
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{eid}/popup.html"); pop.wait_for_timeout(1000)
    check("popup shows stopped", "stopped" in pop.inner_text("#state"), pop.inner_text("#state"))
    ctx.close()
print(f"\n{ok} passed, {fail} failed")
