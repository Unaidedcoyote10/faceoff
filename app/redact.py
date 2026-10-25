"""Face detection (OpenCV YuNet) + tight redaction (feathered ellipse blur or black box).

Pi-friendly: detection runs on a downscaled copy; boxes are mapped back to full res.
Big images additionally get a tiled high-res pass so small faces in group shots are found.
"""
from __future__ import annotations

import io
import math
import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # iPhone HEIC support
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

MODEL_PATH = os.environ.get(
    "YUNET_MODEL", os.path.join(os.path.dirname(__file__), "..", "models", "face_detection_yunet_2023mar.onnx")
)
cv2.setNumThreads(int(os.environ.get("CV_THREADS", "2")))

# sensitivity -> YuNet score threshold. Higher sensitivity = lower threshold.
SENSITIVITY_THRESHOLDS = {"low": 0.85, "medium": 0.74, "high": 0.62}

FULL_PASS_MAX = 1280      # max side for the whole-image pass
COARSE_PASSES = (640, FULL_PASS_MAX)
TILE_SRC_MAX = 3200       # max side the image is scaled to for the tiled pass
TILE = 960                # tile size for tiled pass
TILE_OVERLAP = 240
MIN_FACE_PX = 10          # in detection-image pixels
VERIFY_SKIP_SCORE = 0.90  # detections at/above this are trusted outright
VERIFY_MIN_PX = 64        # faces smaller than this (orig px) skip verification
VERIFY_MIN_SCORE = 0.80   # crop re-detection score needed

_local = threading.local()


def _detector(w: int, h: int, thr: float):
    det = getattr(_local, "det", None)
    if det is None:
        det = cv2.FaceDetectorYN.create(MODEL_PATH, "", (w, h), thr, 0.3, 5000)
        _local.det = det
    det.setInputSize((w, h))
    det.setScoreThreshold(thr)
    return det


@dataclass
class Face:
    x: float
    y: float
    w: float
    h: float
    score: float
    lm: np.ndarray  # 5x2 landmarks: r-eye, l-eye, nose, r-mouth, l-mouth

    def as_dict(self):
        return {"x": round(self.x), "y": round(self.y), "w": round(self.w), "h": round(self.h), "score": round(self.score, 3)}


def load_image(data: bytes) -> np.ndarray:
    """Decode bytes -> BGR ndarray with EXIF orientation applied (EXIF is not kept)."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _run(img: np.ndarray, thr: float, scale: float, ox: float = 0, oy: float = 0) -> list[Face]:
    h, w = img.shape[:2]
    det = _detector(w, h, thr)
    _, res = det.detect(img)
    out = []
    if res is None:
        return out
    for r in res:
        x, y, fw, fh = r[:4]
        if fw < MIN_FACE_PX or fh < MIN_FACE_PX:
            continue
        lm = r[4:14].reshape(5, 2)
        out.append(Face((x + ox) / scale, (y + oy) / scale, fw / scale, fh / scale, float(r[14]), (lm + [ox, oy]) / scale))
    return out


def _plausible(f: Face, W: int, H: int) -> bool:
    ar = f.w / max(f.h, 1)
    if not (0.45 <= ar <= 1.6):
        return False
    # landmarks should mostly sit inside the box (filters a lot of texture false positives)
    lm = f.lm
    inside = ((lm[:, 0] >= f.x - 0.15 * f.w) & (lm[:, 0] <= f.x + 1.15 * f.w) & (lm[:, 1] >= f.y - 0.15 * f.h) & (lm[:, 1] <= f.y + 1.15 * f.h)).sum()
    if inside < 4:
        return False
    # eyes above mouth (in the face's own orientation the eye-mouth distance must be meaningful)
    eye_c = lm[0:2].mean(0)
    mouth_c = lm[3:5].mean(0)
    if np.linalg.norm(mouth_c - eye_c) < 0.15 * f.h:
        return False
    return True


def _nms(faces: list[Face], iou_thr: float = 0.35) -> list[Face]:
    faces = sorted(faces, key=lambda f: f.score, reverse=True)
    keep: list[Face] = []
    for f in faces:
        ok = True
        for k in keep:
            ix = max(0, min(f.x + f.w, k.x + k.w) - max(f.x, k.x))
            iy = max(0, min(f.y + f.h, k.y + k.h) - max(f.y, k.y))
            inter = ix * iy
            if inter <= 0:
                continue
            small = min(f.w * f.h, k.w * k.h)
            union = f.w * f.h + k.w * k.h - inter
            if inter / union > iou_thr or inter / small > 0.6:
                ok = False
                break
        if ok:
            keep.append(f)
    return keep


def detect_faces(img: np.ndarray, sensitivity: str = "medium") -> list[Face]:
    thr = SENSITIVITY_THRESHOLDS.get(sensitivity, SENSITIVITY_THRESHOLDS["medium"])
    H, W = img.shape[:2]
    faces: list[Face] = []

    # 1) whole-image passes: coarse (large/close-up faces) + normal (upscale tiny images a bit)
    for side in COARSE_PASSES:
        s = min(side / max(H, W), 2.0)
        if s >= 1.0 and side != COARSE_PASSES[-1]:
            continue
        small = cv2.resize(img, (max(1, round(W * s)), max(1, round(H * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        faces += _run(small, thr, s)

    # 2) tiled pass at higher resolution for big images (small faces in group photos)
    s2 = min(1.0, TILE_SRC_MAX / max(H, W))
    if max(H, W) * s2 > FULL_PASS_MAX * 1.25:
        big = img if s2 == 1.0 else cv2.resize(img, (round(W * s2), round(H * s2)), interpolation=cv2.INTER_AREA)
        bh, bw = big.shape[:2]
        step = TILE - TILE_OVERLAP
        ys = list(range(0, max(1, bh - TILE), step)) + [max(0, bh - TILE)]
        xs = list(range(0, max(1, bw - TILE), step)) + [max(0, bw - TILE)]
        for ty in sorted(set(ys)):
            for tx in sorted(set(xs)):
                tile = big[ty:ty + TILE, tx:tx + TILE]
                # tiled pass is stricter: it sees lots of texture, small faces only
                for f in _run(np.ascontiguousarray(tile), min(0.95, thr + 0.02), s2, tx, ty):
                    faces.append(f)
            
    faces = [f for f in faces if _plausible(f, W, H)]
    faces = _nms(faces)
    # 3) verify larger, not-super-confident hits by re-detecting on a normalised crop.
    #    Kills the classic YuNet false positives (dog/cat faces, posters) without hurting tiny crowd faces.
    return [f for f in faces if f.score >= VERIFY_SKIP_SCORE or min(f.w, f.h) < VERIFY_MIN_PX or _verify(img, f)]


def _verify(img: np.ndarray, f: Face) -> bool:
    H, W = img.shape[:2]
    m = 0.6
    x0, y0 = int(max(0, f.x - f.w * m)), int(max(0, f.y - f.h * m))
    x1, y1 = int(min(W, f.x + f.w * (1 + m))), int(min(H, f.y + f.h * (1 + m)))
    crop = img[y0:y1, x0:x1]
    s = 256 / max(crop.shape[:2])
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))),
                      interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    best = max((g.score for g in _run(np.ascontiguousarray(crop), 0.3, 1.0)), default=0.0)
    return best >= VERIFY_MIN_SCORE


def _ellipse_params(f: Face, pad: float):
    """Ellipse fitted to the face box, oriented along the eye line."""
    lm = f.lm
    dx, dy = lm[1] - lm[0]  # right eye -> left eye (image coords)
    angle = math.degrees(math.atan2(dy, dx)) if abs(dx) + abs(dy) > 1 else 0.0
    if abs(angle) > 60:
        angle = 0.0
    cx = f.x + f.w / 2
    cy = f.y + f.h / 2
    # YuNet boxes are brow-to-chin; give a touch more height for forehead/hairline
    ax = f.w / 2 * (1 + pad) * 1.04
    ay = f.h / 2 * (1 + pad) * 1.10
    cy -= f.h * 0.03
    return (cx, cy), (ax, ay), angle


def redact(img: np.ndarray, faces: list[Face], mode: str = "blur", padding: float = 0.08, strength: int = 3,
           fast: bool = False) -> np.ndarray:
    """fast=True (video): blur at the pixelated resolution, then upscale, and blend in place — ~same look, far cheaper."""
    out = img if fast else img.copy()
    H, W = img.shape[:2]
    for f in faces:
        if mode == "box":
            px, py = f.w * padding, f.h * padding
            x0 = int(max(0, math.floor(f.x - px)))
            y0 = int(max(0, math.floor(f.y - py * 1.4)))  # a bit more on top for forehead
            x1 = int(min(W, math.ceil(f.x + f.w + px)))
            y1 = int(min(H, math.ceil(f.y + f.h + py)))
            cv2.rectangle(out, (x0, y0), (x1 - 1, y1 - 1), (0, 0, 0), -1)
            continue

        (cx, cy), (ax, ay), ang = _ellipse_params(f, padding)
        r = int(math.ceil(max(ax, ay))) + 4
        x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
        x1, y1 = min(W, int(cx + r)), min(H, int(cy + r))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = out[y0:y1, x0:x1]
        rh, rw = roi.shape[:2]
        # pixelate + gaussian: irreversible-looking, strong
        face_px = max(f.w, f.h)
        blocks = max(4, int(10 - strength * 1.5))  # ~ blocks across the face
        bs = max(2, int(face_px / blocks))
        sm = cv2.resize(roi, (max(1, rw // bs), max(1, rh // bs)), interpolation=cv2.INTER_AREA)
        k = int(face_px * (0.12 + 0.05 * strength)) | 1
        if fast:
            ks = max(3, int(k / bs * 2)) | 1
            sm2 = cv2.resize(sm, (sm.shape[1] * 2, sm.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
            blurred = cv2.resize(cv2.GaussianBlur(sm2, (ks, ks), 0), (rw, rh), interpolation=cv2.INTER_LINEAR)
        else:
            pix = cv2.resize(sm, (rw, rh), interpolation=cv2.INTER_NEAREST)
            blurred = cv2.GaussianBlur(pix, (k, k), 0)
        mask = np.zeros((rh, rw), np.float32)
        cv2.ellipse(mask, (round(cx - x0), round(cy - y0)), (max(1, round(ax)), max(1, round(ay))), ang, 0, 360, 1.0, -1, cv2.LINE_AA)
        feather = max(1, int(face_px * 0.06)) * 2 + 1
        mask = cv2.GaussianBlur(mask, (feather, feather), 0)
        # keep core fully opaque, feather only the rim
        mask = np.clip(mask * 1.6, 0, 1)
        if fast:
            out[y0:y1, x0:x1] = cv2.blendLinear(blurred, roi, mask, 1 - mask)
        else:
            mask = mask[..., None]
            out[y0:y1, x0:x1] = (blurred * mask + roi * (1 - mask)).astype(np.uint8)
    return out


def encode_jpeg(img: np.ndarray, quality: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("encode failed")
    return buf.tobytes()


def thumbnail(img: np.ndarray, max_side: int = 640) -> bytes:
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return encode_jpeg(img, 82)


def process_bytes(data: bytes, mode="blur", padding=0.08, sensitivity="medium", strength=3):
    img = load_image(data)
    faces = detect_faces(img, sensitivity)
    out = redact(img, faces, mode, padding, strength)
    return img, out, faces
