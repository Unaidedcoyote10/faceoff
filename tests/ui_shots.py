import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = "screenshots"
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True,
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#upload"); pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{OUT}/1-upload.png")
    pg.tap("nav.bottom button[data-tab=gallery]"); pg.wait_for_timeout(1800)
    tiles = pg.locator(".tile")
    n = tiles.count()
    # toggle two tiles to BEFORE by tapping
    tiles.nth(1).click(position={"x": 60, "y": 120}); tiles.nth(2).click(position={"x": 60, "y": 120}); pg.wait_for_timeout(500)
    assert pg.locator(".tile.showb").count() == 2, "tap toggle failed"
    assert pg.locator(".tile.showb .badge").first.inner_text() == "BEFORE"
    pg.screenshot(path=f"{OUT}/2-gallery-toggle.png")
    # open viewer on the group photo
    t = pg.locator(".tile", has=pg.locator(".chip", has_text="335 faces")).first
    if not t.count():
        t = tiles.first
    t.locator(".open").click(); pg.wait_for_timeout(1200)
    pg.locator("#cmpRange").fill("45"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/3-viewer-slider.png")
    pg.click("#vClose")
    pg.tap("nav.bottom button[data-tab=settings]"); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/4-settings.png")
    pg.locator("#extCard").scroll_into_view_if_needed(); pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/5-settings-extension.png")
    title = pg.inner_text("#extCard")
    assert pg.locator("#cookieIn, #igCard, #cookieSave").count() == 0
    assert "extension-only" in title
    print("tiles:", n, "| extension card:", title.split("\n")[0])
    b.close()
print("console errors:", errs)
