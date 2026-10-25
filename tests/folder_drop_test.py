"""Headless test: drop real nested folders onto the drop zone (CDP drag with directory paths),
and pick a folder via the webkitdirectory input. Expects 151 images, 6 junk files skipped."""
import sys, time, json, urllib.request
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
ROOT = "/tmp/fold"
def api(p): return json.load(urllib.request.urlopen(BASE + p))
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(BASE + "/#upload"); pg.wait_for_timeout(800)
    assert pg.is_visible("#folderBtn"), "folder button should show on desktop"
    before = max([i["id"] for i in api("/api/images")["items"]] or [0])
    # 1) real OS-style folder drag via CDP
    cdp = ctx.new_cdp_session(pg)
    box = pg.locator("#drop").bounding_box(); x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    data = {"items": [], "files": [f"{ROOT}/Trip 2026", f"{ROOT}/Party", f"{ROOT}/__MACOSX"], "dragOperationsMask": 1}
    for t in ("dragEnter", "dragOver", "drop"):
        cdp.send("Input.dispatchDragEvent", {"type": t, "x": x, "y": y, "data": data})
    pg.wait_for_selector("#upStatus:not(.hidden)", timeout=15000)
    pg.wait_for_function("document.querySelector('.upbox .uptxt') && /queued for redaction/.test(document.querySelector('.upbox .uptxt').textContent)", timeout=120000)
    status = pg.inner_text("#upStatus"); summary = pg.inner_text(".upbox .uptxt")
    print("drop status :", status); print("drop result :", summary)
    pg.screenshot(path="screenshots/6-folder-drop.png")
    # 2) folder picker input
    pg.set_input_files("#folderInput", f"{ROOT}/Party")
    pg.wait_for_function("document.querySelectorAll('.upbox').length >= 2 && /queued for redaction/.test(document.querySelectorAll('.upbox .uptxt')[0].textContent)", timeout=120000)
    print("picker status:", pg.inner_text("#upStatus"))
    # iPhone: folder button hidden
    m = b.new_context(viewport={"width": 390, "height": 844}, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", is_mobile=True, has_touch=True)
    mp = m.new_page(); mp.goto(BASE + "/#upload"); mp.wait_for_timeout(800)
    print("iOS folder button visible:", mp.is_visible("#folderBtn"), "| files button visible:", mp.is_visible("#filesBtn"))
    mp.screenshot(path="screenshots/7-upload-iphone.png")
    b.close()
# wait for processing
t = time.time()
while True:
    c = api("/api/images")["counts"]
    if not c.get("queued") and not c.get("processing"): break
    time.sleep(2)
items = [i for i in api("/api/images")["items"] if i["id"] > before]
print(f"processing done in {time.time()-t:.0f}s; gallery size {len(api('/api/images')['items'])}; counts {c}")
heic = [i for i in items if i["name"].lower().endswith(".heic")]
print("heic:", [(i["name"], i["status"], i["faces"], i["error"]) for i in heic])
print("sample names:", [i["name"] for i in items[:3]], [i["exported"] for i in items[:3]])
print("errors in items:", [(i["name"], i["error"]) for i in items if i["status"] == "error"][:5])
print("console errors:", errs)
