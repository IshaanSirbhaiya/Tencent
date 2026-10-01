"""Review **R-005** (`sources/document.py`, `capture.py`): one test per finding, each failing
without the fix.

The review is `wiki/40-reviews/raw/phase3-adapters-document.md`, triaged in
`wiki/40-reviews/R-005-adapter-fleet-triage.md`. Dispositions — what was fixed, what was partially
fixed and why, and what was deferred — are in `wiki/40-reviews/R-007-document-dispositions.md`.
Findings are numbered as the review numbers them; finding 3 ("untrusted source values can decide
access") is not re-tested here because R-005's triage already marks it CLOSED by a centralised
compile-time check in `PolicyEngine._compile`, and finding 9 is recorded as deferred in the
dispositions rather than fixed.

Fixtures below are a smaller, function-scoped echo of `test_document_adapter.py`'s — this file
does not import from that module (importing a sibling *test* module is fragile), so the handful of
fixtures it actually needs (an engine, a `Principal`, a `CaptureStore`, a `read()` helper) are
rebuilt here directly from `government_estate.py`, which both files are free to share.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from databridge_core import GroundedValue  # noqa: E402

import government_estate as gov                                                    # noqa: E402
from databridge_adapter import (                                                   # noqa: E402
    Budget,
    CapabilityRequest,
    PolicyEngine,
    Principal,
    PrincipalBinding,
    Projection,
    Verdict,
    load_purpose_bindings,
    load_role_model,
    purpose_vocabulary,
    use_purposes,
)
from databridge_adapter.capture import CaptureCorrupt, CaptureStore                # noqa: E402
from databridge_adapter.policy import Admission                                    # noqa: E402
from databridge_adapter.sources.document import (                                  # noqa: E402
    DocumentCore,
    DocumentSourceError,
    _block_bounds,
    csv_rows_with_spans,
)

PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"
_GOV_VOCABULARY = purpose_vocabulary(("government",), PACKS_ROOT)


@pytest.fixture
def gov_purposes():
    with use_purposes(_GOV_VOCABULARY) as vocab:
        yield vocab


@pytest.fixture
def gov_policy_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("gov-policy-r005")
    return {
        "with_exception": gov.write_roles(root / "roles.yaml"),
        "bindings": gov.write_purpose_bindings(root / "purpose_bindings.yaml"),
    }


@pytest.fixture
def gov_engine(gov_policy_files) -> PolicyEngine:
    role_model = load_role_model(gov_policy_files["with_exception"])
    binding = load_purpose_bindings("government", path=gov_policy_files["bindings"],
                                    known_estate_purposes=role_model.estate_purposes)
    return PolicyEngine(
        role_model=role_model, purpose_binding=binding,
        directory={
            "adapter@case_files": PrincipalBinding(
                roles=("adapter_service",), attributes=(("bound_source_id", "case_files"),)),
        },
        default_budget=Budget(max_rows=20_000))


@pytest.fixture
def captures(tmp_path_factory) -> CaptureStore:
    return CaptureStore(root=tmp_path_factory.mktemp("captures-r005"))


@pytest.fixture
def ingest(gov_purposes) -> Principal:
    return Principal(actor="adapter@case_files", on_behalf_of=None,
                     purpose="register.graph_build")


def read(core: DocumentCore, principal: Principal, entity: str, fields, key, order,
         *, limit: int = 50, **kw):
    req = CapabilityRequest(
        source_id=core.source_id, capability="document_read", principal=principal,
        projection=Projection(entity=entity, fields=fields, key=key),
        order_by=order, limit=limit, **kw)
    return req, core.resolve(req)


def _estate(tmp_path: Path, *sub_sources: str) -> Path:
    """A private copy of just the named sub-sources of the government pack, plus its sources.yaml.

    Cheaper than copying the whole ~9,000-file estate for a test that mutates one file.
    """
    root = tmp_path / "estate"
    root.mkdir()
    for name in sub_sources:
        shutil.copytree(gov.GOV_PACK / name, root / name)
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")
    return root


# =======================================================================================
# 1 (Critical) — index paths can escape the declared source root
# =======================================================================================


def test_finding_1_an_index_row_cannot_escape_the_declared_source_root(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """An INDEX row is content the estate's scanner wrote, not code — `..\\..\\secret.txt` or an
    absolute path in `relative_path` must not let the drop read a file outside itself."""
    root = _estate(tmp_path, "case_files")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET, OUTSIDE THE ESTATE", encoding="utf-8")

    index_path = root / "case_files" / "INDEX.csv"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    path_col = header.index("relative_path")
    evil = lines[1].split(",")
    evil[0] = "CF-EVIL-0001"
    evil[path_col] = "../../../../../../secret.txt"
    index_path.write_text("\n".join([lines[0], ",".join(evil)]), encoding="utf-8")

    core = DocumentCore(source_id="case_files", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match="outside"):
        read(core, ingest, "document", ("document_id", "content"), ("document_id",),
             ("document_id",), limit=10)
    # and the secret's bytes specifically were never captured (the index itself legitimately is)
    secret_digest = captures.digest_of(secret.read_bytes())
    assert not captures.exists(secret_digest)


# =======================================================================================
# 2 (Critical) — a cached capture silently turns an old read into "now"
# =======================================================================================


def test_finding_2_a_second_resolve_on_the_same_core_sees_the_live_file_not_a_stale_cache(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """T3 has no past to return to: a second `resolve()` on the SAME `DocumentCore`, after the
    cited file changed on disk, must see the CHANGE — not the first read's bytes stamped with a
    fresh `now()`."""
    root = _estate(tmp_path, "case_files")
    core = DocumentCore(source_id="case_files", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    _req, first = read(core, ingest, "document", ("document_id", "content"), ("document_id",),
                       ("document_id",), limit=1)
    cell = first.rows.rows[0][1]
    assert isinstance(cell, GroundedValue)
    original_digest = cell.pointer.capture_digest

    page = core._reader.live_path_of(cell.pointer.document_id)
    page.write_text(page.read_text(encoding="utf-8") + "\n[EDITED AFTER FIRST READ]\n",
                    encoding="utf-8")

    _req2, second = read(core, ingest, "document", ("document_id", "content"), ("document_id",),
                         ("document_id",), limit=1)
    cell2 = second.rows.rows[0][1]
    assert isinstance(cell2, GroundedValue)
    assert cell2.pointer.capture_digest != original_digest, (
        "the second resolve() on the same Core returned the FIRST read's capture even though the "
        "file changed underneath it — a stale 'now'")
    assert "[EDITED AFTER FIRST READ]" in cell2.value


# =======================================================================================
# 4 (Critical) — malformed OCR text is fabricated as the empty string
# =======================================================================================


def test_finding_4_a_missing_ocr_text_is_refused_not_fabricated_as_empty_string(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """A block whose `text` is absent, `null`, or non-string is a malformed sidecar. `''` is
    itself a value (invariant 4) and must never stand in for one the sidecar never stated."""
    root = _estate(tmp_path, "inspection_photos")
    sidecars = sorted((root / "inspection_photos" / "sidecars").glob("*.json"))
    target = sidecars[0]
    doc = json.loads(target.read_text(encoding="utf-8"))
    blocks = doc["ocr"]["blocks"]
    assert blocks
    del blocks[0]["text"]
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match="text"):
        read(core, ingest, "ocr_block", ("image_id", "line", "text", "confidence"), (),
             ("line",), limit=500)


# =======================================================================================
# 5 (Critical) — OCR locator fields are silently invented or changed
# =======================================================================================


@pytest.mark.parametrize("mutate,match", [
    (lambda doc: doc["ocr"].__setitem__("page", None), "page"),
    (lambda doc: doc.__setitem__("image_id", ""), "image_id"),
    (lambda doc: doc["image"].__setitem__("width", 0), "width"),
])
def test_finding_5_a_missing_locator_field_is_refused_not_invented(
        tmp_path, gov_engine, captures, gov_purposes, ingest, mutate, match):
    """`page`, `image_id` and `image.width`/`height` all end up on the emitted pointer's locator.
    A missing one used to be silently invented (`page` defaulted to 1, `image_id` to the
    filename, width/height to 0 which switched bounds-checking off entirely) instead of refusing.
    """
    root = _estate(tmp_path, "inspection_photos")
    sidecars = sorted((root / "inspection_photos" / "sidecars").glob("*.json"))
    target = sidecars[0]
    doc = json.loads(target.read_text(encoding="utf-8"))
    mutate(doc)
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match=match):
        read(core, ingest, "ocr_block", ("image_id", "line", "text", "confidence"), (),
             ("line",), limit=500)


# =======================================================================================
# 6 (Critical) — present OCR text becomes `Absent` when its locator is invalid
# =======================================================================================


def test_finding_6_text_present_with_an_out_of_bounds_bbox_is_refused_not_absent(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """The text IS in the sidecar — only its bbox lies outside the declared page. The old code
    silently emitted the row without a `text` cell, which `_emit_row` turns into `Absent`, exactly
    misrepresenting "present but unlocatable" as "the page does not say" (invariant 4)."""
    root = _estate(tmp_path, "inspection_photos")
    sidecars = sorted((root / "inspection_photos" / "sidecars").glob("*.json"))
    target = sidecars[0]
    doc = json.loads(target.read_text(encoding="utf-8"))
    block = doc["ocr"]["blocks"][0]
    width, height = doc["image"]["width"], doc["image"]["height"]
    block["bbox"] = [width + 100, height + 100, 50, 50]     # entirely off the declared page
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match="outside the declared"):
        read(core, ingest, "ocr_block", ("image_id", "line", "text", "confidence"), (),
             ("line",), limit=500)


# =======================================================================================
# 7 (Critical) — arbitrary derived pointers can receive a positive verdict
# =======================================================================================


def test_finding_7_verify_replays_the_derivation_not_just_the_parents(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """`verify()`'s `derived` branch used to combine the PARENTS' verdicts and never touch
    `pointer.derivation` at all — an unknown, malformed or wrong-arity derivation sailed through to
    a positive verdict. 'Provenance structurally present' is not 'derivation verified'."""
    root = _estate(tmp_path, "legacy_records")
    core = DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    _req, result = read(core, ingest, "legacy_master", ("lm_legacy_key", "lm_surname"), (),
                        ("lm_legacy_key",), limit=8000)
    surname_cell = next(c for row in result.rows.rows for c in row
                        if isinstance(c, GroundedValue) and c.pointer.kind == "derived")
    assert surname_cell.pointer.kind == "derived"
    assert core.verify(surname_cell.pointer).is_positive     # the honest one still verifies

    poisoned = surname_cell.pointer.model_copy(
        update={"derivation": "not_a_registered_rule@v1()"})
    verdict, note = core.verify_with_note(poisoned)
    assert verdict is Verdict.UNVERIFIABLE
    assert "derivation" in note

    wrong_arity = surname_cell.pointer.model_copy(
        update={"derivation": "fixed_width_trim@v1(width=999)"})
    verdict2, note2 = core.verify_with_note(wrong_arity)
    assert verdict2 is Verdict.UNVERIFIABLE
    assert "derivation" in note2


# =======================================================================================
# 8 (Critical) — mutable metadata can make a capture pointer name different bytes
# =======================================================================================


def test_finding_8_a_meta_json_naming_a_different_digest_is_refused(tmp_path):
    """`meta.json` is content on disk like anything else. If it is corrupted or tampered so its
    own `digest` field names something other than the digest this store was asked for, the store
    must refuse rather than mint a `Capture` that a pointer could cite the wrong bytes under."""
    store = CaptureStore(root=tmp_path / "captures")
    capture = store.put(b"honest bytes", media_type="text/plain")
    meta_path = store._meta(capture.digest)
    meta_path.chmod(0o600)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["digest"] = "0" * 64                          # a different, well-formed digest
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CaptureCorrupt, match="digest"):
        store.describe(capture.digest)

    # and put()'s "already stored" branch must not silently trust the tampered file either
    with pytest.raises(CaptureCorrupt, match="digest"):
        store.put(b"honest bytes", media_type="text/plain")


# =======================================================================================
# 10 (High) — denied snapshot and plan requests are not checked
# =======================================================================================


def test_finding_10_snapshot_and_plan_verify_the_same_admission_resolve_does(
        monkeypatch, tmp_path, gov_engine, captures, gov_purposes, ingest):
    """`resolve()` re-verifies its compiled decision table with `.check()` before using it.
    `snapshot()` and `plan()` fetched the same `Admission` and never checked it."""
    root = _estate(tmp_path, "case_files")
    core = DocumentCore(source_id="case_files", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    calls: list[object] = []
    original = Admission.check

    def spy(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(Admission, "check", spy)
    req = CapabilityRequest(
        source_id="case_files", capability="document_read", principal=ingest,
        projection=Projection(entity="document", fields=("document_id",), key=("document_id",)),
        order_by=("document_id",), limit=1)

    core.snapshot(req)
    assert calls, "snapshot() must verify its Admission the same way resolve() does"
    calls.clear()
    core.plan(req)
    assert calls, "plan() must verify its Admission the same way resolve() does"


# =======================================================================================
# 11 (High, partial) — an unlocatable OCR block searched the rest of the document
# =======================================================================================


def test_finding_11_block_bounds_refuses_rather_than_searching_the_whole_document():
    """When an OCR block's own text literal cannot be found from its expected position onward,
    the old fallback searched from there to the END of the sidecar for `line`/`confidence` —
    letting a LATER block's fields vouch for this one. It must refuse instead."""
    text = '{"ocr": {"blocks": [{"line": 0, "text": "actually present"}]}}'
    with pytest.raises(DocumentSourceError, match="one block's fields vouch for another"):
        _block_bounds(text, "this text is not anywhere in the document", 0)


# =======================================================================================
# 12 (High, partial) — resolution mode applied `limit` before searching for the pointer
# =======================================================================================


def test_finding_12_resolution_mode_finds_a_pointer_regardless_of_a_small_limit(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """A pointer is a LOCATOR; re-resolving it must not depend on where its row falls in the
    reader's enumeration. `legacy_records` has thousands of records — resolving a pointer to one
    near the end, under a `limit` far smaller than the estate, must still find it."""
    root = _estate(tmp_path, "legacy_records")
    core = DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    # `lm_dob_cymd` is `PIC 9(08)` — always a `doc_span` cell (numeric fields are cited verbatim,
    # never skipped as `Absent` the way an all-pad `PIC X` field is, and never `derived`), so every
    # row's pointer for it carries a real `row_id` to resolve against.
    _req, browse = read(core, ingest, "legacy_master", ("lm_legacy_key", "lm_dob_cymd"), (),
                        ("lm_legacy_key",), limit=8000)
    assert len(browse.rows.rows) > 50, "the fixture is expected to have more than 50 records"
    late_row = browse.rows.rows[-1]
    dob_cell = next(c for c in late_row if isinstance(c, GroundedValue)
                    and c.pointer.column == "lm_dob_cymd")
    pointer = dob_cell.pointer

    again = CapabilityRequest(
        source_id="legacy_records", capability="document_read", principal=ingest,
        projection=Projection(entity="legacy_master", fields=("lm_legacy_key", "lm_dob_cymd"),
                              key=()),
        order_by=("lm_legacy_key",), resolve=(pointer,), limit=1)
    result = core.resolve(again)
    assert len(result.rows.rows) == 1, (
        "resolving a pointer whose row falls after the reader's first `limit` rows must still "
        "find it — a locator does not depend on where the browse read happened to stop")


# =======================================================================================
# 13 (High, partial) — sidecar index evades capture; photo schema stops at the first sidecar
# =======================================================================================


def test_finding_13a_the_sidecar_index_is_captured_before_it_is_parsed(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """`SidecarReader`'s own INDEX.csv decides which sidecar files a read enumerates — the same
    job `TextDropReader`'s captured index does — and must be captured the same way."""
    root = _estate(tmp_path, "inspection_photos")
    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    read(core, ingest, "photo", ("image_id",), ("image_id",), ("image_id",), limit=5)
    index_bytes = (root / "inspection_photos" / "INDEX.csv").read_bytes()
    assert captures.exists(captures.digest_of(index_bytes)), (
        "SidecarReader's INDEX.csv must be captured, the same as TextDropReader's is")


def test_finding_13b_the_photo_schema_is_the_union_of_every_sidecar_not_just_the_first(
        tmp_path, gov_engine, captures, gov_purposes, ingest):
    """A field present in a LATER sidecar but absent from the first must still be part of the
    advertised `photo` schema — a document drop's schema is discovered as a union, and
    `TextDropReader._discover_labels` already does this for its own drop."""
    root = _estate(tmp_path, "inspection_photos")
    sidecars = sorted((root / "inspection_photos" / "sidecars").glob("*.json"))
    assert len(sidecars) >= 2
    first_doc = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert "only_in_the_second_sidecar" not in first_doc
    second_doc = json.loads(sidecars[1].read_text(encoding="utf-8"))
    second_doc["only_in_the_second_sidecar"] = "a fact the first sidecar never had"
    sidecars[1].write_text(json.dumps(second_doc, indent=2), encoding="utf-8")

    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    fields = {f.name for f in core.describe().entity("photo").fields}
    assert "only_in_the_second_sidecar" in fields


# =======================================================================================
# 14 (High) — capture writes and shredding are not crash- or concurrency-atomic
# =======================================================================================


def test_finding_14_put_repairs_a_blob_left_without_metadata(tmp_path):
    """A crash between installing the blob and writing its metadata used to leave a capture that
    no LATER `put()` for the same content could ever complete — it called `_read_meta()` on a
    metadata file that did not exist. Re-`put()`ing the same content now repairs it."""
    store = CaptureStore(root=tmp_path / "captures")
    content = b"content whose metadata write crashed"
    digest = store.digest_of(content)
    # Simulate the crash: install the blob exactly as `put()` would, but never write meta.json.
    blob = store._blob(digest)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)

    with pytest.raises(CaptureCorrupt, match="metadata is missing"):
        store.describe(digest)

    repaired = store.put(content, media_type="text/plain", source_id="repair-test")
    assert repaired.digest == digest
    assert store.describe(digest).digest == digest


def test_finding_14_shred_commits_the_tombstone_before_deleting_the_blob(tmp_path):
    """A crash between deleting the bytes and writing the tombstone used to lose the erasure
    authority and reason entirely — a bare missing file, indistinguishable from `CaptureNotFound`.
    The tombstone is now written first; a retried `shred()` returns the ORIGINAL tombstone rather
    than overwriting who authorised the erasure."""
    store = CaptureStore(root=tmp_path / "captures")
    capture = store.put(b"personal data", media_type="text/plain")
    first = store.shred(capture.digest, authority="dpo@example.com", reason="erasure R-1")
    assert not store._blob(capture.digest).is_file()

    # A retry (e.g. a caller that did not see the first response) must not rewrite the tombstone.
    second = store.shred(capture.digest, authority="someone-else@example.com", reason="different")
    assert second == first


# =======================================================================================
# 15 (Medium) — CSV parsing silently accepts forms it claims to refuse
# =======================================================================================


def test_finding_15_a_doubled_quote_is_refused_not_silently_truncated():
    """`"a""b"` used to be silently truncated to `a` at the first quote — the doubled-quote
    refusal this scanner promises could never fire, because a value sliced that way can never
    contain a bare `"`."""
    with pytest.raises(DocumentSourceError, match="doubled quote"):
        csv_rows_with_spans('h1,h2\n"a""b",x\n')


def test_finding_15_an_unterminated_quoted_field_is_refused_not_silently_accepted():
    with pytest.raises(DocumentSourceError, match="closing quote"):
        csv_rows_with_spans('h1,h2\n"unterminated,x\n')


def test_finding_15_a_blank_line_before_the_header_does_not_swallow_it():
    """A blank line ahead of the header used to make the REAL header get read as the first data
    row instead — silently narrowing the columns and turning column names into row content."""
    header, rows = csv_rows_with_spans('\ndocument_id,relative_path\nCF-1,2024/CF-1.txt\n')
    assert header == ["document_id", "relative_path"]
    assert len(rows) == 1
    assert [v for v, _s, _e in rows[0]] == ["CF-1", "2024/CF-1.txt"]
