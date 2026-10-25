"""Keyword redaction: text PDFs, whole-word matching, OCR when Tesseract is installed, export.

Face blur/box, the Saved library, and the export folder stay on their existing paths.
Originals are not modified. No network and no real accounts.

Run: python -m tests.docs_redact_test

Manual check (also in the README): open Docs, confirm Upload is still Blur/Box only,
redact a text PDF, confirm the export name looks like *_doc_*_redact.pdf, the original
file is unchanged, and the document does not show up in the Gallery.
"""
import io
import os
import re
import sys
import tempfile
import time

ROOT = os.environ.get("FACEOFF_TEST_ROOT") or tempfile.mkdtemp(prefix="faceoff-docs-")
os.environ.setdefault("DATA_DIR", os.path.join(ROOT, "data"))
os.environ.setdefault("EXPORT_DIR", os.path.join(ROOT, "export"))
os.environ.setdefault("IG_EXPORT_DIR", os.path.join(ROOT, "ig"))
for d in (os.environ["DATA_DIR"], os.environ["EXPORT_DIR"], os.environ["IG_EXPORT_DIR"]):
    os.makedirs(d, exist_ok=True)

from PIL import Image  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

from app import keywords  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))
    ok += bool(cond)
    fail += (not cond)


def build_pdf(pages, rotate=0):
    """Minimal text PDF. pages is a list of strings drawn in Helvetica at 72,700."""
    objects = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_obj_nums = []
    rot = f" /Rotate {int(rotate)}" if rotate else ""
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 20 Tf 72 700 Td ({safe}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_num = len(objects)
        objects.append(
            f"<< /Type /Page /Parent PAGES 0 R /MediaBox [0 0 612 792]{rot} /Contents {content_num} 0 R "
            f"/Resources << /Font << /F1 1 0 R >> >> >>".encode()
        )
        page_obj_nums.append(len(objects))
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_nums)} >>".encode())
    pages_num = len(objects)
    objects = [o.replace(b"PAGES", str(pages_num).encode()) for o in objects]
    objects.append(f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    xref = [0]
    for i, obj in enumerate(objects, 1):
        xref.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(xref)}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in xref[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(xref)} /Root {len(objects)} 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def ocr_pdf(data: bytes) -> str:
    import pytesseract
    doc = pdfium.PdfDocument(data)
    try:
        parts = []
        for i in range(len(doc)):
            page = doc[i]
            try:
                bm = page.render(scale=144 / 72, rev_byteorder=True)
                im = bm.to_pil().convert("RGB")
                bm.close()
                parts.append(pytesseract.image_to_string(im))
            finally:
                page.close()
        return "\n".join(parts)
    finally:
        doc.close()


PAGE_A = "The secret code is OMEGA-9 today. Account number 44821 is here."
PAGE_B = "A category catalog and the cat sat."
PHRASES = ["OMEGA-9", "secret code", "44821", "cat"]


# --- pure helpers ---
check("parse one per line, de-dupe", keywords.parse_keywords(" Omega-9 \nomega-9\n\nsecret code\n") == ["Omega-9", "secret code"])
try:
    keywords.parse_keywords("   \n")
    check("empty keywords rejected", False)
except keywords.DocError:
    check("empty keywords rejected", True)
try:
    keywords.parse_keywords("\n".join(f"word{i}" for i in range(41)))
    check("41 keywords rejected", False)
except keywords.DocError:
    check("41 keywords rejected", True)
spans = keywords.find_spans(["a", "category", "catalog", "and", "the", "cat", "sat"], [["cat"], ["category"]])
check("cat does not match category", (5, 6, 0) in spans and (1, 2, 1) in spans and all(not (a == 1 and p == 0) for a, _, p in spans), spans)
same = keywords.cluster_line_rects([(10, 100, 40, 112), (48, 100, 90, 112)])
split = keywords.cluster_line_rects([(10, 100, 40, 112), (10, 40, 40, 52)])
check("same-line boxes union", len(same) == 1 and same[0][2] > 90, same)
check("two lines stay two boxes", len(split) == 2, split)

pdf = build_pdf([PAGE_A, PAGE_B])
result = keywords.redact_file(pdf, "report.pdf", PHRASES)
check("pdf hits are the four whole words", result.hits == 4 and result.by_phrase["cat"] == 1 and result.by_phrase["OMEGA-9"] == 1, result.by_phrase)
check("text layer removed", pdfium.PdfDocument(result.data)[0].get_textpage().count_chars() == 0)
check("redacted tokens not in file bytes", b"OMEGA-9" not in result.data and b"44821" not in result.data)
check("original bytes unchanged by the call", pdf.startswith(b"%PDF") and b"OMEGA-9" in pdf)
if keywords.ocr_available():
    text = ocr_pdf(result.data).casefold()
    print("OCR:", " ".join(text.split()))
    check("covered words are not readable", "omega" not in text and "44821" not in text and "secret" not in text, text)
    check("neighbors and category stay", "today" in text and "category" in text and "catalog" in text, text)
    check("standalone cat is covered", re.search(r"\bcat\b", text) is None, text)
else:
    check("OCR available for visibility check", False, "tesseract missing")

rot = keywords.redact_file(build_pdf(["Hello OMEGA-9 there."], rotate=90), "rot.pdf", ["OMEGA-9"])
check("rotated page still matches", rot.hits == 1 and rot.by_phrase["OMEGA-9"] == 1, rot.by_phrase)
if keywords.ocr_available():
    rtext = ocr_pdf(rot.data).casefold()
    print("OCR rot:", " ".join(rtext.split()))
    check("rotated cover hides the word", "omega" not in rtext and "hello" in rtext, rtext)

# scanned page: image-only PDF, no text layer
src_doc = pdfium.PdfDocument(build_pdf([PAGE_A]))
src_page = src_doc[0]
bm = src_page.render(scale=144 / 72, rev_byteorder=True)
scan_im = bm.to_pil().convert("RGB")
bm.close()
src_page.close()
src_doc.close()
scan_buf = io.BytesIO()
scan_im.save(scan_buf, "PDF", resolution=144)
scanned = scan_buf.getvalue()
check("scan has no text layer", pdfium.PdfDocument(scanned)[0].get_textpage().count_chars() == 0)
if keywords.ocr_available():
    scan_res = keywords.redact_file(scanned, "scan.pdf", ["44821"])
    check("scan uses OCR and finds the number", scan_res.ocr_pages >= 1 and scan_res.hits >= 1 and scan_res.by_phrase["44821"] >= 1, scan_res.by_phrase)
    stext = ocr_pdf(scan_res.data).casefold()
    print("OCR scan:", " ".join(stext.split()))
    check("scanned number is covered", "44821" not in stext, stext)
    png_buf = io.BytesIO()
    scan_im.save(png_buf, "PNG")
    img_res = keywords.redact_file(png_buf.getvalue(), "page.png", ["OMEGA-9"])
    check("image OCR returns png and a hit", img_res.ext == ".png" and img_res.hits >= 1 and img_res.ocr_pages == 1, img_res.by_phrase)
    im = Image.open(io.BytesIO(img_res.data))
    import pytesseract
    itext = pytesseract.image_to_string(im).casefold()
    print("OCR image:", " ".join(itext.split()))
    check("image cover hides the word", "omega" not in itext and "today" in itext, itext)
else:
    try:
        keywords.redact_file(scanned, "scan.pdf", ["44821"])
        check("scan without OCR is an error", False)
    except keywords.DocError as e:
        check("scan without OCR is an error", "OCR" in str(e) or "selectable" in str(e), str(e))

prev = os.environ.get("DOC_MAX_PAGES")
os.environ["DOC_MAX_PAGES"] = "1"
try:
    keywords.redact_file(pdf, "big.pdf", ["cat"])
    check("page cap enforced", False)
except keywords.DocError as e:
    check("page cap enforced", "limit" in str(e).lower(), str(e))
finally:
    if prev is None:
        os.environ.pop("DOC_MAX_PAGES", None)
    else:
        os.environ["DOC_MAX_PAGES"] = prev

try:
    keywords.redact_file(b"not a pdf", "notes.pdf", ["cat"])
    check("non-pdf rejected", False)
except keywords.DocError:
    check("non-pdf rejected", True)
try:
    keywords.redact_file(b"", "x.png", ["cat"])
    check("empty file rejected", False)
except keywords.DocError:
    check("empty file rejected", True)

# --- API: export folder, originals, gallery isolation ---
from fastapi.testclient import TestClient  # noqa: E402
from app import main, store  # noqa: E402

with TestClient(main.app) as c:
    check("health", c.get("/api/health").json()["ok"])
    check("saved library still empty", c.get("/api/saved").json()["total"] == 0)
    bad = c.post("/api/docs", data={"keywords": "   "}, files={"file": ("a.pdf", pdf, "application/pdf")})
    check("API rejects empty keywords", bad.status_code == 400, bad.text)
    bad = c.post("/api/docs", data={"keywords": "cat"}, files={"file": ("notes.txt", b"hello cat", "text/plain")})
    check("API rejects text files", bad.status_code == 400, bad.text)
    bad = c.post("/api/docs", data={"keywords": "cat"}, files={"file": ("fake.pdf", b"hello", "application/pdf")})
    check("API rejects fake pdf", bad.status_code == 400, bad.text)

    up = c.post("/api/docs", data={"keywords": "\n".join(PHRASES)}, files={"file": ("Quarterly Report.pdf", pdf, "application/pdf")})
    check("API queues a pdf", up.status_code == 200 and up.json()["status"] == "queued", up.text)
    doc_id = up.json()["id"]
    t0 = time.time()
    body = None
    while time.time() - t0 < 90:
        body = c.get(f"/api/docs/{doc_id}").json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    check("API job finished", body and body["status"] == "done" and body["hits"] == 4, body)
    check("API note does not claim face blur", body and "face" not in (body.get("note") or "").lower() or "not" in (body.get("note") or "").lower(), body.get("note") if body else None)
    raw_path = store.doc_paths(doc_id, ".pdf")["original"]
    check("original on disk is untouched", open(raw_path, "rb").read() == pdf)
    exp = body.get("exported") if body else None
    check("export name pattern hides the keywords", bool(exp) and re.match(r"\d{8}-\d{6}_doc_Quarterly-Report_redact\.pdf$", exp or "") and "OMEGA" not in (exp or "") and "44821" not in (exp or ""), exp)
    exported = []
    for dp, _, fs in os.walk(os.environ["EXPORT_DIR"]):
        for fn in fs:
            exported.append(os.path.join(dp, fn))
    check("export file exists in the photo export folder", any(os.path.basename(p) == exp for p in exported), exported)
    dl = c.get(f"/api/docs/{doc_id}/file/download")
    check("download is the redacted pdf", dl.status_code == 200 and dl.content[:4] == b"%PDF" and "attachment" in dl.headers.get("content-disposition", "") and "_redact.pdf" in dl.headers.get("content-disposition", ""), dl.headers.get("content-disposition"))
    prevw = c.get(f"/api/docs/{doc_id}/file/preview")
    check("preview jpeg", prevw.status_code == 200 and prevw.content[:2] == b"\xff\xd8", prevw.headers.get("content-type"))
    gal = c.get("/api/images").json()["items"]
    check("docs stay out of the photo gallery", all(i["name"] != "Quarterly Report.pdf" for i in gal) and all(i.get("mode") in ("blur", "box", None) for i in gal), [i["name"] for i in gal])

    jpeg = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(jpeg, "JPEG")
    photo = c.post("/api/upload", data={"mode": "box"}, files={"files": ("tiny.jpg", jpeg.getvalue(), "image/jpeg")})
    check("photo upload still queues blur/box", photo.status_code == 200 and photo.json()["ids"] and photo.json()["mode"] == "box", photo.text)
    docs_after = c.get("/api/docs").json()["items"]
    check("photo did not enter Docs", all(d["name"] != "tiny.jpg" for d in docs_after))

    c.post("/api/settings", json={"auto_export": False})
    up2 = c.post("/api/docs", data={"keywords": "cat"}, files={"file": ("second.pdf", pdf, "application/pdf")})
    id2 = up2.json()["id"]
    t0 = time.time()
    body2 = None
    while time.time() - t0 < 90:
        body2 = c.get(f"/api/docs/{id2}").json()
        if body2["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    check("auto-export off still finishes and skips the folder", body2 and body2["status"] == "done" and not body2["exported"], body2)
    n_export = sum(len(fs) for _, _, fs in os.walk(os.environ["EXPORT_DIR"]))
    check("no second export file", n_export == 1, n_export)
    c.post("/api/settings", json={"auto_export": True})

    gone = c.delete(f"/api/docs/{doc_id}")
    check("delete keeps the export", gone.status_code == 200 and c.get(f"/api/docs/{doc_id}").status_code == 404 and not os.path.exists(raw_path) and any(os.path.basename(p) == exp for p in exported if os.path.exists(p)))

print(f"\n{ok} passed, {fail} failed  ({ROOT})")
sys.exit(1 if fail else 0)
