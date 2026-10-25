"""Save-to-device UI checks at phone width. Run against a server with saved photos+videos and ≥1 processed gallery item.
   python3 tests/save_shots.py http://localhost:8088 screenshots"""
import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshots"
ok = fail = 0
errs = []
def check(n, c, x=""):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n + (f"  ({x})" if x != "" else ""), flush=True); ok += bool(c); fail += (not c)
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
SHARE_STUB = """window.__shared = []; navigator.canShare = d => !!(d && d.files);
navigator.share = async d => { if (!navigator.userActivation || !navigator.userActivation.isActive) throw new DOMException('no gesture', 'NotAllowedError');
  window.__shared.push(...d.files.map(f => ({name: f.name, type: f.type, size: f.size}))); };"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    # ---------- Android / generic: attachment download ----------
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, user_agent=ANDROID, accept_downloads=True)
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#saved"); pg.wait_for_timeout(1200)
    pg.click("#savedKind button[data-v=video]"); pg.wait_for_timeout(1000)
    check("save button on every saved tile", pg.locator(".st .dlb").count() == pg.locator(".st").count() > 0)
    pg.click("#feedBtn"); pg.wait_for_timeout(2000)
    check("feed: Save button visible", pg.is_visible("#feedSave"))
    pg.screenshot(path=f"{OUT}/20-feed-save.png")
    who = pg.inner_text(".slide[data-i='0'] .who")
    with pg.expect_download(timeout=60000) as d: pg.click("#feedSave")
    dl = d.value
    check("feed Save → real download named @user_shortcode.mp4", dl.suggested_filename.startswith(who) and dl.suggested_filename.endswith(".mp4"), dl.suggested_filename)
    import os; path = dl.path(); check("downloaded video non-empty", os.path.getsize(path) > 10000, os.path.getsize(path))
    playing = pg.evaluate("[...document.querySelectorAll('#feedScroll video')].some(v => !v.paused)")
    check("feed keeps playing / stays open after Save", pg.is_visible("#feed"), playing)
    pg.click("#feedClose"); pg.wait_for_timeout(400)
    pg.click("#savedKind button[data-v=image]"); pg.wait_for_timeout(1000)
    with pg.expect_download(timeout=30000) as d: pg.locator(".st .dlb").first.click()
    fn = d.value.suggested_filename
    check("photo tile ⬇ → download @user_shortcode.ext", fn.startswith("@") and fn.rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp", "heic"), fn)
    check("tile ⬇ doesn't open the viewer", not pg.is_visible("#viewer"))
    pg.screenshot(path=f"{OUT}/21-saved-grid-save.png")
    pg.locator(".st").first.click(); pg.wait_for_timeout(800)
    check("viewer shows ⬇ Save", pg.is_visible("#vDownload") and "Save" in pg.inner_text("#vDownload"))
    with pg.expect_download(timeout=30000) as d: pg.click("#vDownload")
    check("viewer Save downloads", d.value.suggested_filename.startswith("@"), d.value.suggested_filename)
    pg.click("#vClose"); pg.wait_for_timeout(300)
    pg.click("#selBtn"); pg.wait_for_timeout(300)
    check("select mode hides tile ⬇", not pg.locator(".st .dlb").first.is_visible())
    pg.locator(".st").nth(0).click(); pg.locator(".st").nth(1).click()
    check("select bar has ⬇ Save", pg.is_visible("#selSave") and not pg.is_disabled("#selSave"))
    pg.screenshot(path=f"{OUT}/22-select-save.png")
    got = []
    pg.on("download", lambda d: got.append(d.suggested_filename))
    pg.click("#selSave"); pg.wait_for_timeout(4000)
    check("multi-save downloads each file", len(got) == 2, got)
    pg.click("#selDone")
    # gallery
    pg.click("nav.bottom button[data-tab=gallery]"); pg.wait_for_timeout(1500)
    n = pg.locator(".tile .dlb").count()
    if n:
        with pg.expect_download(timeout=30000) as d: pg.locator(".tile .dlb").first.click()
        check("gallery tile ⬇ downloads redacted copy (_blur/_box)", any(m in d.value.suggested_filename for m in ("_blur", "_box")), d.value.suggested_filename)
        pg.screenshot(path=f"{OUT}/23-gallery-save.png")
        pg.locator(".tile .open").first.click(); pg.wait_for_timeout(800)
        with pg.expect_download(timeout=30000) as d: pg.click("#vDownload")
        check("lightbox ⬇ Save downloads redacted copy", any(m in d.value.suggested_filename for m in ("_blur", "_box")), d.value.suggested_filename)
    else:
        check("gallery has processed items to test", False)
    ctx.close()
    # ---------- iPhone (HTTPS): share sheet → Save Video ----------
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True, user_agent=IPHONE, accept_downloads=True)
    ctx.add_init_script(SHARE_STUB)
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#saved"); pg.wait_for_timeout(1200)
    secure = pg.evaluate("isSecureContext")
    pg.click("#savedKind button[data-v=video]"); pg.wait_for_timeout(900)
    pg.click("#feedBtn"); pg.wait_for_timeout(1500)
    pg.click("#feedSave"); pg.wait_for_timeout(300)
    check("iOS: save sheet opens", pg.is_visible("#saveSheet"), secure)
    pg.wait_for_function("!document.getElementById('ssShare').disabled", timeout=60000)
    pg.screenshot(path=f"{OUT}/24-ios-sheet.png")
    check("iOS: sheet ready w/ 'Save to Photos / Files' + Files fallback link", "Ready" in pg.inner_text("#ssStatus") and pg.get_attribute("#ssAlt", "href").endswith("/file/download"))
    pg.click("#ssShare"); pg.wait_for_timeout(500)
    sh = pg.evaluate("window.__shared")
    check("iOS: share gets the video file (type video/mp4, real size)", len(sh) == 1 and sh[0]["type"] == "video/mp4" and sh[0]["size"] > 10000 and sh[0]["name"].endswith(".mp4"), sh)
    check("iOS: sheet closes after save", not pg.is_visible("#saveSheet"))
    pg.click("#feedClose"); pg.wait_for_timeout(300)
    pg.click("#savedKind button[data-v=image]"); pg.wait_for_timeout(900)
    pg.locator(".st .dlb").first.click()
    pg.wait_for_function("!document.getElementById('ssShare').disabled", timeout=30000)
    pg.click("#ssShare"); pg.wait_for_timeout(400)
    sh = pg.evaluate("window.__shared")
    check("iOS: photo shared as image file", len(sh) == 2 and sh[1]["type"].startswith("image/"), sh[-1:])
    ctx.close()
    check("no page errors", not errs, errs)
    b.close()
print(f"\n{ok} passed, {fail} failed")
