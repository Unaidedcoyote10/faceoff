"""Render docs/og.png (1280x640 GitHub social preview) from the app's glitch mark."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from mark import static_svg
from playwright.sync_api import sync_playwright
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "og.png")
svg = static_svg(300)
html = f"""<html><body style="margin:0;width:1280px;height:640px;background:#0b0b10;
background-image:radial-gradient(90% 80% at 20% 30%,rgba(139,92,246,.22),transparent 60%),radial-gradient(70% 70% at 90% 90%,rgba(236,72,153,.16),transparent 60%);
display:flex;align-items:center;justify-content:center;gap:64px;font-family:Inter,system-ui,-apple-system,sans-serif;color:#f2f2f7;overflow:hidden">
<div style="filter:drop-shadow(0 20px 60px rgba(139,92,246,.35))">{svg}</div>
<div><div style="position:relative;font-size:136px;font-weight:800;letter-spacing:-4px;line-height:1">
<span style="position:absolute;left:-4px;top:0;color:#22e6ff;opacity:.55">Faceoff</span>
<span style="position:absolute;left:4px;top:0;color:#ff2bd6;opacity:.55">Faceoff</span>
<span style="position:relative;background:linear-gradient(135deg,#8b5cf6,#ec4899);-webkit-background-clip:text;background-clip:text;color:transparent">Faceoff</span></div>
<div style="margin-top:22px;font-size:34px;color:#8e8ea0;font-weight:500">Self-hosted face blur &amp; redaction</div></div>
<div style="position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(255,255,255,.03) 0 2px,transparent 2px 6px)"></div>
</body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    pg = b.new_page(viewport={"width": 1280, "height": 640}); pg.set_content(html); pg.wait_for_timeout(300)
    pg.screenshot(path=OUT); b.close()
print("wrote", OUT)
