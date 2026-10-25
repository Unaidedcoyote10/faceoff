import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshots"
errs = []
ok = fail = 0
def check(n, c, x=""):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n + (f"  ({x})" if x != "" else "")); ok += bool(c); fail += (not c)
st = lambda pg: pg.evaluate("""() => [...document.querySelectorAll('#feedScroll .slide')].filter(s => s.querySelector('video')).map(s => {
  const v = s.querySelector('video'); return {i: +s.dataset.i, paused: v.paused, muted: v.muted, t: v.currentTime}; })""")
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=user-gesture-required"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#saved"); pg.wait_for_timeout(1200)
    pg.click("#savedKind button[data-v=video]"); pg.wait_for_timeout(1000)
    nvid = pg.locator(".st").count()
    check("Videos filter shows only videos", nvid > 0 and pg.locator(".st .tl span", has_text="▶").count() == nvid, nvid)
    check("'Watch as feed' button visible", pg.is_visible("#feedBtn"))
    pg.screenshot(path=f"{OUT}/15-videos-grid.png")
    pg.click("#feedBtn"); pg.wait_for_timeout(2500)
    s = st(pg)
    act = [x for x in s if not x["paused"]]
    check("feed open, first video autoplays muted", len(act) == 1 and act[0]["i"] == 0 and act[0]["muted"] and act[0]["t"] > 0, s)
    check("only neighbours mounted (≤3 videos)", len(s) <= 3, len(s))
    pg.screenshot(path=f"{OUT}/16-feed.png")
    pg.click("#feedMute"); pg.wait_for_timeout(300)
    pg.evaluate("document.getElementById('feedScroll').scrollBy(0, innerHeight)"); pg.wait_for_timeout(2000)
    s = st(pg); act = [x for x in s if not x["paused"]]
    check("swipe → next plays, previous paused", len(act) == 1 and act[0]["i"] == 1, s)
    check("unmute persists while scrolling", act and not act[0]["muted"])
    check("counter (covers all videos, not just loaded grid page)", pg.inner_text("#feed .fcount").startswith("2 / "), pg.inner_text("#feed .fcount"))
    pg.locator(".slide[data-i='1']").click(position={"x": 195, "y": 300}); pg.wait_for_timeout(400)
    check("tap pauses", st(pg) and all(x["paused"] for x in st(pg)))
    pg.locator(".slide[data-i='1']").click(position={"x": 195, "y": 300}); pg.wait_for_timeout(600)
    check("tap plays again", any(not x["paused"] for x in st(pg)))
    href = pg.get_attribute(".slide[data-i='1'] .iglink", "href")
    check("Open on Instagram link", href and href.startswith("https://www.instagram.com/p/"), href)
    pg.go_back(); pg.wait_for_timeout(600)
    check("back gesture closes feed", not pg.is_visible("#feed"))
    pg.locator(".st").nth(min(2, nvid - 1)).click(); pg.wait_for_timeout(2200)
    s = st(pg); act = [x for x in s if not x["paused"]]
    check("tapping a grid video opens feed at that video", act and act[0]["i"] == min(2, nvid - 1), s)
    owner = pg.inner_text(f".slide[data-i='{min(2, nvid - 1)}'] .who")
    pg.click(f".slide[data-i='{min(2, nvid - 1)}'] .who"); pg.wait_for_timeout(2500)
    who = set(pg.locator("#feedScroll .who").all_inner_texts())
    check("tap @user → feed filtered to that account", pg.is_visible("#feed") and who == {owner}, (owner, who))
    pg.click("#feedClose"); pg.wait_for_timeout(500)
    check("close button", not pg.is_visible("#feed") and owner[1:] in pg.inner_text("#ownerChip"))
    check("no page errors", not errs, errs)
    b.close()
print(f"\n{ok} passed, {fail} failed")
