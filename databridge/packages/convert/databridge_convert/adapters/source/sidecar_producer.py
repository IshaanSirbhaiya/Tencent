"""Sidecar producer: a document in, the OCR sidecar the document adapter already reads, out.

``packages/convert`` can turn a real document into per-line geometry and confidence
(``docling_extract``, ``ocr_engines``). ``packages/adapter``'s ``SidecarReader`` consumes OCR
sidecars and mints ``ocr_bbox`` pointers from them. **Nothing joined the two.** The shipped
fixtures under ``fixtures/packs/government/inspection_photos/sidecars/`` are pre-computed, which is
right for a test pack — a layout model in the test suite would be absurd — but it means the reader
has never been fed by a real recognition run, and a deployment has no way to make one. This module
is that missing half.

**The contract is the consumer's, not ours.** Every shape decision below was read out of
``SidecarReader`` and the shipped fixtures, not invented:

* ``ocr.coordinate_origin`` must be ``"top_left"`` and every block's ``bbox_format`` must be
  ``"xywh_pixels"`` — the reader *refuses* anything else rather than assuming, so a producer that
  emits a different convention gets a loud refusal (good) and one that emits the right words over
  the wrong numbers gets a silent mis-citation (the thing to avoid).
* ``bbox`` is ``[x, y, w, h]``, and the reader checks it lies inside ``image.width ×
  image.height``; a block outside is emitted **without its text**. So the boxes are clamped here,
  after rounding, rather than left to fall off the page.
* ``line`` / ``confidence`` are located by ``_block_bounds``, which anchors on the block's own text
  literal, walks *back* to the nearest ``{`` and *forward* to the nearest ``}``. That makes the
  block's key order load-bearing: no ``{`` may appear before ``text`` and no ``}`` between ``text``
  and the end of the block. Hence exactly ``line, text, confidence, bbox, bbox_format`` — the
  fixture's own order — with ``bbox`` a list, never an object.
* The reader locates every scalar by scanning for the **first** occurrence of its key, so all
  top-level scalars are emitted before any nested object, and ``page`` exists once, under ``ocr``.
* Serialisation is ``json.dumps(indent=2, ensure_ascii=True)`` plus a trailing newline, which
  reproduces the shipped fixtures byte for byte. It is not cosmetic: the reader finds a block by
  ``text.find(json.dumps(block_text))``, so the escaping convention in the file has to be the one
  ``json.dumps`` produces.

**The text-layer rule, which is the whole point of doing this properly.**
``docling_extract`` emits an ``ocr_bbox`` pointer only for a line docling marks ``from_ocr``: a PDF
text layer has geometry but no recognition step ran over it, so there is no confidence to report,
and inventing ``ocr_conf=1.0`` would be a value without a source. That judgement has to survive the
trip through a sidecar, and the sidecar format gives it nowhere obvious to live — every block in
``ocr.blocks`` is read as ``float(block.get("confidence") or 0.0)``, which turns a missing or null
confidence into a **fabricated 0.0**. So:

    ``ocr.blocks``          lines a recognition step produced. Always a real numeric confidence.
    ``text_layer.blocks``   lines lifted from a text layer. ``confidence: null``, ``source:
                            "text_layer"``, and NOT in ``ocr.blocks``, because a reader that saw
                            them there would have to invent the number the format demands.

Nothing is lost and nothing is invented: the text-layer lines keep their geometry, in their own
list, where the field that does not exist for them is explicitly ``null``.

**Degraded mode is a refusal, not a guess.** docling is an optional extra
(``pip install -e "packages/convert[docling]"``). Without it a PDF cannot be split into text-layer
and recognised lines at all, so ``produce`` raises ``ProducerUnavailable`` naming the install
command. A raster still has an honest route — every line in an image IS a recognition — so images
fall back to the ``ocr_engines`` chain, and refuse the same way when no engine is installed either.

**Determinism.** No timestamp is written unless the caller passes one, so the same bytes through
the same engine give the same file. What the producer will *not* do is claim reproducibility on
the engine's behalf: ``ocr.determinism.claim`` is ``"not_asserted"`` for docling and the OCR chain,
because neither pins its models here. A caller supplying its own ``EngineDescriptor`` can assert
otherwise for an engine it does pin.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .._util import AdapterError
from . import _imageprep, docling_extract
from .ocr_engines import OcrEngine, OcrResult, OcrUnavailable, select_engine

__all__ = [
    "BBOX_FORMAT_BY_UNIT",
    "COORDINATE_ORIGIN",
    "EngineDescriptor",
    "ProducerUnavailable",
    "SIDECAR_FORMAT",
    "SidecarLine",
    "SidecarPage",
    "SidecarProducerError",
    "build_sidecar",
    "docling_descriptor",
    "index_rows",
    "ocr_chain_descriptor",
    "pages_from_docling",
    "pages_from_ocr_result",
    "produce",
    "serialise",
    "write_index",
    "write_sidecars",
]

#: Stamped into every produced sidecar so a consumer can tell a produced one from a hand-authored
#: fixture, and so a later format change is detectable rather than inferred from missing keys.
SIDECAR_FORMAT = "databridge/ocr-sidecar@1"

#: The only origin `SidecarReader` implements. Emitting anything else is a refusal at the consumer.
COORDINATE_ORIGIN = "top_left"

#: Geometry units → the `bbox_format` token that describes them. Only `pixels` is consumable by
#: today's reader; `points` exists because a PDF text-layer page's boxes ARE in points, and saying
#: "pixels" over point coordinates is the silent mis-citation this module exists to prevent.
BBOX_FORMAT_BY_UNIT = {"pixels": "xywh_pixels", "points": "xywh_points"}

_INSTALL_DOCLING = 'pip install -e "packages/convert[docling]"'

#: Written into `ocr.conf_policy` verbatim. It is addressed at the consumer, because the consumer
#: is where the mistake happens: `float(block.get("confidence") or 0.0)` fabricates a confidence.
_CONF_POLICY = (
    "`ocr.blocks` carries only lines a recognition step produced, each with that engine's own "
    "confidence. A line lifted from a PDF text layer has geometry but no recognition and therefore "
    "no confidence; those are in `text_layer.blocks` with `confidence: null` and are deliberately "
    "NOT ocr blocks. A reader that defaults an absent confidence to 0.0 is inventing one."
)

_TEXT_LAYER_NOTE = (
    "Geometry for lines taken from the document's own text layer. No OCR ran on them, so there is "
    "no confidence to report and none is supplied. They are listed apart from `ocr.blocks` so that "
    "no consumer can mint an `ocr_bbox` pointer carrying a confidence nobody measured; their "
    "grounding is a `doc_span` into the extracted text, not a box on a raster."
)

_CAPTURE_NOTE = (
    "This sidecar has NOT been captured by the producer. An `ocr_bbox` pointer must resolve "
    "against a capture of these bytes, so the ingesting adapter has to capture the file before it "
    "mints pointers from it."
)

_NOTICE = (
    "Produced by databridge-convert sidecar_producer from a real extraction run. The page raster "
    "these boxes address is not shipped beside this file (`image_present` is false); persist it "
    "and reference it by `image_id` if the boxes are to be resolvable to pixels."
)


class SidecarProducerError(AdapterError):
    """The producer cannot make an honest sidecar out of what it was given."""


class ProducerUnavailable(SidecarProducerError):
    """No route to real geometry in this environment — the refusal, never a fabricated sidecar."""


# =======================================================================================
# engines
# =======================================================================================


@dataclass(frozen=True)
class EngineDescriptor:
    """What recognised this, and whether it claims to do so reproducibly.

    ``deterministic`` is deliberately three-valued. ``None`` means *not asserted* — which is the
    honest answer for an engine whose models are resolved at run time — and is different from
    ``False``, which is a positive claim that re-running will differ.
    """

    name: str
    version: str
    deterministic: Optional[bool] = None
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}/{self.version}"

    def as_json(self) -> dict[str, Any]:
        claim = ("deterministic" if self.deterministic is True
                 else "not_deterministic" if self.deterministic is False
                 else "not_asserted")
        return {"claim": claim, "note": self.note}


def _installed_version(*candidates: str) -> str:
    """The installed version of the first distribution that answers, else ``"unknown"``.

    ``"unknown"`` is a fact about this environment and is written as such. Substituting a plausible
    version string would put a number in the citation that nothing supports.
    """
    for dist in candidates:
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def docling_descriptor() -> EngineDescriptor:
    return EngineDescriptor(
        name="docling", version=_installed_version("docling"), deterministic=None,
        note=("not asserted: docling resolves its layout and OCR models at run time and this "
              "producer does not pin them, so a different model cache, thread count or device can "
              "change the recognised lines. Re-running is not a check of this sidecar; re-reading "
              "the captured bytes is."))


def ocr_chain_descriptor(engine: OcrEngine) -> EngineDescriptor:
    # The distribution that carries each engine differs from the engine's own short name; both
    # candidates are tried rather than assumed, and an unknown answer stays "unknown".
    dists = {"rapidocr": ("rapidocr", "rapidocr_onnxruntime"), "tesseract": ("pytesseract",)}
    return EngineDescriptor(
        name=engine.name, version=_installed_version(*dists.get(engine.name, (engine.name,))),
        deterministic=None,
        note=("not asserted: the recognition models are whatever this environment has installed "
              "and are not pinned by this producer."))


# =======================================================================================
# the neutral page shape both routes produce
# =======================================================================================


@dataclass(frozen=True)
class SidecarLine:
    """One recognised or lifted line, in the page's own coordinate space, top-left origin."""

    line: int                     # position in page reading order
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    from_ocr: bool
    ocr_conf: Optional[float]     # None iff not `from_ocr` — never defaulted


@dataclass(frozen=True)
class SidecarPage:
    page: int                     # 1-based
    width: float
    height: float
    unit: str                     # "pixels" | "points"
    space: str                    # what the coordinates address, e.g. "page_raster"
    lines: tuple[SidecarLine, ...]


def pages_from_docling(extracted: "docling_extract.ExtractedDocument", *,
                       input_kind: str = "pdf") -> tuple[SidecarPage, ...]:
    """``ExtractedDocument`` → one ``SidecarPage`` per page that has geometry.

    ``unit`` is derived rather than declared: docling reports a rasterised page's cells in the
    *pixels* of the render it OCR'd, and a text-layer PDF page's cells in *points*. A page with any
    OCR'd line was rasterised, so its space is pixels. An image input is always a raster.
    """
    if input_kind not in {"pdf", "image"}:
        raise SidecarProducerError(f"unknown input kind {input_kind!r}; expected 'pdf' or 'image'")
    by_page: dict[int, list] = {}
    for region in extracted.regions:
        by_page.setdefault(region.page, []).append(region)

    pages: list[SidecarPage] = []
    for page_no in sorted(by_page):
        regions = by_page[page_no]
        rasterised = input_kind == "image" or any(r.media_id is not None for r in regions)
        unit = "pixels" if rasterised else "points"
        # The line's text is the slice its span names in the text that was returned — taken from
        # there, not from a parallel copy, so the sidecar and the stored document body can never
        # drift into citing different characters.
        lines = tuple(
            SidecarLine(line=idx, text=extracted.text[r.char_start:r.char_end],
                        x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1,
                        from_ocr=r.from_ocr, ocr_conf=r.ocr_conf)
            for idx, r in enumerate(regions))
        pages.append(SidecarPage(
            page=page_no, width=regions[0].page_width, height=regions[0].page_height,
            unit=unit, space="page_raster" if rasterised else "pdf_user_space",
            lines=lines))
    return tuple(pages)


def pages_from_ocr_result(result: OcrResult, *, page: int = 1,
                          space: str = "decoded_image_pixels") -> tuple[SidecarPage, ...]:
    """An ``OcrResult`` → one page. Every line here came out of a recogniser, so every line is
    ``from_ocr`` and carries that engine's confidence."""
    lines = tuple(
        SidecarLine(line=idx, text=ln.text, x0=ln.x0, y0=ln.y0, x1=ln.x1, y1=ln.y1,
                    from_ocr=True, ocr_conf=float(ln.conf))
        for idx, ln in enumerate(result.lines))
    return (SidecarPage(page=page, width=float(result.width), height=float(result.height),
                        unit="pixels", space=space, lines=lines),)


# =======================================================================================
# rendering
# =======================================================================================


def _num(value: float) -> Any:
    """Round to 2dp, and emit an integral value as an ``int`` — which is what the fixtures do."""
    rounded = round(float(value) + 0.0, 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _bbox(line: SidecarLine, width: float, height: float) -> Optional[list[Any]]:
    """``[x, y, w, h]``, clamped into the declared page rect, or ``None`` if nothing is left.

    Clamping happens *after* rounding because the reader's bounds check is ``x + w > width``: a box
    that rounds a hundredth of a pixel past the edge loses its text at the consumer, silently. A
    box with no area left is not emitted at all — a zero-area citation frames nothing.
    """
    x0, x1 = sorted((float(line.x0), float(line.x1)))
    y0, y1 = sorted((float(line.y0), float(line.y1)))
    x, y = _num(max(0.0, x0)), _num(max(0.0, y0))
    w, h = _num(x1 - max(0.0, x0)), _num(y1 - max(0.0, y0))
    if width > 0:
        w = _num(min(float(w), width - float(x)))
    if height > 0:
        h = _num(min(float(h), height - float(y)))
    if float(w) <= 0 or float(h) <= 0:
        return None
    return [x, y, w, h]


def _block(line: SidecarLine, bbox: list[Any], bbox_format: str,
           *, text_layer: bool) -> dict[str, Any]:
    """One block. Key order is the fixture's and is load-bearing — see the module docstring."""
    block: dict[str, Any] = {
        "line": line.line,
        "text": line.text,
        "confidence": None if line.ocr_conf is None else round(float(line.ocr_conf), 4),
        "bbox": bbox,
        "bbox_format": bbox_format,
    }
    if text_layer:
        block["source"] = "text_layer"
    return block


def build_sidecar(page: SidecarPage, *, image_id: str, document_id: str,
                  engine: EngineDescriptor, route: str,
                  filename: Optional[str] = None, source_media_type: Optional[str] = None,
                  source_sha256: Optional[str] = None, language: Optional[str] = None,
                  preprocess: Optional[dict[str, Any]] = None,
                  produced_at: Optional[str] = None) -> dict[str, Any]:
    """One page → the sidecar document, as a plain dict in the order it must be serialised in.

    All top-level scalars precede every nested object because ``SidecarReader`` resolves a field by
    the **first** occurrence of its key anywhere in the file; a nested key that shadowed a
    top-level one would hand the consumer a span inside the wrong object.
    """
    if page.unit not in BBOX_FORMAT_BY_UNIT:
        raise SidecarProducerError(
            f"unknown geometry unit {page.unit!r}; known: {sorted(BBOX_FORMAT_BY_UNIT)}")
    bbox_format = BBOX_FORMAT_BY_UNIT[page.unit]

    ocr_blocks: list[dict[str, Any]] = []
    text_blocks: list[dict[str, Any]] = []
    dropped = 0
    for line in page.lines:
        if not line.text.strip():
            continue
        box = _bbox(line, page.width, page.height)
        if box is None:
            dropped += 1
            continue
        if line.from_ocr and line.ocr_conf is not None:
            ocr_blocks.append(_block(line, box, bbox_format, text_layer=False))
        elif line.from_ocr:
            # Recognised, but the engine reported no confidence for it. It is not a text-layer line
            # and it cannot be an ocr block either, because the format has no way to say "recognised,
            # confidence unknown" that a consumer will not read as a number.
            dropped += 1
        else:
            text_blocks.append(_block(line, box, bbox_format, text_layer=True))

    confidences = [b["confidence"] for b in ocr_blocks]
    doc: dict[str, Any] = {
        "notice": _NOTICE,
        "sidecar_format": SIDECAR_FORMAT,
        "image_id": image_id,
        "filename": filename,
        "image_present": False,
        "document_id": document_id,
        "source_media_type": source_media_type,
        "source_sha256": source_sha256,
    }
    if produced_at is not None:
        # Opt-in only: a wall-clock stamp makes two runs over the same bytes differ, which is the
        # one property a caller may want to trade away knowingly rather than have taken from them.
        doc["produced_at"] = produced_at
    doc["image"] = {
        "width": _num(page.width),
        "height": _num(page.height),
        "unit": page.unit,
        "space": page.space,
    }
    ocr: dict[str, Any] = {
        "engine": engine.label,
        "engine_name": engine.name,
        "engine_version": engine.version,
        "determinism": engine.as_json(),
        "route": route,
        "language": language,
        "page": page.page,
        "coordinate_origin": COORDINATE_ORIGIN,
        "line_index": ("page_reading_order — indices may be non-contiguous where text-layer lines "
                       "are interleaved; those lines are under `text_layer.blocks`"),
        "conf_policy": _CONF_POLICY,
        "blocks_dropped_no_geometry": dropped,
        "mean_confidence": (round(sum(confidences) / len(confidences), 4) if confidences
                            else None),
        "blocks": ocr_blocks,
    }
    if preprocess is not None:
        ocr["preprocess"] = preprocess
    doc["ocr"] = ocr
    # After `ocr`, always: the consumer walks the file forward looking for each ocr block's text
    # literal, so a text-layer copy of the same line must never sit earlier in the bytes.
    doc["text_layer"] = {
        "note": _TEXT_LAYER_NOTE,
        "bbox_format": bbox_format,
        "blocks": text_blocks,
    }
    doc["capture"] = {"captured_into": None, "note": _CAPTURE_NOTE}
    return doc


def serialise(sidecar: dict[str, Any]) -> str:
    """The exact serialisation the shipped fixtures use — this is part of the contract.

    ``allow_nan=False`` because ``NaN`` / ``Infinity`` are not JSON; a consumer's parser would
    either refuse the file or, worse, accept a confidence that compares false against everything.
    """
    return json.dumps(sidecar, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


# =======================================================================================
# the routes
# =======================================================================================


def _image_id(document_id: str, page: int) -> str:
    """A filesystem-safe id for the page raster. This becomes the pointer's ``media_id``."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in document_id) or "document"
    return f"{safe}-p{page:04d}"


def _refuse_pdf_without_docling(kind: str) -> None:
    raise ProducerUnavailable(
        f"cannot produce a sidecar for a {kind} here: docling is not installed, and without it "
        "there is no way to tell a line lifted from the text layer from one a recogniser "
        "produced. Install it with: " + _INSTALL_DOCLING + ". Refusing rather than rasterising "
        "blind — a sidecar with invented geometry fails at the point of citation, which is much "
        "later and much quieter than failing here.")


def produce(data: bytes, *, document_id: str, media_type: Optional[str] = None,
            route: str = "auto", filename: Optional[str] = None,
            engine: Optional[OcrEngine] = None,
            engine_descriptor: Optional[EngineDescriptor] = None,
            extract: Optional[Callable[..., Any]] = None,
            deskew: bool = False, language: Optional[str] = None,
            produced_at: Optional[str] = None) -> tuple[dict[str, Any], ...]:
    """Document bytes → one sidecar per page, ready to write beside an ``INDEX.csv``.

    ``route``:
      * ``"docling"``    — whole-document extraction; the only route that can distinguish a
        text-layer line from a recognised one. Requires the ``docling`` extra, or an injected
        ``extract`` callable with the same signature (which is how the tests run without it).
      * ``"raster_ocr"`` — one image through the ``ocr_engines`` chain. Images only: every line in
        a raster IS a recognition, so the distinction the docling route makes does not arise.
      * ``"auto"``       — docling where it is usable, otherwise ``raster_ocr`` for an image and a
        refusal for a PDF. The route that ran is recorded in the sidecar; it is not left to be
        inferred from what happens to be installed.
    """
    if not data:
        raise SidecarProducerError("sidecar_producer: empty input")
    sniffed = docling_extract.sniff(data)
    if sniffed is None:
        raise SidecarProducerError(
            "sidecar_producer: not a PDF or a supported image (magic bytes). The declared media "
            "type is not trusted on its own.")
    if media_type is not None and docling_extract.is_supported(media_type):
        declared = media_type.split(";")[0].strip().lower()
        if (declared == "application/pdf") != (sniffed == "application/pdf"):
            raise SidecarProducerError(
                f"sidecar_producer: declared {declared!r} but the bytes are {sniffed!r}")
    kind = "pdf" if sniffed == "application/pdf" else "image"
    source_sha256 = hashlib.sha256(data).hexdigest()

    if route not in {"auto", "docling", "raster_ocr"}:
        raise SidecarProducerError(
            f"unknown route {route!r}; known: 'auto', 'docling', 'raster_ocr'")
    docling_usable = extract is not None or docling_extract.available()
    if route == "auto":
        route = "docling" if docling_usable else "raster_ocr"
    if route == "docling" and not docling_usable:
        _refuse_pdf_without_docling(kind)
    if route == "raster_ocr" and kind == "pdf":
        _refuse_pdf_without_docling("PDF")

    if route == "docling":
        return _produce_docling(
            data, document_id=document_id, media_type=sniffed, kind=kind, filename=filename,
            source_sha256=source_sha256, extract=extract, descriptor=engine_descriptor,
            language=language, produced_at=produced_at)
    return _produce_raster(
        data, document_id=document_id, media_type=sniffed, filename=filename,
        source_sha256=source_sha256, engine=engine, descriptor=engine_descriptor,
        deskew=deskew, language=language, produced_at=produced_at)


def _produce_docling(data: bytes, *, document_id: str, media_type: str, kind: str,
                     filename: Optional[str], source_sha256: str,
                     extract: Optional[Callable[..., Any]],
                     descriptor: Optional[EngineDescriptor], language: Optional[str],
                     produced_at: Optional[str]) -> tuple[dict[str, Any], ...]:
    run = extract or docling_extract.extract
    try:
        extracted = run(data, source_id=document_id, media_type=media_type)
    except docling_extract.DoclingUnavailable as exc:
        # The engine would not start (no model cache, no wheel). That is an unavailable route, not
        # a bad file, and the two deserve different answers.
        raise ProducerUnavailable(
            f"the extraction engine could not be started here: {exc}. Install it with: "
            + _INSTALL_DOCLING) from exc
    engine = descriptor or docling_descriptor()
    return tuple(
        build_sidecar(page, image_id=_image_id(document_id, page.page), document_id=document_id,
                      engine=engine, route="docling", filename=filename,
                      source_media_type=media_type, source_sha256=source_sha256,
                      language=language, produced_at=produced_at)
        for page in pages_from_docling(extracted, input_kind=kind))


def _produce_raster(data: bytes, *, document_id: str, media_type: str, filename: Optional[str],
                    source_sha256: str, engine: Optional[OcrEngine],
                    descriptor: Optional[EngineDescriptor], deskew: bool,
                    language: Optional[str],
                    produced_at: Optional[str]) -> tuple[dict[str, Any], ...]:
    try:
        eng = engine or select_engine()
    except OcrUnavailable as exc:
        raise ProducerUnavailable(
            f"no OCR engine is available here, so there is no honest geometry to write: {exc} "
            "Alternatively install docling for the whole-document route: " + _INSTALL_DOCLING
        ) from exc
    # Deskew is OFF by default here, and that is a citation decision rather than a quality one:
    # deskewing rotates the raster, so the boxes would address a page that exists only inside this
    # process and can never be shown to anyone. Left flat, they address the decoded image itself.
    pre = _imageprep.preprocess(data, do_deskew=deskew, do_binarise=eng.wants_binarise)
    result = eng.recognize(pre.raster)
    pages = pages_from_ocr_result(
        result, page=1,
        space="deskewed_page_raster" if deskew else "decoded_image_pixels")
    preprocess = {
        "deskew_deg": _num(pre.skew_deg) if deskew else 0,
        "binarised": bool(eng.wants_binarise),
        "note": ("boxes address the raster AFTER these steps. Deskew is off by default because a "
                 "rotated raster is a frame nobody else holds; binarisation does not move "
                 "geometry."),
    }
    return tuple(
        build_sidecar(page, image_id=_image_id(document_id, page.page), document_id=document_id,
                      engine=descriptor or ocr_chain_descriptor(eng), route="raster_ocr",
                      filename=filename, source_media_type=media_type,
                      source_sha256=source_sha256, language=language, preprocess=preprocess,
                      produced_at=produced_at)
        for page in pages)


# =======================================================================================
# writing a consumable set
# =======================================================================================

#: The columns `SidecarReader` actually reads (`sidecar_path`, `ocr_block_count`) plus the ones
#: that identify what was produced. The government pack's own index adds domain columns
#: (`inspection_id`, `parcel_id`, …) that come from the estate, not from an extraction, so they are
#: not invented here.
INDEX_COLUMNS = ("image_id", "sidecar_path", "document_id", "page", "engine",
                 "ocr_block_count", "text_layer_block_count", "mean_confidence")


def index_rows(sidecars: Sequence[dict[str, Any]], *,
               subdir: str = "sidecars") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for sidecar in sidecars:
        ocr = sidecar["ocr"]
        mean = ocr.get("mean_confidence")
        rows.append({
            "image_id": str(sidecar["image_id"]),
            "sidecar_path": f"{subdir}/{sidecar['image_id']}.json",
            "document_id": str(sidecar["document_id"]),
            "page": str(ocr["page"]),
            "engine": str(ocr["engine"]),
            "ocr_block_count": str(len(ocr["blocks"])),
            "text_layer_block_count": str(len(sidecar["text_layer"]["blocks"])),
            # Empty, not "0.0": a page with no recognised line has no mean confidence, and zero is
            # a measurement.
            "mean_confidence": "" if mean is None else f"{float(mean):.3f}",
        })
    return rows


def write_index(rows: Sequence[dict[str, str]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(INDEX_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return path


def write_sidecars(sidecars: Sequence[dict[str, Any]], out_dir: Path, *,
                   subdir: str = "sidecars", index_name: str = "INDEX.csv") -> list[Path]:
    """Write the set the ``json_sidecar`` reader expects: ``INDEX.csv`` beside a ``sidecars/`` dir.

    The reader resolves each ``sidecar_path`` relative to the index's own directory, so the two are
    written together or not at all — an index naming files that are not there is a source whose
    contract and bytes disagree, which is the failure it refuses at.
    """
    out_dir = Path(out_dir)
    target = out_dir / subdir
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sidecar in sidecars:
        path = target / f"{sidecar['image_id']}.json"
        path.write_text(serialise(sidecar), encoding="utf-8", newline="")
        written.append(path)
    written.append(write_index(index_rows(sidecars, subdir=subdir), out_dir / index_name))
    return written
