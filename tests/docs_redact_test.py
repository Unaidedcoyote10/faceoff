"""Keyword and preset redaction: text PDFs, whole-word matching, PII presets, OCR when Tesseract is installed, export.

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
from app.presets import find_preset_spans  # noqa: E402

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
        lines = text.split("\n")
        if len(lines) == 1:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 20 Tf 72 700 Td ({safe}) Tj ET".encode()
        else:
            parts = ["BT /F1 18 Tf 72 750 Td"]
            for i, line in enumerate(lines):
                safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                if i:
                    parts.append("0 -22 Td")
                parts.append(f"({safe}) Tj")
            parts.append("ET")
            stream = " ".join(parts).encode()
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

def surfaces(text, presets):
    return [(text[s:e], k) for s, e, k in find_preset_spans(text, presets)]

ALL = ["names", "money", "addresses", "emails", "phones", "locations"]
check("parse presets order and aliases", keywords.parse_job("", " email, phones ,locations")[1] == ["emails", "phones", "locations"])
check("presets alone are a valid job", keywords.parse_job("  ", ["names"]) == ([], ["names"]))
try:
    keywords.parse_job("   ", "")
    check("neither presets nor keywords rejected", False)
except keywords.DocError:
    check("neither presets nor keywords rejected", True)
try:
    keywords.parse_job("cat", "passport")
    check("unknown preset rejected", False)
except keywords.DocError as e:
    check("unknown preset rejected", "passport" in str(e), str(e))

def kinds(text, presets=ALL):
    return {k: sum(1 for _s, name in surfaces(text, presets) if name == k) for k in presets}

check("email shapes", all(kinds(s, ["emails"]).get("emails") == 1 for s in (
    "jane.smith@example.com", "JANE.SMITH@EXAMPLE.COM", "user+tag@mail.co.uk", "a@b.co", "jane @ example.com")))
check("not an email", kinds("not an email", ["emails"]).get("emails") == 0 and kinds("a@b", ["emails"]).get("emails") == 0)
check("phone shapes", all(kinds(s, ["phones"]).get("phones") == 1 for s in (
    "(415) 555-2671", "415-555-2671", "415.555.2671", "415 555 2671", "+1 415 555 2671",
    "+44 20 7946 0958", "1-415-555-2671", "(415)555-2671")))
check("bare digits and short numbers are not phones", all(kinds(s, ["phones"]).get("phones") == 0 for s in (
    "44821", "123456789012", "555-2671", "2026-10-25")))
check("money shapes", all(kinds(s, ["money"]).get("money") == 1 for s in (
    "$1,234.56", "$12", "EUR 50", "EUR 50.00", "USD 1,200", "USD 1200", "50 dollars", "12.50 USD", "($1,234.56)")))
check("bare decimal is not money", kinds("12.50", ["money"]).get("money") == 0 and kinds("44821", ["money"]).get("money") == 0)
check("street and po box", all(kinds(s, ["addresses"]).get("addresses") == 1 for s in (
    "742 Evergreen Terrace", "10 N. Main St.", "123 Main Street", "P.O. Box 88", "PO Box 19", "Springfield, IL 62704")))
check("street without a suffix is missed", kinds("123 Main", ["addresses"]).get("addresses") == 0)
check("locations that are on the list", surfaces("Meet in Chicago.", ["locations"]) == [("Chicago", "locations")]
      and surfaces("Then New York.", ["locations"]) == [("New York", "locations")]
      and kinds("California", ["locations"]).get("locations") == 1
      and surfaces("Chicago, IL", ["locations"]) == [("Chicago, IL", "locations")])
check("lowercase place, bare abbreviation, and omitted country are missed",
      kinds("chicago", ["locations"]).get("locations") == 0 and kinds("or", ["locations"]).get("locations") == 0
      and kinds("IN", ["locations"]).get("locations") == 0 and kinds("Jordan", ["locations"]).get("locations") == 0
      and kinds("Springfield, IL", ["locations"]).get("locations") == 0)
check("postal line is both an address and a location",
      kinds("Springfield, IL 62704") == {"names": 0, "money": 0, "addresses": 1, "emails": 0, "phones": 0, "locations": 1})
check("name shapes", all(kinds(s, ["names"]).get("names") == 1 for s in (
    "Dr. Jane Smith", "Mr John Smith", "Name: Ada Lovelace", "Name: Jane", "name: jane smith",
    "Jane Smith", "JANE SMITH", "Maria de la Cruz", "May Smith", "Dr. Pepper")))
check("name false positives stay visible", all(kinds(s, ["names"]).get("names") == 0 for s in (
    "The Quarterly Report", "May 12", "Will the project", "Dr. See below", "category catalog")))
check("account number is not personal-detail noise", surfaces("Account 44821 stays.", ALL) == [])

PII_PAGE = "\n".join([
    "Invoice for the office.",
    "Patient: Dr. Jane Smith",
    "jane.smith@example.com",
    "(415) 555-2671",
    "+44 20 7946 0958",
    "Balance due $1,234.56 soon.",
    "EUR 50",
    "742 Evergreen Terrace",
    "Springfield, IL 62704",
    "PO Box 19",
    "Meet in Chicago.",
    "Then New York.",
    "Reference OMEGA-9 today.",
    "Account 44821 stays.",
])
pii_counts = kinds(PII_PAGE)
check("page presets count each category",
      pii_counts == {"names": 1, "money": 2, "addresses": 3, "emails": 1, "phones": 2, "locations": 3}, pii_counts)

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_html = open(os.path.join(_repo, "app/static/index.html"), encoding="utf-8").read()
_js = open(os.path.join(_repo, "app/static/app.js"), encoding="utf-8").read()
_readme = open(os.path.join(_repo, "README.md"), encoding="utf-8").read()
for _pid in ALL:
    check(f"docs checkbox {_pid}", f'value="{_pid}"' in _html and 'type="checkbox"' in _html)
check("upload sends presets with keywords", "fd.append('presets'" in _js and "fd.append('keywords'" in _js)
check("readme states name and address limits", "Dr. Pepper" in _readme and "small towns" in _readme.lower() and "bare" in _readme.lower())

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

pii_pdf = build_pdf([PII_PAGE])
words_only = keywords.redact_file(pii_pdf, "words.pdf", ["OMEGA-9"])
check("custom keyword does not invent preset counts", set(words_only.by_phrase) == {"OMEGA-9"} and words_only.hits == 1, words_only.by_phrase)
both = keywords.redact_file(pii_pdf, "pii.pdf", ["OMEGA-9"], presets=ALL)
check("presets and keyword combine",
      both.by_phrase.get("OMEGA-9") == 1 and both.by_phrase.get("Emails") == 1 and both.by_phrase.get("Phone numbers") == 2
      and both.by_phrase.get("Money") == 2 and both.by_phrase.get("Addresses") == 3 and both.by_phrase.get("Names") == 1
      and both.by_phrase.get("Locations") == 3 and both.hits == 1 + 1 + 2 + 2 + 3 + 1 + 3, both.by_phrase)
check("note counts presets and does not echo the values",
      "Presets:" in both.note and "jane.smith" not in both.note.lower() and "555-2671" not in both.note and "1,234.56" not in both.note
      and "guesses" in both.note.lower(), both.note)
check("original pii pdf unchanged", b"jane.smith@example.com" in pii_pdf and b"44821" in pii_pdf)
if keywords.ocr_available():
    covered = ocr_pdf(both.data).casefold()
    print("OCR presets:", " ".join(covered.split()))
    check("preset boxes hide the details",
          "jane" not in covered and "smith" not in covered and "555" not in covered and "7946" not in covered
          and "1,234" not in covered and "1234" not in covered and "evergreen" not in covered and "62704" not in covered
          and "chicago" not in covered and "york" not in covered and "omega" not in covered, covered)
    check("neighbors and the account number stay",
          "office" in covered and "44821" in covered and "invoice" in covered, covered)
    untouched = ocr_pdf(words_only.data).casefold()
    check("keyword-only job leaves the email readable", "jane" in untouched or "@" in untouched, untouched)
    shot_doc = pdfium.PdfDocument(pii_pdf)
    shot = shot_doc[0]
    shot_bm = shot.render(scale=2, rev_byteorder=True)
    shot_im = shot_bm.to_pil().convert("RGB")
    shot_bm.close()
    shot.close()
    shot_doc.close()
    png_buf = io.BytesIO()
    shot_im.save(png_buf, "PNG")
    import pytesseract
    img_pii = keywords.redact_file(png_buf.getvalue(), "pii-page.png", ["OMEGA-9"], presets=ALL)
    check("image presets find email, phone, and the custom line",
          img_pii.ext == ".png" and img_pii.ocr_pages == 1 and img_pii.by_phrase.get("Emails", 0) >= 1
          and img_pii.by_phrase.get("Phone numbers", 0) >= 1 and img_pii.by_phrase.get("OMEGA-9", 0) >= 1
          and img_pii.by_phrase.get("Names", 0) >= 1 and img_pii.by_phrase.get("Addresses", 0) >= 1
          and img_pii.by_phrase.get("Locations", 0) >= 1 and img_pii.by_phrase.get("Money", 0) >= 1,
          img_pii.by_phrase)
    img_text = pytesseract.image_to_string(Image.open(io.BytesIO(img_pii.data))).casefold()
    print("OCR image presets:", " ".join(img_text.split()))
    check("image preset boxes hide the email and phone", "jane" not in img_text and "555" not in img_text and "omega" not in img_text, img_text)
    check("image preset boxes leave the account number", "44821" in img_text, img_text)
else:
    check("OCR available for preset visibility check", False, "tesseract missing")

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
    bad = c.post("/api/docs", data={"keywords": "  ", "presets": ""}, files={"file": ("a.pdf", pdf, "application/pdf")})
    check("API rejects a job with no preset and no keyword", bad.status_code == 400, bad.text)
    bad = c.post("/api/docs", data={"keywords": "cat", "presets": "passport"}, files={"file": ("a.pdf", pdf, "application/pdf")})
    check("API rejects an unknown preset", bad.status_code == 400 and "passport" in bad.text, bad.text)

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
    check("keyword job did not store presets", body and body.get("presets") == [] and "Emails" not in (body.get("by_phrase") or {}), body.get("by_phrase") if body else None)

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

    preset_up = c.post(
        "/api/docs",
        data={"keywords": "OMEGA-9", "presets": "names,money,addresses,emails,phones,locations"},
        files={"file": ("PII page.pdf", pii_pdf, "application/pdf")},
    )
    check("API queues presets with a custom line", preset_up.status_code == 200 and preset_up.json()["status"] == "queued", preset_up.text)
    preset_id = preset_up.json()["id"]
    t0 = time.time()
    preset_body = None
    while time.time() - t0 < 90:
        preset_body = c.get(f"/api/docs/{preset_id}").json()
        if preset_body["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    check("API preset job finished", preset_body and preset_body["status"] == "done" and preset_body["hits"] >= 13, preset_body)
    check("API returns the ticked presets", preset_body and preset_body.get("presets") == ["names", "money", "addresses", "emails", "phones", "locations"], preset_body.get("presets") if preset_body else None)
    check("API note does not echo a detected email", preset_body and "jane.smith" not in (preset_body.get("note") or "").lower(), preset_body.get("note") if preset_body else None)
    check("preset export name hides the detected values",
          preset_body and preset_body.get("exported") and "jane" not in preset_body["exported"].lower() and "555" not in preset_body["exported"] and "OMEGA" not in preset_body["exported"],
          preset_body.get("exported") if preset_body else None)
    only = c.post("/api/docs", data={"keywords": "", "presets": "emails"}, files={"file": ("mail.pdf", pii_pdf, "application/pdf")})
    check("API accepts presets with an empty keyword box", only.status_code == 200, only.text)
    gal2 = c.get("/api/images").json()["items"]
    check("preset docs stay out of the gallery", all(i["name"] != "PII page.pdf" for i in gal2))

    gone = c.delete(f"/api/docs/{doc_id}")
    check("delete keeps the export", gone.status_code == 200 and c.get(f"/api/docs/{doc_id}").status_code == 404 and not os.path.exists(raw_path) and any(os.path.basename(p) == exp for p in exported if os.path.exists(p)))

print(f"\n{ok} passed, {fail} failed  ({ROOT})")
sys.exit(1 if fail else 0)
