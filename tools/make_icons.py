import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from mark import static_svg
from playwright.sync_api import sync_playwright
D = os.path.join(os.path.dirname(__file__), "..", "app", "static", "icons")
open(f"{D}/logo.svg", "w").write(static_svg(64))
jobs = [("icon-192.png", 192, 0), ("icon-512.png", 512, 0), ("apple-touch-icon.png", 180, 0), ("icon-maskable-512.png", 512, 0.12), ("favicon-32.png", 32, 0)]
EXT = os.path.join(os.path.dirname(__file__), "..", "extension", "icons")
jobs += [(os.path.join(EXT, f"icon{n}.png"), n, 0) for n in (16, 32, 48, 128)]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    for name, sz, pad in jobs:
        pg = b.new_page(viewport={"width": sz, "height": sz})
        # full-bleed tiles (no rounded corners) for platform-masked icons
        svg = static_svg(sz, pad=pad) if name.startswith(("icon-mask", "apple")) is False else static_svg(sz, pad=pad or 0.0001)
        pg.set_content(f'<html><body style="margin:0;background:#0b0b10">{svg}</body></html>')
        pg.screenshot(path=name if os.path.isabs(name) or name.startswith(EXT) else f"{D}/{name}", omit_background=False); pg.close()
    b.close()
print("ok")
