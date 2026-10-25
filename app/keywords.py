"""Keyword and preset redaction for PDFs and images. Separate from face blur/box.

Text PDFs: PDFium finds whole-word keyword matches, and preset detectors (emails,
phones, money, and light name/address/location heuristics) run on the same text.
Scans and photos: Tesseract (English), if the binary is installed. Output is a new
file — callers never overwrite the upload.

PDF output is flattened to images so matched words are not still selectable underneath.
That also drops the original text layer, metadata, and attachments. It is not a
certified sanitizer: OCR misses words, and a person can still read anything we did
not cover. Review the result.
"""
from __future__ import annotations

import io
import os
import re
import shutil
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .presets import PRESET_LABELS, PRESET_NOTES, PRESET_ORDER, find_preset_spans, parse_presets

_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
ALLOWED_EXT = PDF_EXT | IMAGE_EXT


class DocError(ValueError):
    """A problem we can explain to the person using the app."""


@dataclass
class DocResult:
    data: bytes
    ext: str
    media_type: str
    pages: int
    hits: int
    by_phrase: dict[str, int]
    ocr_pages: int
    preview_jpeg: bytes
    preview_page: int
    width: int
    height: int
    note: str
    skipped_ocr: int = 0


def max_pages() -> int:
    try:
        n = int(os.environ.get("DOC_MAX_PAGES", "40"))
    except ValueError:
        n = 40
    return max(1, min(200, n))


def ocr_available() -> bool:
    flag = os.environ.get("DOC_OCR", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False
    return True


def limits() -> dict:
    return {
        "ocr": ocr_available(),
        "max_pages": max_pages(),
        "formats": ["PDF", "JPEG", "PNG", "WEBP", "HEIC", "TIFF", "BMP"],
        "language": "eng" if ocr_available() else None,
        "presets": [{"id": k, "label": PRESET_LABELS[k], "note": PRESET_NOTES[k]} for k in PRESET_ORDER],
    }


def _coerce_presets(presets) -> list[str]:
    if not presets:
        return []
    try:
        return parse_presets(presets)
    except ValueError as e:
        raise DocError(str(e)) from e


def normalize_token(s: str) -> str:
    s = (s or "").replace("\u00ad", "").casefold()
    return _EDGE.sub("", s.strip())


def phrase_tokens(phrase: str) -> list[str]:
    return [t for t in (normalize_token(p) for p in phrase.split()) if t]


def parse_keywords(text: str, *, allow_empty: bool = False) -> list[str]:
    """One keyword or phrase per line. Case-insensitive de-dupe. Order kept."""
    if text is None or not str(text).strip():
        if allow_empty:
            return []
        raise DocError("Add at least one keyword or phrase, one per line.")
    out, seen = [], set()
    for line in str(text).splitlines():
        phrase = " ".join(line.split())
        if not phrase:
            continue
        if len(phrase) > 80:
            raise DocError("Each keyword or phrase must be 80 characters or fewer.")
        if not phrase_tokens(phrase):
            continue
        key = phrase.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(phrase)
        if len(out) > 40:
            raise DocError("Use 40 keywords or fewer at a time.")
    if not out:
        if allow_empty:
            return []
        raise DocError("Add at least one keyword or phrase, one per line.")
    return out


def parse_job(keywords_text: str, presets_value) -> tuple[list[str], list[str]]:
    """Custom lines plus preset ids. Either side may be empty; both empty is an error."""
    chosen = _coerce_presets(presets_value)
    phrases = parse_keywords(keywords_text, allow_empty=True)
    if not chosen and not phrases:
        raise DocError("Tick a preset or add at least one keyword or phrase, one per line.")
    return phrases, chosen


def _with_presets(by_phrase: dict, preset_counts: dict | None) -> dict:
    """Keyword counts plus preset totals. Preset values are counts, never the text that was found."""
    if not preset_counts:
        return dict(by_phrase)
    detail = dict(by_phrase)
    for key in PRESET_ORDER:
        if key not in preset_counts:
            continue
        label = PRESET_LABELS[key]
        slot = label if label not in detail else f"{label} (preset)"
        detail[slot] = preset_counts[key]
    return detail


def find_spans(tokens: list[str], phrases: list[list[str]]) -> list[tuple[int, int, int]]:
    """Inclusive-exclusive token spans. phrases are already normalized."""
    spans = []
    n = len(tokens)
    for pi, ph in enumerate(phrases):
        m = len(ph)
        if m == 0 or m > n:
            continue
        for i in range(0, n - m + 1):
            if tokens[i:i + m] == ph:
                spans.append((i, i + m, pi))
    return spans


def cluster_line_rects(boxes: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Union character boxes that sit on the same line. PDF coords, origin bottom-left."""
    items = [(l, b, r, t) for l, b, r, t in boxes if (r - l) > 0.05 or (t - b) > 0.05]
    if not items:
        return []
    heights = sorted(t - b for _, b, _, t in items if t > b)
    med_h = heights[len(heights) // 2] if heights else 8.0
    widths = sorted(r - l for l, _, r, _ in items if r > l)
    med_w = widths[len(widths) // 2] if widths else 6.0
    # A broken space glyph can be huge. Drop it so one match can't black out the page.
    cap_w = max(48.0, med_w * 8)
    items = [(l, b, r, t) for l, b, r, t in items if (r - l) <= cap_w]
    if not items:
        return []
    items.sort(key=lambda b: -((b[1] + b[3]) / 2))
    lines: list[dict] = []
    for box in items:
        cy = (box[1] + box[3]) / 2
        h = max(med_h, box[3] - box[1])
        placed = False
        for line in lines:
            if abs(cy - line["cy"]) <= max(h, line["h"]) * 0.65:
                line["boxes"].append(box)
                line["cy"] = sum((b[1] + b[3]) / 2 for b in line["boxes"]) / len(line["boxes"])
                line["h"] = max(line["h"], h)
                placed = True
                break
        if not placed:
            lines.append({"cy": cy, "h": h, "boxes": [box]})
    rects = []
    for line in lines:
        bs = line["boxes"]
        h = max(t - b for _, b, _, t in bs)
        pad = max(2.0, 0.28 * h)  # tight glyph boxes leave ink at the edges; ~0.28 of the height covers it
        rects.append((
            min(b[0] for b in bs) - pad,
            min(b[1] for b in bs) - pad,
            max(b[2] for b in bs) + pad,
            max(b[3] for b in bs) + pad,
        ))
    return rects


def _render_scale(width: float, height: float) -> float:
    try:
        dpi = float(os.environ.get("DOC_DPI", "144"))
    except ValueError:
        dpi = 144.0
    dpi = min(200.0, max(72.0, dpi))
    try:
        max_side = int(os.environ.get("DOC_OCR_MAX_SIDE", "2200"))
    except ValueError:
        max_side = 2200
    max_side = max(600, min(4000, max_side))
    scale = dpi / 72.0
    long_side = max(width, height) * scale
    if long_side > max_side:
        scale = max_side / max(width, height, 1)
    return scale


def _preview(im: Image.Image, max_side: int = 1100) -> bytes:
    w, h = im.size
    s = min(1.0, max_side / max(w, h, 1))
    if s < 1:
        im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _note(hits: int, by_phrase: dict[str, int], ocr_pages: int, skipped_ocr: int, kind: str,
          preset_counts: dict | None = None) -> str:
    preset_counts = preset_counts or {}
    missing = [p for p, n in by_phrase.items() if not n]
    if hits == 0:
        msg = "No matches, so nothing was blacked out."
        if by_phrase:
            msg += " Check the wording."
        if kind == "image" or ocr_pages:
            msg += " OCR misses handwriting, tiny type, and rotated or non-English text."
        elif skipped_ocr:
            msg += " This looks like a scan, and OCR is not available."
        if preset_counts:
            msg += " Presets only catch common patterns, so a real name, address, or place can still be visible."
    else:
        found = [f"{p} ×{n}" for p, n in by_phrase.items() if n]
        msg = f"Blacked out {hits} match{'' if hits == 1 else 'es'}"
        if found:
            msg += " (" + ", ".join(found) + ")"
        msg += "."
    if preset_counts:
        bits = [f"{PRESET_LABELS[k]} ×{preset_counts[k]}" for k in PRESET_ORDER if k in preset_counts]
        msg += " Presets: " + ", ".join(bits) + "."
        msg += " Names, addresses, and locations are guesses; emails, phones, and money follow patterns."
    if missing and hits:
        msg += " Not found: " + ", ".join(missing) + "."
    if ocr_pages:
        msg += f" OCR ran on {ocr_pages} page{'' if ocr_pages == 1 else 's'} (English)."
    if skipped_ocr:
        msg += f" {skipped_ocr} scanned page{'' if skipped_ocr == 1 else 's'} had no text layer and were not searched (OCR is off)."
    if kind == "pdf":
        msg += " Output is a flattened copy, so the covered words are not still selectable."
    msg += " Original file was not modified."
    return msg


def _pdf_match_rects(textpage, phrase: str, whole_word: bool = True) -> tuple[int, list[tuple[float, float, float, float]]]:
    """Return (occurrence count, line rectangles in PDF coordinates)."""
    searcher = textpage.search(phrase, match_case=False, match_whole_word=whole_word)
    try:
        found = 0
        rects = []
        while True:
            occ = searcher.get_next()
            if not occ:
                break
            found += 1
            start, count = occ
            boxes = []
            for k in range(count):
                try:
                    boxes.append(textpage.get_charbox(start + k, loose=False))
                except Exception:
                    continue
            rects.extend(cluster_line_rects(boxes))
        return found, rects
    finally:
        searcher.close()


def _pixels(conv, rect, iw: int, ih: int) -> tuple[int, int, int, int] | None:
    l, b, r, t = rect
    pts = []
    for x, y in ((l, b), (r, b), (l, t), (r, t)):
        try:
            pts.append(conv.to_bitmap(x, y))
        except Exception:
            return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = max(0, min(xs) - 2)
    y0 = max(0, min(ys) - 2)
    x1 = min(iw, max(xs) + 3)
    y1 = min(ih, max(ys) + 3)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1, y1


def _rects_for_span(textpage, text: str, start: int, end: int, nchars: int):
    """Map a match in extracted text back to PDF rectangles. Falls back to a text search."""
    import pypdfium2.raw as pdfium_c

    boxes = []
    for ti in range(max(0, start), min(end, len(text))):
        if text[ti] in "\r\n\u2028\u2029":
            continue
        try:
            ci = pdfium_c.FPDFText_GetCharIndexFromTextIndex(textpage, ti)
        except Exception:
            ci = ti
        if ci is None or int(ci) < 0 or int(ci) >= nchars:
            continue
        try:
            boxes.append(textpage.get_charbox(int(ci), loose=False))
        except Exception:
            continue
    rects = cluster_line_rects(boxes)
    if rects:
        return rects
    snippet = " ".join(text[start:end].split())
    if not snippet:
        return []
    whole = re.fullmatch(r"[A-Za-z]+(?:[ \t]+[A-Za-z]+)*", snippet) is not None
    _n, found = _pdf_match_rects(textpage, snippet, whole_word=whole)
    return found


def _preset_page(textpage, presets: list[str]) -> tuple[dict, list]:
    counts = {p: 0 for p in presets}
    n = textpage.count_chars()
    if n <= 0:
        return counts, []
    text = textpage.get_text_range(0, n) or ""
    spans = find_preset_spans(text, presets)
    rects = []
    for start, end, kind in spans:
        if kind in counts:
            counts[kind] += 1
        rects.extend(_rects_for_span(textpage, text, start, end, n))
    return counts, rects


def redact_pdf(data: bytes, phrases: list[str], on_progress=None, presets=None) -> DocResult:
    import pypdfium2 as pdfium
    from pypdfium2 import PdfiumError

    chosen = _coerce_presets(presets)
    if not data.startswith(b"%PDF"):
        raise DocError("This file is not a PDF.")
    try:
        src = pdfium.PdfDocument(data)
    except PdfiumError as e:
        raise DocError("Couldn't open this PDF. It may be damaged or password-protected.") from e

    out = pdfium.PdfDocument.new()
    by_phrase = {p: 0 for p in phrases}
    preset_counts = {p: 0 for p in chosen}
    hits = 0
    ocr_pages = 0
    skipped_ocr = 0
    text_pages = 0
    preview_im = None
    preview_page = 1
    width = height = 0
    try:
        n = len(src)
        if n <= 0:
            raise DocError("This PDF has no pages.")
        if n > max_pages():
            raise DocError(f"This PDF has {n} pages. The limit is {max_pages()} so a small machine can finish it.")
        # First pass: which pages have a text layer, so a fully scanned file fails clearly when OCR is off.
        page_words = []
        for i in range(n):
            page = src[i]
            try:
                tp = page.get_textpage()
                page_words.append(tp.count_chars() > 0)
                tp.close()
            finally:
                page.close()
        text_pages = sum(1 for w in page_words if w)
        need_ocr = n - text_pages
        if need_ocr and not ocr_available() and text_pages == 0:
            raise DocError(
                "This PDF has no selectable text, and OCR is not available. "
                "Upload a PDF you can highlight text in, or install Tesseract (English)."
            )
        for i in range(n):
            page = src[i]
            try:
                scale = _render_scale(*page.get_size())
                bitmap = page.render(scale=scale, rev_byteorder=True)
                try:
                    pil = bitmap.to_pil().convert("RGB").copy()
                    conv = bitmap.get_posconv(page)
                finally:
                    bitmap.close()
                if width == 0:
                    width, height = pil.size
                rects_px = []
                if page_words[i]:
                    tp = page.get_textpage()
                    try:
                        for phrase in phrases:
                            n_occ, found = _pdf_match_rects(tp, phrase)
                            by_phrase[phrase] += n_occ
                            hits += n_occ
                            for rect in found:
                                px = _pixels(conv, rect, pil.width, pil.height)
                                if px:
                                    rects_px.append(px)
                        if chosen:
                            p_counts, p_rects = _preset_page(tp, chosen)
                            for key, n_hit in p_counts.items():
                                preset_counts[key] += n_hit
                                hits += n_hit
                            for rect in p_rects:
                                px = _pixels(conv, rect, pil.width, pil.height)
                                if px:
                                    rects_px.append(px)
                    finally:
                        tp.close()
                elif ocr_available():
                    ocr_pages += 1
                    boxes, counts, p_counts = _ocr_hit_boxes(pil, phrases, presets=chosen)
                    for pi, phrase in enumerate(phrases):
                        by_phrase[phrase] += counts[pi]
                        hits += counts[pi]
                    for key, n_hit in p_counts.items():
                        preset_counts[key] += n_hit
                        hits += n_hit
                    rects_px.extend(boxes)
                else:
                    skipped_ocr += 1
                if rects_px:
                    draw = ImageDraw.Draw(pil)
                    for x0, y0, x1, y1 in rects_px:
                        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(0, 0, 0))
                    if preview_im is None:
                        preview_im = pil
                        preview_page = i + 1
                elif preview_im is None and i == 0:
                    preview_im = pil
                _add_jpeg_page(out, pdfium, page, pil)
            finally:
                page.close()
            if on_progress:
                on_progress((i + 1) / n)
        bio = io.BytesIO()
        out.save(bio)
        pdf_bytes = bio.getvalue()
    finally:
        out.close()
        src.close()

    if preview_im is None:
        preview_im = Image.new("RGB", (64, 64), "white")
    return DocResult(
        data=pdf_bytes, ext=".pdf", media_type="application/pdf", pages=n, hits=hits,
        by_phrase=_with_presets(by_phrase, preset_counts),
        ocr_pages=ocr_pages, preview_jpeg=_preview(preview_im), preview_page=preview_page, width=width, height=height,
        note=_note(hits, by_phrase, ocr_pages, skipped_ocr, "pdf", preset_counts), skipped_ocr=skipped_ocr,
    )


def _add_jpeg_page(out, pdfium, src_page, pil: Image.Image):
    """Place a JPEG of this page on a new page the same display size. No text layer."""
    dw, dh = src_page.get_size()
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=90)
    buf.seek(0)
    new_page = out.new_page(dw, dh)
    img = pdfium.PdfImage.new(out)
    img.load_jpeg(buf, inline=True)
    img.set_matrix(pdfium.PdfMatrix(dw, 0, 0, dh, 0, 0))
    new_page.insert_obj(img)
    new_page.gen_content()


def _ocr_lines(im: Image.Image) -> list[list[dict]]:
    import pytesseract
    from pytesseract import Output

    raw = pytesseract.image_to_data(im, lang="eng", output_type=Output.DICT, timeout=90)
    lines: dict[tuple, list] = {}
    order: list[tuple] = []
    for i, text in enumerate(raw["text"]):
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf = float(raw["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 30:
            continue
        key = (raw["block_num"][i], raw["par_num"][i], raw["line_num"][i])
        if key not in lines:
            lines[key] = []
            order.append(key)
        x, y, w, h = int(raw["left"][i]), int(raw["top"][i]), int(raw["width"][i]), int(raw["height"][i])
        lines[key].append({"text": text, "x0": x, "y0": y, "x1": x + w, "y1": y + h})
    return [lines[k] for k in order]


def _token_line(tokens: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    pos = 0
    for i, tok in enumerate(tokens):
        if i:
            parts.append(" ")
            pos += 1
        start = pos
        piece = tok["text"]
        parts.append(piece)
        pos += len(piece)
        spans.append((start, pos, i))
    return "".join(parts), spans


def _pad_box(group: list[dict], inv: float) -> tuple[int, int, int, int]:
    bh = max(t["y1"] - t["y0"] for t in group)
    pad = max(3, int(round(0.28 * bh)))
    x0 = min(t["x0"] for t in group) - pad
    y0 = min(t["y0"] for t in group) - pad
    x1 = max(t["x1"] for t in group) + pad
    y1 = max(t["y1"] for t in group) + pad
    return (int(x0 * inv), int(y0 * inv), int(x1 * inv) + 1, int(y1 * inv) + 1)


def _ocr_hit_boxes(im: Image.Image, phrases: list[str], scale: float = 1.0, presets=None):
    """scale is small/full (OCR image size / full image size). Boxes are returned in full-image pixels."""
    chosen = _coerce_presets(presets)
    ph = [phrase_tokens(p) for p in phrases]
    counts = [0] * len(phrases)
    pcounts = {p: 0 for p in chosen}
    boxes = []
    try:
        lines = _ocr_lines(im)
    except Exception as e:
        name = type(e).__name__
        if "Tesseract" in name or "Timeout" in name:
            raise DocError(f"OCR failed ({name}). Try a clearer image, or a PDF with selectable text.") from e
        raise
    inv = 1.0 / scale if scale else 1.0
    for line in lines:
        norms, kept = [], []
        for tok in line:
            nrm = normalize_token(tok["text"])
            if not nrm:
                continue
            norms.append(nrm)
            kept.append(tok)
        for start, end, pi in find_spans(norms, ph):
            counts[pi] += 1
            boxes.append(_pad_box(kept[start:end], inv))
        if chosen:
            shown = [tok for tok in line if (tok.get("text") or "").strip()]
            if not shown:
                continue
            text, tmap = _token_line(shown)
            for start, end, kind in find_preset_spans(text, chosen):
                idxs = [i for a, b, i in tmap if b > start and a < end]
                if not idxs or kind not in pcounts:
                    continue
                pcounts[kind] += 1
                boxes.append(_pad_box([shown[i] for i in idxs], inv))
    return boxes, counts, pcounts


def _load_pil(data: bytes) -> Image.Image:
    from . import redact

    try:
        bgr = redact.load_image(data)
    except Exception as e:
        raise DocError("Couldn't read this image. Use JPEG, PNG, WEBP, HEIC, TIFF, or BMP.") from e
    import cv2
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _image_out_ext(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in (".png", ".webp"):
        return ".png"
    return ".jpg"


def redact_image(data: bytes, name: str, phrases: list[str], on_progress=None, presets=None) -> DocResult:
    if not ocr_available():
        raise DocError("Photos of text need Tesseract OCR, which is not available. Upload a PDF with selectable text instead.")
    if on_progress:
        on_progress(0.05)
    im = _load_pil(data)
    w, h = im.size
    try:
        max_side = int(os.environ.get("DOC_OCR_MAX_SIDE", "2200"))
    except ValueError:
        max_side = 2200
    max_side = max(600, min(4000, max_side))
    long_side = max(w, h)
    ocr_im = im
    ocr_scale = 1.0
    if long_side > max_side:
        ocr_scale = max_side / long_side
        ocr_im = im.resize((max(1, round(w * ocr_scale)), max(1, round(h * ocr_scale))), Image.Resampling.LANCZOS)
    chosen = _coerce_presets(presets)
    boxes, counts, p_counts = _ocr_hit_boxes(ocr_im, phrases, ocr_scale, presets=chosen)
    by_phrase = {p: counts[i] for i, p in enumerate(phrases)}
    hits = sum(counts) + sum(p_counts.values())
    if boxes:
        draw = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in boxes:
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 > x0 and y1 > y0:
                draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(0, 0, 0))
    ext = _image_out_ext(name)
    buf = io.BytesIO()
    if ext == ".png":
        im.save(buf, "PNG")
        media = "image/png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=90)
        media = "image/jpeg"
    if on_progress:
        on_progress(1.0)
    return DocResult(
        data=buf.getvalue(), ext=ext, media_type=media, pages=1, hits=hits,
        by_phrase=_with_presets(by_phrase, p_counts), ocr_pages=1,
        preview_jpeg=_preview(im), preview_page=1, width=w, height=h,
        note=_note(hits, by_phrase, 1, 0, "image", p_counts),
    )


def output_ext_for(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in PDF_EXT:
        return ".pdf"
    return _image_out_ext(name)


def kind_for(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    raise DocError("Use a PDF, or an image (JPEG, PNG, WEBP, HEIC, TIFF, BMP).")


def redact_file(data: bytes, name: str, phrases: list[str], on_progress=None, presets=None) -> DocResult:
    chosen = _coerce_presets(presets)
    if not data:
        raise DocError("That file is empty.")
    if not phrases and not chosen:
        raise DocError("Tick a preset or add at least one keyword or phrase, one per line.")
    kind = kind_for(name)
    if kind == "pdf":
        return redact_pdf(data, phrases, on_progress, presets=chosen)
    return redact_image(data, name, phrases, on_progress, presets=chosen)
