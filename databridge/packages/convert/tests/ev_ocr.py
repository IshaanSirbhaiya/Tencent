"""EV-OCR scorer — OCR field accuracy + bbox provenance (09_FINAL_PRD §23). Gate F1 ≥0.75.

Lives beside the OCR fixtures (mirrors EV-XL living in ``convert/tests``) so the fixture
import resolves without a cross-package path. ``test_ev_ocr.py`` asserts the gates.

Fixtures: the 3 invoice scans (clean/skewed/noisy). Each is OCR'd (C13) → DocumentRecord, then
a deterministic ``Label: value`` parser pulls field values (a precursor to C15's §11.1
pre-extraction — kept local so EV-OCR has no C15 dependency).

Metrics (§23): field **F1** (exact value match, gate ≥0.75) · **bbox presence** (every
extracted field's block carries a RESOLVABLE ``ocr_bbox`` pointer, gate 100%) · **confusion
trap** (O/0 + 1/l/I fixture, MEASURED not gated; the §23 canary).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from databridge_convert.adapters.source import image_ocr
from fixtures.ocr.build import build_confusion, scan_fixtures

F1_GATE = 0.75
BBOX_PRESENCE_GATE = 1.0
# a field's bbox must localise (not span the whole page) — a whole-page box for every field
# would otherwise pass the presence gate vacuously (HIGH-2). C15 grounding requires real bboxes.
BBOX_MAX_AREA_FRAC = 0.5

# Confusion-tolerant comparison per §11.4 — import the SINGLE canonical map from the shipped
# grounding wall (C15) so EV-OCR's tolerant compare and grounding are provably identical.
from databridge_convert.extract.grounding import CONFUSION_MAP  # noqa: E402
_CONFUSE = str.maketrans(CONFUSION_MAP)


@dataclass
class FieldScore:
    fixture: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    bbox_present: int = 0
    bbox_total: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_fields(doc) -> list[tuple[str, int]]:
    """Deterministic ``Label: value`` parse → [(value, block_index)] (split on first colon)."""
    out: list[tuple[str, int]] = []
    for i, b in enumerate(doc.blocks):
        if ":" in b.text:
            value = _norm(b.text.split(":", 1)[1])
            if value:
                out.append((value, i))
    return out


def score_fixture(fx, *, prefer: str | None = None) -> FieldScore:
    doc, _media, raster = image_ocr.read_with_artifacts(fx.png, source_id=fx.name, prefer=prefer)
    parsed = parse_fields(doc)
    remaining = [_norm(v) for v in fx.gold.values()]
    page_area = float(raster.shape[0] * raster.shape[1]) or 1.0
    sc = FieldScore(fixture=fx.name)
    for value, blk in parsed:
        sc.bbox_total += 1
        ptr = doc.block_provenance.get(blk)
        if ptr is not None and ptr.kind == "ocr_bbox" and all(
                getattr(ptr, f) is not None
                for f in ("media_id", "page", "x0", "y0", "x1", "y1", "ocr_conf")):
            try:
                crop = image_ocr.resolve_bbox(ptr, raster)       # must RESOLVE to real pixels
                box_area = (ptr.x1 - ptr.x0) * (ptr.y1 - ptr.y0)
                # …AND LOCALISE: a field box that covers most of the page isn't provenance
                # (HIGH-2 — defeats the "whole-page box passes vacuously" failure mode).
                if crop.size > 0 and 0 < box_area <= BBOX_MAX_AREA_FRAC * page_area:
                    sc.bbox_present += 1
            except Exception:
                pass
        if value in remaining:
            sc.tp += 1
            remaining.remove(value)
        else:
            sc.fp += 1
    sc.fn = len(remaining)
    return sc


def run_ev_ocr(*, prefer: str | None = None) -> dict:
    scans = [score_fixture(fx, prefer=prefer) for fx in scan_fixtures()]
    tp = sum(s.tp for s in scans); fp = sum(s.fp for s in scans); fn = sum(s.fn for s in scans)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    bp = sum(s.bbox_present for s in scans); bt = sum(s.bbox_total for s in scans)
    bbox_rate = bp / bt if bt else 0.0

    # confusion trap (measured, not gated; the §23 canary)
    conf_fx = build_confusion()
    cdoc, _m, _ras = image_ocr.read_with_artifacts(conf_fx.png, source_id="confusion", prefer=prefer)
    cparsed = {v for v, _ in parse_fields(cdoc)}
    gold = [_norm(v) for v in conf_fx.gold.values()]
    exact = sum(1 for g in gold if g in cparsed)
    tol = sum(1 for g in gold if g.translate(_CONFUSE) in {v.translate(_CONFUSE) for v in cparsed})

    return {
        "field_f1": f1, "precision": p, "recall": r,
        "f1_gate": F1_GATE, "f1_pass": f1 >= F1_GATE,
        "bbox_presence": bbox_rate, "bbox_gate": BBOX_PRESENCE_GATE,
        "bbox_pass": bbox_rate >= BBOX_PRESENCE_GATE,
        "per_fixture": {s.fixture: {"f1": round(s.f1, 3), "tp": s.tp, "fp": s.fp, "fn": s.fn}
                        for s in scans},
        "confusion_trap": {
            "exact_accuracy": exact / len(gold) if gold else 0.0,
            "confusion_tolerant_accuracy": tol / len(gold) if gold else 0.0,
            "n_fields": len(gold),
        },
    }
