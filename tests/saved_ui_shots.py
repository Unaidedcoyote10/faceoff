import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = "screenshots"
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#saved"); pg.wait_for_timeout(1500)
    n = pg.locator(".st").count()
    print("saved tiles:", n)
    pg.screenshot(path=f"{OUT}/8-saved.png")
    pg.click("#selBtn"); pg.locator(".st").nth(6).click(); pg.locator(".st").nth(7).click(); pg.wait_for_timeout(300)
    print("selected:", pg.inner_text("#selCount"))
    pg.screenshot(path=f"{OUT}/9-saved-select.png")
    pg.click("#selAll"); pg.wait_for_timeout(500); print("select all:", pg.inner_text("#selCount"))
    pg.click("#selDone")
    pg.click("#savedFilters button[data-f=videos]"); pg.wait_for_timeout(800); print("videos:", pg.locator(".st").count())
    pg.click("#savedFilters button[data-f=all]"); pg.wait_for_timeout(800)
    # open an unprocessed one (raw)
    raw = pg.locator(".st:not(:has(.p))").first; raw.click(); pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{OUT}/10-saved-raw.png"); pg.click("#vClose")
    # open processed video
    vid = pg.locator(".st:has(.p)").filter(has_text="▶").first; vid.click(); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/11-saved-video.png")
    pg.click("#vSide button[data-v=before]"); pg.wait_for_timeout(800)
    print("video src:", pg.eval_on_selector("#vVideo", "e => e.src.split('/').slice(-1)[0]"))
    pg.click("#vClose")
    pg.click("nav.bottom button[data-tab=settings]"); pg.wait_for_timeout(1200)
    pg.locator("#extCard").screenshot(path=f"{OUT}/12-extension-card.png")
    pg.locator("#igExportStatus").scroll_into_view_if_needed()
    print("ig export text:", pg.inner_text("#igExportStatus"))
    print("extension title:", pg.inner_text("#extCard .card-h"))
    assert pg.locator("#igCard, #cookieIn").count() == 0
    assert "extension-only" in pg.inner_text("#extCard")
    print("errors:", errs)
    b.close()
