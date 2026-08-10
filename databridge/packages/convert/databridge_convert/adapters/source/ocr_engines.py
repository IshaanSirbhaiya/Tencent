"""OCR engine abstraction (C13, 09_FINAL_PRD §11.3).

One ``OcrEngine`` interface; pluggable backends behind it. This is the seam that keeps the
image/PDF-OCR adapters engine-agnostic — they speak ``OcrResult``, never a vendor API.

**Engine policy (OD-3, RESOLVED — see ADR-0007):**
  * Primary  : **RapidOCR** (ONNX, fully local / airgap-safe — bundled models, no network).
  * Fallback : **Tesseract** (pytesseract; needs the system `tesseract` binary).
  * Future   : **docling**-bundled OCR and **OpenDataLoader** are registered as *future-scope*
               engines (Stage 2+). They are real entries in ``OCR_ENGINES`` so the extension
               point is discoverable and tested-as-unavailable today; ``recognize`` raises a
               typed "future scope" error until implemented. Adding one is: write the class,
               append it to the chain — nothing else changes (the adapters don't move).

Both runnable engines emit **per-region boxes + confidence** in one normalised shape so the
adapter maps every recognised unit to an ``ocr_bbox`` SourcePointer (§17.1) and retains
word/region confidence for §11.5 blending.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

import numpy as np


# --- normalised result shape ----------------------------------------------------
@dataclass(frozen=True)
class OcrLine:
    """One recognised text region (a line for RapidOCR, a grouped word-run for Tesseract).
    Box is axis-aligned in pixel coordinates of the (preprocessed) page raster."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float                                   # region confidence ∈ [0,1]
    word_confs: tuple[float, ...] = ()            # per-word confidences when the engine emits them


@dataclass(frozen=True)
class OcrResult:
    lines: list[OcrLine]
    width: int
    height: int
    engine_name: str


class OcrUnavailable(RuntimeError):
    """No OCR engine in the chain is usable in this environment (no models / no binary)."""


class OcrFutureScope(NotImplementedError):
    """A registered-but-unimplemented future engine (docling / OpenDataLoader) was invoked."""


@runtime_checkable
class OcrEngine(Protocol):
    name: str
    # Whether GLOBAL binarisation helps this engine. Classical OCR (Tesseract) wants a clean
    # 1-bit image; the neural PP-OCR detector (RapidOCR) normalises internally and a hard
    # Otsu pass DESTROYS its detection (measured — see ADR-0007 / session note). So §11.3's
    # "deskew + binarise" is applied engine-aware: deskew always, binarise only where it helps.
    wants_binarise: bool
    def available(self) -> bool: ...                                  # cheap, no heavy import
    def recognize(self, image: np.ndarray) -> OcrResult: ...          # image = HxW or HxWx3


# --- helpers --------------------------------------------------------------------
def _quad_to_bbox(quad) -> tuple[float, float, float, float]:
    """RapidOCR returns a 4×2 polygon per region; collapse to an axis-aligned bbox."""
    pts = np.asarray(quad, dtype=float).reshape(-1, 2)
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


# --- RapidOCR (primary) ---------------------------------------------------------
class RapidOcrEngine:
    name = "rapidocr"
    wants_binarise = False        # neural detector normalises internally; Otsu hurts it (measured)

    def __init__(self) -> None:
        self._engine = None                       # lazily constructed (model load ~1s)

    def available(self) -> bool:
        import importlib.util
        # the unified `rapidocr` package OR the older `rapidocr_onnxruntime` both work.
        spec = (importlib.util.find_spec("rapidocr")
                or importlib.util.find_spec("rapidocr_onnxruntime"))
        if spec is None:
            return False
        # Airgap (P1, F10): verify the ONNX models are present ON DISK so we never pick an
        # engine that would try to DOWNLOAD them at first recognize(). If the models aren't
        # bundled, treat the engine as unavailable (fall through to Tesseract / refuse).
        return self._models_present(spec)

    @staticmethod
    def _models_present(spec) -> bool:
        import os
        from pathlib import Path
        try:
            root = Path(spec.origin).parent / "models"
            if not root.is_dir():
                return False
            return any(p.suffix == ".onnx" for p in root.iterdir())
        except Exception:
            return False

    def _get(self):
        if self._engine is None:
            try:
                from rapidocr import RapidOCR              # unified package
            except ImportError:                            # pragma: no cover - legacy path
                from rapidocr_onnxruntime import RapidOCR
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: np.ndarray) -> OcrResult:
        h, w = image.shape[:2]
        arr = image if image.ndim == 3 else np.stack([image] * 3, axis=-1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = self._get()(arr)
        lines: list[OcrLine] = []
        boxes = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None) or ()
        scores = getattr(res, "scores", None) or ()
        if boxes is not None:
            boxes = np.asarray(boxes)
            n = min(len(txts), len(boxes))         # guard: txts/boxes must be parallel (L)
            for i in range(n):
                x0, y0, x1, y1 = _quad_to_bbox(boxes[i])
                conf = float(scores[i]) if i < len(scores) else 0.0
                lines.append(OcrLine(text=str(txts[i]), x0=x0, y0=y0, x1=x1, y1=y1,
                                     conf=conf, word_confs=(conf,)))
        return OcrResult(lines=lines, width=w, height=h, engine_name=self.name)


# --- Tesseract (fallback) -------------------------------------------------------
class TesseractEngine:
    name = "tesseract"
    wants_binarise = True         # classical OCR benefits from a clean 1-bit image

    def available(self) -> bool:
        import importlib.util
        if importlib.util.find_spec("pytesseract") is None:
            return False
        try:
            import pytesseract
            pytesseract.get_tesseract_version()        # raises if the binary is absent
            return True
        except Exception:
            return False

    def recognize(self, image: np.ndarray) -> OcrResult:
        import pytesseract
        from pytesseract import Output
        h, w = image.shape[:2]
        data = pytesseract.image_to_data(image, output_type=Output.DICT)
        # group word boxes into lines by (block, par, line) — retain per-word confidence.
        groups: dict[tuple, list[int]] = {}
        for i, txt in enumerate(data["text"]):
            if not str(txt).strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(i)
        lines: list[OcrLine] = []
        for key, idxs in groups.items():
            words = [str(data["text"][i]) for i in idxs]
            confs = [max(0.0, float(data["conf"][i]) / 100.0) for i in idxs]
            xs0 = [data["left"][i] for i in idxs]
            ys0 = [data["top"][i] for i in idxs]
            xs1 = [data["left"][i] + data["width"][i] for i in idxs]
            ys1 = [data["top"][i] + data["height"][i] for i in idxs]
            lines.append(OcrLine(
                text=" ".join(words),
                x0=float(min(xs0)), y0=float(min(ys0)),
                x1=float(max(xs1)), y1=float(max(ys1)),
                conf=float(sum(confs) / len(confs)) if confs else 0.0,
                word_confs=tuple(confs)))
        return OcrResult(lines=lines, width=w, height=h, engine_name=self.name)


# --- future-scope engines (registered, not yet implemented — see ADR-0007) ------
class _FutureEngine:
    name = "future"
    _why = "future scope"
    wants_binarise = False

    def available(self) -> bool:
        return False

    def recognize(self, image: np.ndarray) -> OcrResult:
        raise OcrFutureScope(
            f"OCR engine {self.name!r} is {self._why} (Stage 2+); not implemented in the POC. "
            "Use the default RapidOCR→Tesseract chain.")


class DoclingEngine(_FutureEngine):
    name = "docling"
    _why = ("future scope — docling-bundled OCR/layout stack (richer table+layout structure); "
            "planned post-POC, see [[50-research/tools/ocr-engines]]")


class OpenDataLoaderEngine(_FutureEngine):
    name = "opendataloader"
    _why = ("future scope — OpenDataLoader document model; planned post-POC, see "
            "[[50-research/tools/ocr-engines]]")


# --- registry + selection -------------------------------------------------------
# Ordered chain: the adapter walks this and uses the first ``available()`` engine.
# Future engines sit at the END (unavailable today) so they never shadow the runnable ones.
_RAPID = RapidOcrEngine()
_TESS = TesseractEngine()
OCR_ENGINE_CHAIN: list[OcrEngine] = [_RAPID, _TESS]

# All known engines (incl. future) keyed by name — the discoverable extension surface.
OCR_ENGINES: dict[str, OcrEngine] = {
    _RAPID.name: _RAPID,
    _TESS.name: _TESS,
    "docling": DoclingEngine(),
    "opendataloader": OpenDataLoaderEngine(),
}


def select_engine(prefer: Optional[str] = None) -> OcrEngine:
    """Resolve the OCR engine: an explicit ``prefer`` if usable, else the first available in
    the RapidOCR→Tesseract chain. Raises ``OcrUnavailable`` if none can run here (CI without
    models/binary skips, mirroring the live-PG / live-Ollama deselect pattern)."""
    if prefer is not None:
        eng = OCR_ENGINES.get(prefer)
        if eng is None:
            raise OcrUnavailable(f"unknown OCR engine {prefer!r}; known: {sorted(OCR_ENGINES)}")
        if isinstance(eng, _FutureEngine):
            # not-implemented is distinct from not-installed — be explicit, don't silently fall back.
            raise OcrFutureScope(
                f"OCR engine {prefer!r} is future scope (Stage 2+), not implemented in the POC; "
                "default chain is RapidOCR→Tesseract")
        if eng.available():
            return eng
        warnings.warn(f"OCR engine {prefer!r} unavailable; falling back to the default chain",
                      stacklevel=2)
    for eng in OCR_ENGINE_CHAIN:
        if eng.available():
            return eng
    raise OcrUnavailable(
        "no OCR engine available (RapidOCR models / Tesseract binary missing). "
        "Install `rapidocr` (airgap-safe, bundled models) or the system tesseract binary.")
