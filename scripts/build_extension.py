#!/usr/bin/env python3
"""Zip extension/ into dist/faceoff-sync.zip (top folder 'faceoff-sync/'), ready for 'Load unpacked' after extracting.
The running server also serves the extension at /api/ext/download."""
import os, zipfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC, OUT = os.path.join(ROOT, "extension"), os.path.join(ROOT, "dist", "faceoff-sync.zip")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for r, _, fs in os.walk(SRC):
        for f in sorted(fs):
            p = os.path.join(r, f)
            z.write(p, os.path.join("faceoff-sync", os.path.relpath(p, SRC)))
print("wrote", OUT, os.path.getsize(OUT), "bytes")
