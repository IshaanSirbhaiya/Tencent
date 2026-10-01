"""The sidecar producer — held to the *consumer's* contract, not to its own.

A producer that emits a nearly-compatible sidecar is worse than no producer, because it fails at
the point of citation rather than at the point of production: the file parses, the reader accepts
it, and a bounding box quietly frames the wrong words (or the block loses its text and nobody
notices). So most of what is asserted here is read off a **shipped fixture** —
``fixtures/packs/government/inspection_photos/sidecars/`` — and off the invariants
``SidecarReader`` relies on:

* ``coordinate_origin`` / ``bbox_format`` are the exact tokens the reader implements;
* the boxes lie inside the declared page, because a box outside loses its text at the consumer;
* the block key order survives ``_block_bounds``, which walks back to the nearest ``{`` and forward
  to the nearest ``}`` around the block's own text literal;
* the serialisation is byte-for-byte the fixtures' own, because the reader locates a block by
  searching for ``json.dumps(text)`` in the file.

Nothing here needs docling: the extraction is injected as a stub, which is also how the refusal
path is exercised. Two tests reach for the real docling and the real OCR chain and skip when they
are not installed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import pathlib
from types import SimpleNamespace

import numpy as np
import pytest

from databridge_convert.adapters.source import docling_extract, sidecar_producer as sp
from databridge_convert.adapters.source.docling_extract import TextRegion
from databridge_convert.adapters.source.ocr_engines import (OcrLine, OcrResult, OcrUnavailable,
                                                            select_engine)

REPO = pathlib.Path(__file__).resolve().parents[3]
FIXTURE_SIDECARS = REPO / "fixtures" / "packs" / "government" / "inspection_photos" / "sidecars"
FIXTURE_INDEX = FIXTURE_SIDECARS.parent / "INDEX.csv"


# --- a stub extraction: one page, mixed provenance --------------------------------
# Page 1 was rasterised and OCR'd; page 2 is a text layer. That mix is the case the whole module
# exists for, and it is the one a single-source fixture cannot produce.
PAGE_TEXT = "SITE NOTICE\nPERMIT No. AM-P-2011-743409\nOFFICER OFF-0034\nPage 2 heading\ntail line"


def _stub_extracted() -> SimpleNamespace:
    text = PAGE_TEXT
    spans = []
    cursor = 0
    for line in text.split("\n"):
        spans.append((cursor, cursor + len(line)))
        cursor += len(line) + 1
    regions = (
        TextRegion(char_start=spans[0][0], char_end=spans[0][1], page=1,
                   x0=120.0, y0=100.0, x1=494.0, y1=160.0, page_width=1700.0, page_height=2200.0,
                   from_ocr=True, ocr_conf=0.97, media_id="doc:p1"),
        TextRegion(char_start=spans[1][0], char_end=spans[1][1], page=1,
                   x0=121.0, y0=225.0, x1=823.0, y1=304.0, page_width=1700.0, page_height=2200.0,
                   from_ocr=True, ocr_conf=0.8791, media_id="doc:p1"),
        # Recognised, but the engine reported no confidence. Neither an ocr block nor a text-layer
        # line — see the assertions below.
        TextRegion(char_start=spans[2][0], char_end=spans[2][1], page=1,
                   x0=200.0, y0=400.0, x1=700.0, y1=460.0, page_width=1700.0, page_height=2200.0,
                   from_ocr=True, ocr_conf=None, media_id="doc:p1"),
        TextRegion(char_start=spans[3][0], char_end=spans[3][1], page=2,
                   x0=72.0, y0=90.0, x1=300.5, y1=104.25, page_width=595.0, page_height=842.0,
                   from_ocr=False, ocr_conf=None, media_id=None),
        TextRegion(char_start=spans[4][0], char_end=spans[4][1], page=2,
                   x0=72.0, y0=120.0, x1=250.0, y1=134.0, page_width=595.0, page_height=842.0,
                   from_ocr=False, ocr_conf=None, media_id=None),
    )
    return SimpleNamespace(text=text, regions=regions)


def _stub_extract(data, *, source_id, media_type=None):        # signature of docling_extract.extract
    return _stub_extracted()


PDF_BYTES = b"%PDF-1.4\n% a stub; the stub extractor never parses it\n%%EOF\n"


@pytest.fixture()
def produced() -> tuple[dict, ...]:
    return sp.produce(PDF_BYTES, document_id="chit_07.pdf", media_type="application/pdf",
                      route="docling", extract=_stub_extract)


@pytest.fixture(scope="module")
def fixture_sidecar() -> dict:
    path = sorted(FIXTURE_SIDECARS.glob("*.json"))[0]
    return json.loads(path.read_text(encoding="utf-8"))


# =======================================================================================
# byte-shape compatibility with the shipped fixtures
# =======================================================================================


def test_the_fixture_pack_is_where_this_contract_is_read_from():
    assert FIXTURE_SIDECARS.is_dir() and any(FIXTURE_SIDECARS.glob("*.json")), \
        "the shipped sidecars are the contract; without them these tests assert nothing"


def test_serialisation_reproduces_a_shipped_fixture_byte_for_byte():
    # If this drifts, every `text.find(json.dumps(block_text))` in the reader is a coin toss.
    for path in sorted(FIXTURE_SIDECARS.glob("*.json"))[:5]:
        raw = path.read_text(encoding="utf-8")
        assert sp.serialise(json.loads(raw)) == raw, f"{path.name}: serialisation differs"


def test_the_produced_ocr_section_carries_every_key_the_fixture_declares(produced,
                                                                        fixture_sidecar):
    mine = produced[0]["ocr"]
    for key in fixture_sidecar["ocr"]:
        assert key in mine, f"the fixture declares ocr.{key} and the producer omits it"
    assert mine["coordinate_origin"] == fixture_sidecar["ocr"]["coordinate_origin"]


def test_produced_blocks_have_exactly_the_fixture_block_keys(produced, fixture_sidecar):
    expected = list(fixture_sidecar["ocr"]["blocks"][0])
    for block in produced[0]["ocr"]["blocks"]:
        assert list(block) == expected, \
            "block key ORDER is load-bearing: the reader walks back to the nearest '{' and " \
            "forward to the nearest '}' around the block's text literal"


def test_bbox_format_matches_the_one_the_reader_implements(produced, fixture_sidecar):
    declared = fixture_sidecar["ocr"]["blocks"][0]["bbox_format"]
    assert declared == "xywh_pixels"
    for block in produced[0]["ocr"]["blocks"]:
        assert block["bbox_format"] == declared
        assert len(block["bbox"]) == 4 and isinstance(block["bbox"], list)


def test_top_level_scalars_all_precede_the_nested_objects(produced):
    # `SidecarReader` resolves a field by the FIRST occurrence of its key anywhere in the file. A
    # nested key shadowing a top-level one would hand the consumer a span inside the wrong object.
    keys = list(produced[0])
    first_container = next(i for i, k in enumerate(keys)
                           if isinstance(produced[0][k], (dict, list)))
    assert all(not isinstance(produced[0][k], (dict, list)) for k in keys[:first_container])
    assert all(isinstance(produced[0][k], (dict, list)) for k in keys[first_container:])


def test_page_appears_exactly_once_in_the_bytes(produced):
    # The reader looks up "page" over the whole file; two of them means the wrong one can win.
    text = sp.serialise(produced[0])
    assert text.count('"page":') == 1


def test_no_brace_between_a_blocks_opening_and_its_text_and_none_before_its_end(produced):
    """The exact walk `_block_bounds` performs, asserted on the produced bytes."""
    text = sp.serialise(produced[0])
    cursor = 0
    for block in produced[0]["ocr"]["blocks"]:
        anchor = text.find(json.dumps(block["text"]), cursor)
        assert anchor > 0, "the reader finds a block by searching for its text literal"
        opening = text.rfind("{", 0, anchor)
        closing = text.find("}", anchor)
        chunk = text[opening:closing + 1]
        assert json.loads(chunk) == block, \
            "the reader's brace walk must land on exactly this block, not on a neighbour"
        cursor = anchor + 1


def test_boxes_lie_inside_the_declared_page(produced):
    for sidecar in produced:
        width = float(sidecar["image"]["width"])
        height = float(sidecar["image"]["height"])
        for block in sidecar["ocr"]["blocks"] + sidecar["text_layer"]["blocks"]:
            x, y, w, h = (float(v) for v in block["bbox"])
            # The reader's own check. A block that fails it is emitted WITHOUT its text.
            assert x >= 0 and y >= 0 and x + w <= width and y + h <= height


def test_a_box_that_overruns_the_page_is_clamped_rather_than_dropped_at_the_consumer():
    line = sp.SidecarLine(line=0, text="edge", x0=90.0, y0=10.0, x1=100.004, y1=20.0,
                          from_ocr=True, ocr_conf=0.9)
    page = sp.SidecarPage(page=1, width=100.0, height=100.0, unit="pixels", space="page_raster",
                          lines=(line,))
    doc = sp.build_sidecar(page, image_id="i", document_id="d", route="stub",
                           engine=sp.EngineDescriptor("stub", "1"))
    x, y, w, h = doc["ocr"]["blocks"][0]["bbox"]
    assert x + w <= 100.0 and doc["ocr"]["blocks"][0]["text"] == "edge"


def test_a_zero_area_box_is_not_emitted_at_all():
    line = sp.SidecarLine(line=0, text="nothing", x0=10.0, y0=10.0, x1=10.0, y1=20.0,
                          from_ocr=True, ocr_conf=0.9)
    page = sp.SidecarPage(page=1, width=100.0, height=100.0, unit="pixels", space="page_raster",
                          lines=(line,))
    doc = sp.build_sidecar(page, image_id="i", document_id="d", route="stub",
                           engine=sp.EngineDescriptor("stub", "1"))
    assert doc["ocr"]["blocks"] == [] and doc["ocr"]["blocks_dropped_no_geometry"] == 1


# =======================================================================================
# the from_ocr honesty
# =======================================================================================


def test_every_ocr_block_carries_a_real_measured_confidence(produced):
    for sidecar in produced:
        for block in sidecar["ocr"]["blocks"]:
            assert isinstance(block["confidence"], float)
            assert 0.0 <= block["confidence"] <= 1.0


def test_text_layer_lines_are_not_ocr_blocks_and_carry_a_null_confidence(produced):
    page2 = next(s for s in produced if s["ocr"]["page"] == 2)
    assert page2["ocr"]["blocks"] == [], \
        "a PDF text layer had no recognition step; an ocr block there would be an invented number"
    assert page2["text_layer"]["blocks"], "the text and its geometry are kept, not discarded"
    for block in page2["text_layer"]["blocks"]:
        assert block["confidence"] is None
        assert block["source"] == "text_layer"


def test_a_page_with_no_recognised_line_has_no_mean_confidence(produced):
    page2 = next(s for s in produced if s["ocr"]["page"] == 2)
    assert page2["ocr"]["mean_confidence"] is None, "zero would be a measurement"


def test_a_recognised_line_whose_engine_reported_no_confidence_is_not_an_ocr_block(produced):
    page1 = next(s for s in produced if s["ocr"]["page"] == 1)
    texts = [b["text"] for b in page1["ocr"]["blocks"]]
    assert "OFFICER OFF-0034" not in texts, \
        "the format has no way to say 'recognised, confidence unknown' that a reader will not " \
        "read as a number"
    assert page1["ocr"]["blocks_dropped_no_geometry"] == 1


def test_text_layer_geometry_declares_its_own_units(produced):
    page2 = next(s for s in produced if s["ocr"]["page"] == 2)
    # PDF user space is points, not pixels. Saying "xywh_pixels" over point coordinates is exactly
    # the silent mis-citation this module refuses to make.
    assert page2["image"]["unit"] == "points"
    assert page2["text_layer"]["bbox_format"] == "xywh_points"
    page1 = next(s for s in produced if s["ocr"]["page"] == 1)
    assert page1["image"]["unit"] == "pixels"


def test_the_text_layer_section_comes_after_the_ocr_section_in_the_bytes(produced):
    text = sp.serialise(produced[0])
    assert text.index('"ocr"') < text.index('"text_layer"'), \
        "the reader scans forward for each ocr block's text; an earlier copy would mis-anchor it"


def test_block_text_is_the_slice_the_extraction_returned(produced):
    seen = [b["text"] for s in produced
            for b in s["ocr"]["blocks"] + s["text_layer"]["blocks"]]
    for text in seen:
        assert text in PAGE_TEXT


# =======================================================================================
# what recognised this
# =======================================================================================


def test_the_engine_and_its_version_are_recorded(produced):
    ocr = produced[0]["ocr"]
    assert ocr["engine"] == f"{ocr['engine_name']}/{ocr['engine_version']}"
    assert ocr["engine_name"] == "docling"
    assert ocr["route"] == "docling"


def test_determinism_is_declared_and_not_asserted_for_an_unpinned_engine(produced):
    claim = produced[0]["ocr"]["determinism"]
    assert claim["claim"] == "not_asserted" and claim["note"]


def test_an_engine_that_pins_its_models_may_assert_determinism():
    page = sp.SidecarPage(page=1, width=10, height=10, unit="pixels", space="page_raster",
                          lines=())
    doc = sp.build_sidecar(page, image_id="i", document_id="d", route="stub",
                           engine=sp.EngineDescriptor("pinned", "1.0", deterministic=True,
                                                      note="models are vendored"))
    assert doc["ocr"]["determinism"]["claim"] == "deterministic"


def test_an_unknown_version_is_reported_as_unknown_not_guessed():
    assert sp._installed_version("a-distribution-that-is-not-installed") == "unknown"


def test_the_same_bytes_produce_the_same_file_twice():
    a = sp.produce(PDF_BYTES, document_id="d", route="docling", extract=_stub_extract)
    b = sp.produce(PDF_BYTES, document_id="d", route="docling", extract=_stub_extract)
    assert [sp.serialise(s) for s in a] == [sp.serialise(s) for s in b]


def test_no_wall_clock_stamp_unless_the_caller_asks_for_one(produced):
    assert "produced_at" not in produced[0]
    stamped = sp.produce(PDF_BYTES, document_id="d", route="docling", extract=_stub_extract,
                         produced_at="2026-08-16T00:00:00+00:00")
    assert stamped[0]["produced_at"] == "2026-08-16T00:00:00+00:00"


def test_the_sidecar_does_not_claim_to_have_been_captured(produced):
    assert produced[0]["capture"]["captured_into"] is None
    assert produced[0]["image_present"] is False
    assert produced[0]["source_sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()


# =======================================================================================
# refusal — the degraded path
# =======================================================================================


def test_a_pdf_is_refused_when_docling_is_absent(monkeypatch):
    monkeypatch.setattr(docling_extract, "available", lambda: False)
    with pytest.raises(sp.ProducerUnavailable) as excinfo:
        sp.produce(PDF_BYTES, document_id="d", media_type="application/pdf")
    message = str(excinfo.value)
    assert 'pip install -e "packages/convert[docling]"' in message
    assert "text layer" in message, "the refusal must say WHY a raster route is not a substitute"


def test_an_explicit_docling_route_is_refused_the_same_way(monkeypatch):
    monkeypatch.setattr(docling_extract, "available", lambda: False)
    with pytest.raises(sp.ProducerUnavailable):
        sp.produce(_png_bytes(), document_id="d", route="docling")


def test_the_raster_route_refuses_a_pdf_outright():
    with pytest.raises(sp.ProducerUnavailable):
        sp.produce(PDF_BYTES, document_id="d", route="raster_ocr")


def test_an_image_is_refused_when_no_ocr_engine_is_available_either(monkeypatch):
    monkeypatch.setattr(docling_extract, "available", lambda: False)
    monkeypatch.setattr(sp, "select_engine",
                        lambda *a, **k: (_ for _ in ()).throw(OcrUnavailable("no models here")))
    with pytest.raises(sp.ProducerUnavailable) as excinfo:
        sp.produce(_png_bytes(), document_id="d")
    assert "no honest geometry" in str(excinfo.value)


def test_an_engine_that_will_not_start_is_an_unavailable_route_not_a_bad_file():
    def _explode(data, *, source_id, media_type=None):
        raise docling_extract.DoclingUnavailable("no model cache")
    with pytest.raises(sp.ProducerUnavailable):
        sp.produce(PDF_BYTES, document_id="d", route="docling", extract=_explode)


def test_bytes_that_are_not_a_document_are_refused():
    with pytest.raises(sp.SidecarProducerError):
        sp.produce(b"just some text, no magic", document_id="d")
    with pytest.raises(sp.SidecarProducerError):
        sp.produce(b"", document_id="d")


def test_a_declared_type_that_contradicts_the_bytes_is_refused():
    with pytest.raises(sp.SidecarProducerError):
        sp.produce(PDF_BYTES, document_id="d", media_type="image/png", extract=_stub_extract)


def test_an_unknown_route_is_refused():
    with pytest.raises(sp.SidecarProducerError):
        sp.produce(PDF_BYTES, document_id="d", route="magic")


# =======================================================================================
# the raster route
# =======================================================================================


def _png_bytes(width: int = 120, height: int = 60) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _StubEngine:
    """An engine with pinned, vendored models — so it may assert determinism, unlike the chain."""

    name = "stub-ocr"
    wants_binarise = False

    def available(self) -> bool:
        return True

    def recognize(self, image: np.ndarray) -> OcrResult:
        h, w = image.shape[:2]
        return OcrResult(
            lines=[OcrLine(text="PERMIT No. AM-P-2011-743409", x0=4, y0=4, x1=w - 4, y1=24,
                           conf=0.93, word_confs=(0.93,))],
            width=w, height=h, engine_name=self.name)


def test_the_raster_route_produces_a_sidecar_where_every_line_is_a_recognition():
    sidecars = sp.produce(_png_bytes(), document_id="scan.png", route="raster_ocr",
                          engine=_StubEngine())
    assert len(sidecars) == 1
    doc = sidecars[0]
    assert doc["ocr"]["route"] == "raster_ocr"
    assert doc["text_layer"]["blocks"] == [], "a raster has no text layer to lift lines from"
    assert doc["ocr"]["blocks"][0]["confidence"] == 0.93
    assert doc["ocr"]["preprocess"]["deskew_deg"] == 0
    assert doc["image"]["space"] == "decoded_image_pixels", \
        "deskew rotates the raster; the boxes would address a frame nobody else holds"


def test_pages_from_an_ocr_result_are_all_from_ocr():
    result = OcrResult(lines=[OcrLine("x", 0, 0, 5, 5, 0.5)], width=10, height=10,
                       engine_name="stub")
    page, = sp.pages_from_ocr_result(result)
    assert all(line.from_ocr and line.ocr_conf is not None for line in page.lines)


# =======================================================================================
# a set the reader can actually be pointed at
# =======================================================================================


def test_the_written_set_is_an_index_beside_the_sidecars_it_names(tmp_path, produced):
    written = sp.write_sidecars(produced, tmp_path)
    index = tmp_path / "INDEX.csv"
    assert index in written
    rows = list(csv.DictReader(io.StringIO(index.read_text(encoding="utf-8"))))
    assert len(rows) == len(produced)
    for row in rows:
        target = index.parent / row["sidecar_path"]
        assert target.is_file(), "an index naming a file that is not there is the failure the " \
                                 "reader refuses at"
        assert json.loads(target.read_text(encoding="utf-8"))["image_id"] == row["image_id"]


def test_the_index_columns_the_reader_needs_are_present(produced):
    header = sp.INDEX_COLUMNS
    shipped = FIXTURE_INDEX.read_text(encoding="utf-8").splitlines()[0].split(",")
    for needed in ("image_id", "sidecar_path", "ocr_block_count"):
        assert needed in header and needed in shipped


def test_a_page_with_no_recognised_line_reports_an_empty_mean_not_a_zero(produced):
    rows = sp.index_rows(produced)
    page2 = next(r for r in rows if r["page"] == "2")
    assert page2["mean_confidence"] == "" and page2["ocr_block_count"] == "0"


def test_image_ids_are_filesystem_safe_and_page_scoped():
    assert sp._image_id("a/b:c.pdf", 3) == "a_b_c.pdf-p0003"


# =======================================================================================
# the real engines, when this machine has them
# =======================================================================================


@pytest.mark.skipif(not docling_extract.available(), reason="docling not installed")
def test_a_real_docling_run_produces_a_consumable_sidecar():
    from fixtures.chits.build import ballot_font_path, text_to_pdf
    chits = REPO / "fixtures" / "hackathon" / "chits"
    font = ballot_font_path()
    if font is None:
        pytest.skip("no font on this machine carries the ballot glyphs the fixture needs")
    pdf = text_to_pdf((chits / "chit_07_mol_authorisation_pee226.txt").read_text(encoding="utf-8"),
                      font_path=font)
    sidecars = sp.produce(pdf, document_id="chit_07.pdf", media_type="application/pdf")
    assert sidecars
    for doc in sidecars:
        assert doc["ocr"]["coordinate_origin"] == "top_left"
        assert sp.serialise(doc)                      # no NaN / Infinity survives serialisation
        for block in doc["ocr"]["blocks"]:
            assert isinstance(block["confidence"], float)
        for block in doc["text_layer"]["blocks"]:
            assert block["confidence"] is None


def test_the_real_ocr_chain_produces_a_sidecar_when_an_engine_is_installed():
    try:
        select_engine()
    except OcrUnavailable:
        pytest.skip("no OCR engine on this machine (RapidOCR models / tesseract binary)")
    from fixtures.ocr.build import build_clean
    sidecars = sp.produce(build_clean().png, document_id="invoice.png", route="raster_ocr")
    doc = sidecars[0]
    assert doc["ocr"]["engine_version"] and doc["ocr"]["blocks"]
    assert doc["text_layer"]["blocks"] == []
