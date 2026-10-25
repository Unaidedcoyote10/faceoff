import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshots"
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#saved"); pg.wait_for_timeout(1500)
    pg.click("#savedMode button[data-v=users]"); pg.wait_for_timeout(1000)
    rows = pg.locator(".urow"); print("accounts:", rows.count(), "| first:", rows.first.inner_text().replace("\n", " ")[:80])
    pg.screenshot(path=f"{OUT}/13-by-user.png")
    pg.click("#userSort button[data-v=recent]"); pg.wait_for_timeout(600)
    q = rows.nth(1).get_attribute("data-o"); pg.fill("#userQ", q[:4]); pg.wait_for_timeout(700)
    print("search", q[:4], "->", pg.locator(".urow").count())
    pg.locator(".urow").first.click(); pg.wait_for_timeout(1200)
    who = pg.inner_text("#ownerChip"); tiles = pg.locator(".st").count()
    owners = set(pg.locator(".st .o b").all_inner_texts())
    print("chip:", who.replace("\n", " "), "| tiles:", tiles, "| owners in grid:", owners)
    pg.screenshot(path=f"{OUT}/14-user-grid.png")
    pg.click("#selBtn"); pg.click("#selAll"); pg.wait_for_timeout(500); print("select all:", pg.inner_text("#selCount"))
    pg.click("#selDone"); pg.click("#ownerChip .x"); pg.wait_for_timeout(800); print("cleared → tiles:", pg.locator(".st").count())
    pg.select_option("#savedSort", "owner"); pg.wait_for_timeout(800)
    print("A-Z order:", pg.locator(".st .o b").all_inner_texts()[:12])
    print("errors:", errs)
    b.close()
