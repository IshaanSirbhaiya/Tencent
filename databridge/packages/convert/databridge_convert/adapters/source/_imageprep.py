"""Deterministic image preprocessing for OCR (C13, §11.3): deskew + binarise.

Preprocessing is **deterministic** (no randomness) so OCR provenance is reproducible: the
same bytes always yield the same page raster, and ``ocr_bbox`` coordinates are expressed in
the coordinate space of *this* raster (which is what we persist as the MediaRecord page — so
a bbox always crops the exact region the engine read).

opencv-headless only (no GUI/display deps — airgap/CI safe).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .._util import AdapterError


@dataclass(frozen=True)
class PreprocessResult:
    raster: np.ndarray        # 3-channel uint8 image FED to OCR == the persisted page raster
    skew_deg: float           # estimated + corrected skew angle (degrees)
    binarised: bool


# Decompression-bomb ceiling for rasters (F7): a few-KB PNG/TIFF can declare a multi-gigapixel
# image; cv2 would allocate H·W·3 bytes. Cap total pixels and refuse before/after decode.
MAX_IMAGE_PIXELS = 60_000_000        # 60 MP (~180 MB at 3 channels) — generous for scans


def guard_pixels(h: int, w: int, *, label: str = "image") -> None:
    if h * w > MAX_IMAGE_PIXELS:
        raise AdapterError(f"{label}: {w}x{h} = {h * w} px exceeds {MAX_IMAGE_PIXELS} — "
                           "image decompression bomb (§21.5)")


def load_image(data: bytes) -> np.ndarray:
    """Decode image bytes → BGR uint8. Refuses non-images and pixel bombs loudly."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise AdapterError("image_ocr: bytes are not a decodable raster image")
    guard_pixels(img.shape[0], img.shape[1], label="image_ocr")
    return img


def _to_gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


_SKEW_LIMIT_DEG = 15.0       # deskew corrects SMALL rotations; 90° flips are orientation, not skew
_SKEW_STEP_DEG = 0.5


def _profile_score(binv: np.ndarray, angle: float) -> float:
    """Score a candidate skew angle by the variance of the horizontal projection profile:
    when text rows are axis-aligned, row-sums are sharply peaked (high variance)."""
    h, w = binv.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rot = cv2.warpAffine(binv, m, (w, h), flags=cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    profile = rot.sum(axis=1, dtype=np.float64)
    return float(profile.var())


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate page skew in degrees via a projection-profile search over SMALL angles
    (±15°). Returns the best angle; 0.0 when there is too little ink or no clear skew.

    Deterministic (fixed grid, no randomness). Downscales large pages first so the search is
    cheap. Only small rotations are corrected — a 90°-rotated page is an *orientation* problem
    (out of scope, would be flagged), never silently spun by the deskewer (the old minAreaRect
    approach mis-fired to 90° on clean horizontal text and destroyed detection)."""
    _t, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if int((binv > 0).sum()) < 50:                    # too little ink to trust an angle
        return 0.0
    # downscale for the search (coordinates of the profile are scale-invariant in angle).
    h, w = binv.shape[:2]
    scale = min(1.0, 1000.0 / max(h, w))
    if scale < 1.0:
        binv = cv2.resize(binv, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_NEAREST)
    base = _profile_score(binv, 0.0)
    best_angle, best_score = 0.0, base
    n = int(_SKEW_LIMIT_DEG / _SKEW_STEP_DEG)
    for k in range(-n, n + 1):
        ang = k * _SKEW_STEP_DEG
        if ang == 0.0:
            continue
        s = _profile_score(binv, ang)
        if s > best_score:
            best_score, best_angle = s, ang
    # require a meaningful improvement over no-rotation to avoid chasing noise.
    if abs(best_angle) < 0.3 or best_score <= base * 1.02:
        return 0.0
    return float(best_angle)


def deskew(img: np.ndarray, angle: float) -> np.ndarray:
    """Rotate ``img`` by ``+angle`` (the same angle ``estimate_skew`` scored as best-aligning
    the projection profile) about its centre, padding with white (paper). The estimate and the
    correction use the SAME rotation operator/sign, so the skew is corrected once, not doubled."""
    if angle == 0.0:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def binarise(gray: np.ndarray) -> np.ndarray:
    """Denoise + Otsu binarise → clean black-on-white. Returns single-channel uint8."""
    den = cv2.medianBlur(gray, 3)
    _t, bw = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def preprocess(data: bytes, *, do_deskew: bool = True, do_binarise: bool = True) -> PreprocessResult:
    """bytes → deterministic deskewed/binarised 3-channel raster ready for an OcrEngine.

    The returned raster is BOTH what we OCR and what we persist as the MediaRecord page, so
    ``ocr_bbox`` coordinates resolve against it exactly."""
    img = load_image(data)
    gray = _to_gray(img)
    skew = estimate_skew(gray) if do_deskew else 0.0
    if skew:
        img = deskew(img, skew)
        gray = _to_gray(img)
    if do_binarise:
        bw = binarise(gray)
        raster = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
    else:
        raster = img if img.ndim == 3 else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return PreprocessResult(raster=raster, skew_deg=skew, binarised=do_binarise)


def preprocess_array(img: np.ndarray, *, do_deskew: bool = True,
                     do_binarise: bool = True) -> PreprocessResult:
    """Same as ``preprocess`` but starting from an in-memory array (used by the PDF-page OCR
    route, where pypdfium2 already rasterised the page)."""
    gray = _to_gray(img)
    skew = estimate_skew(gray) if do_deskew else 0.0
    if skew:
        img = deskew(img, skew)
        gray = _to_gray(img)
    if do_binarise:
        raster = cv2.cvtColor(binarise(gray), cv2.COLOR_GRAY2BGR)
    else:
        raster = img if img.ndim == 3 else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return PreprocessResult(raster=raster, skew_deg=skew, binarised=do_binarise)
