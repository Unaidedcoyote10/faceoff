"""Logo checks at phone width: tagline gone, animated mark in header + Upload hero, reduced-motion = static."""
import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshots"
ok = fail = 0
def check(n, c, x=""):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n + (f"  ({x})" if x != "" else ""), flush=True); ok += bool(c); fail += (not c)
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#upload"); pg.wait_for_timeout(1000)
    html = pg.content().lower()
    check("tagline gone", "keep the moment" not in html and "hide the face" not in html)
    check("header mark rendered", pg.locator("header .fo-mark").bounding_box()["width"] >= 28)
    check("hero mark rendered", pg.locator(".hero .fo-mark").bounding_box()["width"] >= 100)
    anim = pg.evaluate("getComputedStyle(document.querySelector('.hero .fo-mark .band')).animationName")
    check("glitch animation running", anim == "fo-band", anim)
    # sample band offset over a full cycle: mostly still, short bursts
    xs = pg.evaluate("""async () => { const el = document.querySelector('header .fo-mark .band'); const out = [];
      for (let i = 0; i < 70; i++) { out.push(new DOMMatrix(getComputedStyle(el).transform).e); await new Promise(r => setTimeout(r, 100)); } return out; }""")
    moving = sum(1 for x in xs if abs(x - 5) > .01)
    check("bursts are short (<20% of the cycle)", 0 < moving <= 14, moving)
    pg.screenshot(path=f"{OUT}/30-logo-upload.png")
    pg.locator(".hero").screenshot(path=f"{OUT}/31-logo-hero.png")
    for t in ("gallery", "saved", "settings"):
        pg.click(f"nav.bottom button[data-tab={t}]"); pg.wait_for_timeout(400)
        check(f"header logo visible on {t}", pg.is_visible("header .fo-mark"))
    for u in ("/icons/logo.svg?v=2", "/icons/icon-192.png?v=2", "/icons/icon-maskable-512.png?v=2", "/icons/apple-touch-icon.png?v=2", "/favicon.ico", "/manifest.webmanifest"):
        r = pg.request.get(BASE + u); check(f"{u} 200", r.ok, r.status)
    man = pg.request.get(BASE + "/manifest.webmanifest").json()
    check("manifest has no tagline", "moment" not in str(man).lower(), man["description"])
    ctx.close()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    pg = ctx.new_page(); pg.goto(BASE + "/#upload"); pg.wait_for_timeout(800)
    anim = pg.evaluate("getComputedStyle(document.querySelector('.hero .fo-mark .band')).animationName")
    check("prefers-reduced-motion → static", anim == "none", anim)
    ctx.close(); b.close()
check("no page errors", not errs, errs)
print(f"\n{ok} passed, {fail} failed")
