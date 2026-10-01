"""`DocumentCore` — the T3 source with no past to return to (`ADAPTER_AGENT_SPEC` §5, §11).

These tests run against the **government estate on disk**: 250 OCR'd case files with deliberate
scanner damage, 8,000 fixed-width legacy records behind a COBOL copybook, and 420 OCR sidecars for
photographs that do not ship. Nothing here is mocked. Two of the estate's ten planted prompt
injections live in this source's files, and `test_the_planted_injections_are_contained` reads them
out of the estate's own `INJECTIONS.md` rather than restating them, so a payload that changed in
the fixture cannot leave a containment test passing against a stale copy of itself.

The organising claim is the one T3 exists to make trouble for: **a citation into an unversioned
source is a promise the system cannot keep unless the bytes were captured.** So the capture tests
are not a section here — they are in every section.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from databridge_core import Absent, GroundedValue, SourcePointer, Trust, Withheld  # noqa: E402

import government_estate as gov                                                    # noqa: E402
from databridge_adapter import (                                                   # noqa: E402
    Budget,
    CannotPinPast,
    CapabilityNotAdvertised,
    CapabilityRequest,
    PolicyEngine,
    Principal,
    PrincipalBinding,
    Projection,
    RegistryTaintMap,
    RowRuleNotCompilable,
    UngroundedValue,
    UnorderedPointeredRead,
    UntrustedInControlFlow,
    Verdict,
    assert_grounded,
    load_purpose_bindings,
    load_role_model,
    purpose_vocabulary,
    screen_sinks,
    use_purposes,
)
from databridge_adapter.capture import CaptureStore                                # noqa: E402
from databridge_adapter.results import PointerResolution                           # noqa: E402
from databridge_adapter.shell import AdapterShell                                  # noqa: E402
from databridge_adapter.sources.document import (                                  # noqa: E402
    READERS,
    DocCell,
    DocRow,
    DocumentCore,
    DocumentSourceError,
    slug,
)

PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"

#: The estate's own document sources, with the entity and total order each is read under. Driven
#: from one table so a test that only ever ran against `case_files` cannot look like three.
SOURCES = (
    ("case_files", "document", ("document_id", "claimed_case_ref", "content"),
     ("document_id",), ("document_id",)),
    ("legacy_records", "legacy_master", ("lm_rec_type", "lm_legacy_key", "lm_surname"),
     (), ("lm_legacy_key",)),
    ("inspection_photos", "photo", ("image_id", "inspection_id", "application_id"),
     ("image_id",), ("image_id",)),
    ("inspection_photos", "ocr_block", ("image_id", "line", "text", "confidence"),
     (), ("line",)),
)


# =======================================================================================
# fixtures
# =======================================================================================


#: Built once — reading three packs' YAML per test is pure overhead — but ACTIVATED per test.
#: `use_purposes` swaps a process-wide vocabulary, and holding it open for a session would leak
#: the government binding into `test_purpose_is_pack_declared.py`, which deliberately exercises
#: the DEFAULT resolution path and would then see 'government' where the deployment says
#: 'healthcare'. A session-scoped context manager over global state is a fixture that decides
#: other modules' results.
_GOV_VOCABULARY = purpose_vocabulary(("government",), PACKS_ROOT)


@pytest.fixture
def gov_purposes():
    """The government pack's declared purpose vocabulary, bound explicitly and only for this test.

    Explicit rather than left to `packs/deployment.yaml`, for the reason `conftest.py` gives: a
    contract test that depended on which packs a deployment happens to enable would start failing
    the day someone enables another one, and the failure would say nothing about the contract.
    """
    with use_purposes(_GOV_VOCABULARY) as vocab:
        yield vocab


@pytest.fixture(scope="session")
def gov_policy_files(tmp_path_factory):
    """`government/roles.yaml` (v2) rendered into the shape `load_role_model` reads.

    See `government_estate.py` for the whole translation table. It is a translation and not a
    rewrite: every mapping is named there, and the one addition — the `adapter_service`/`ingest`
    free-text exception — is the estate's own `columns: "*"` ingest grant expressed in the v1
    vocabulary.
    """
    root = tmp_path_factory.mktemp("gov-policy")
    return {
        "with_exception": gov.write_roles(root / "roles.yaml"),
        "bindings": gov.write_purpose_bindings(root / "purpose_bindings.yaml"),
        "root": root,
    }


def _engine(roles_path: Path, bindings_path: Path) -> PolicyEngine:
    role_model = load_role_model(roles_path)
    binding = load_purpose_bindings("government", path=bindings_path,
                                    known_estate_purposes=role_model.estate_purposes)
    return PolicyEngine(
        role_model=role_model, purpose_binding=binding,
        directory={
            # The adapter service principal, bound to one source by its credential. The estate is
            # emphatic that `bound_source_id` is "a workload-identity attribute -- NEVER a request
            # field, a header, or a config value the adapter process can choose".
            "adapter@case_files": PrincipalBinding(
                roles=("adapter_service",), attributes=(("bound_source_id", "case_files"),)),
            "officer-0008": PrincipalBinding(
                roles=("permits_caseworker",), attributes=(("officer_ref", "OFF-0008"),)),
            "caseworker-12": PrincipalBinding(
                roles=("benefits_caseworker",), attributes=(("caseworker_ref", "CW-0012"),)),
            "audit-1": PrincipalBinding(roles=("auditor",)),
        },
        default_budget=Budget(max_rows=20_000))


@pytest.fixture(scope="session")
def gov_engine(gov_policy_files) -> PolicyEngine:
    return _engine(gov_policy_files["with_exception"], gov_policy_files["bindings"])


@pytest.fixture(scope="session")
def captures(tmp_path_factory) -> CaptureStore:
    return CaptureStore(root=tmp_path_factory.mktemp("captures"))


@pytest.fixture(scope="session")
def cores(gov_engine, captures):
    """One Core per document source, session-scoped. `describe()` scans a drop, so it is cached.

    Deliberately does NOT depend on `gov_purposes`: constructing a Core and describing a source
    touch no purpose vocabulary, and taking the dependency would force the process-wide binding to
    session scope. Only `Principal` construction needs it.
    """
    built = {}
    for source_id in ("case_files", "legacy_records", "inspection_photos"):
        built[source_id] = DocumentCore(
            source_id=source_id, root=gov.GOV_PACK, policy=gov_engine, capture_store=captures,
            sources_yaml=gov.GOV_SOURCES_YAML)
    return built


@pytest.fixture
def ingest(gov_purposes) -> Principal:
    """The build-time crawl: a machine principal, no human, and therefore no grant.

    `register.graph_build` is declared `subject_scoped: false`, so `grant_id` is correctly `None`
    — and a `None` grant here is a statement that this read is not subject-scoped, never a
    shortcut around one that is.
    """
    return Principal(actor="adapter@case_files", on_behalf_of=None,
                     purpose="register.graph_build")


def read(core: DocumentCore, principal: Principal, entity: str, fields, key, order,
         *, limit: int = 6, **kw):
    req = CapabilityRequest(
        source_id=core.source_id, capability="document_read", principal=principal,
        projection=Projection(entity=entity, fields=fields, key=key),
        order_by=order, limit=limit, **kw)
    return req, core.resolve(req)


@pytest.fixture
def mutable_case_files(tmp_path, gov_engine, captures, gov_purposes):
    """A private copy of the case-file drop, for the tests that re-scan or delete a page.

    A session-scoped estate that one test rewrites is a test suite whose results depend on
    collection order.
    """
    root = tmp_path / "estate"
    root.mkdir()
    shutil.copytree(gov.GOV_PACK / "case_files", root / "case_files")
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")
    store = CaptureStore(root=tmp_path / "captures")

    def build() -> DocumentCore:
        return DocumentCore(source_id="case_files", root=root, policy=gov_engine,
                            capture_store=store, sources_yaml=root / "sources.yaml")

    return root, store, build


# =======================================================================================
# 1. three readers, one adapter, chosen by the contract
# =======================================================================================


def test_the_reader_is_chosen_by_the_contract_never_by_the_filename(cores):
    """`sources.yaml: kind` selects the reader. Nothing looks at a file extension.

    A `.md` and a `.txt` in the same drop are the same kind of artefact because the CONTRACT says
    the source is a document drop, so a scanner that started writing a third extension tomorrow
    would not silently change how anything is read.
    """
    assert set(READERS) == {"document_drop", "fixed_width", "json_sidecar"}
    for source_id, core in cores.items():
        declared = gov.contract_entry(source_id)["kind"]
        assert type(core._reader) is READERS[declared]
        assert core._reader.kind == declared


def test_a_source_whose_kind_is_undeclared_is_refused_not_guessed_at(gov_engine, captures,
                                                                     gov_purposes):
    """An undeclared `kind` is a refusal at construction — never a reader picked by guessing.

    A wrong guess does not fail; it produces well-formed citations to the wrong bytes, which is
    the one outcome worse than an error.
    """
    with pytest.raises(DocumentSourceError, match="kind"):
        DocumentCore(source_id="correspondence", root=gov.GOV_PACK, policy=gov_engine,
                     capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)


def test_a_non_t3_source_is_refused_by_the_t3_core(gov_engine, captures, gov_purposes):
    """Reading a T2 source through the tier that promises least would discard what it can pin."""
    with pytest.raises(DocumentSourceError, match="T3"):
        DocumentCore(source_id="gis_parcels", root=gov.GOV_PACK, policy=gov_engine,
                     capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)


def test_a_t3_core_cannot_be_built_without_a_capture_store(gov_engine, gov_purposes):
    """D6's capture rule, made structural. There is no argument for an uncaptured T3 read."""
    with pytest.raises(DocumentSourceError, match="CaptureStore"):
        DocumentCore(source_id="case_files", root=gov.GOV_PACK, policy=gov_engine,
                     capture_store=None, sources_yaml=gov.GOV_SOURCES_YAML)


# --- reader 1: text documents ----------------------------------------------------------


def test_a_doc_span_slices_back_to_the_value_it_cites(cores, ingest):
    """**The round trip.** Emit -> take the cited offsets -> slice the CAPTURE -> byte-identical.

    §4a's rule is that a span pointer is verified by slicing out the cited offsets *first* and only
    then comparing. So this slices the captured bytes at exactly `char_start:char_end` and asserts
    `==`, not containment: containment would pass for a pointer that cites the whole page.
    """
    core = cores["case_files"]
    _req, result = read(core, ingest, "document",
                        ("document_id", "claimed_case_ref", "content", "name"),
                        ("document_id",), ("document_id",), limit=8)
    checked = 0
    for row in result.rows.rows:
        for cell in row:
            if not isinstance(cell, GroundedValue) or cell.pointer.kind != "doc_span":
                continue
            captured = core._captures.get(cell.pointer.capture_digest).decode("utf-8")
            sliced = captured[cell.pointer.char_start:cell.pointer.char_end]
            assert sliced == cell.value, f"{cell.pointer.column}: the cited offsets do not slice "\
                                         f"back to the emitted value"
            checked += 1
    assert checked >= 16


def test_ocr_damage_yields_absent_never_a_repaired_guess(cores, ingest):
    """The estate's own rule: *"the correct output for those is `unresolved`, not a repaired guess"*.

    Roughly a dozen pages carry a scanner confusion in the LABEL itself — `OATE OF BIRTH`,
    `ADDRES5`, `CA5E REFERENCE`. There is deliberately no alias table repairing `OATE` to `DATE`,
    because such a table is a guess wearing a citation. So a projection asking for `date_of_birth`
    gets `Absent` for those documents and `oate_of_birth` carries what the page actually says.
    """
    core = cores["case_files"]
    _req, result = read(core, ingest, "document",
                        ("document_id", "date_of_birth", "oate_of_birth"),
                        ("document_id",), ("document_id",), limit=250)
    columns = list(result.rows.columns)
    damaged = [row for row in result.rows.rows
               if isinstance(row[columns.index("oate_of_birth")], GroundedValue)]
    assert damaged, "the estate ships pages whose DATE OF BIRTH label is OCR-damaged"
    for row in damaged:
        assert isinstance(row[columns.index("date_of_birth")], Absent)
        assert row[columns.index("date_of_birth")].field_path == "document.date_of_birth"
        # and the damaged label is still readable, with a citation
        assert isinstance(row[columns.index("oate_of_birth")], GroundedValue)


def test_the_labels_are_discovered_from_the_drop_not_declared(cores):
    """A document drop has no schema but the union of what its documents happen to say.

    So `describe()` scans, and both the clean label and its damaged twin appear as columns. That is
    the honest description of a schemaless source: `date_of_birth` and `oate_of_birth` are two
    different things the estate contains, and collapsing them would be the repair this reader
    refuses to make.
    """
    fields = {f.name for f in cores["case_files"].describe().entity("document").fields}
    assert {"date_of_birth", "oate_of_birth", "address", "addres5", "content"} <= fields
    # INDEX.csv's own columns are there too, under the name the CONTRACT gives them.
    assert "claimed_case_ref" in fields and "case_ref" not in fields
    # and the reader's plumbing is not projectable: a path is not evidence.
    assert "relative_path" not in fields


def test_slugging_refuses_a_label_that_cannot_be_a_column():
    assert slug("DATE OF BIRTH") == "date_of_birth"
    assert slug("RESIDENT NO") == "resident_no"
    assert slug("OATE OF BIRTH") == "oate_of_birth"       # not repaired
    assert slug("  ") is None
    assert slug("9 LIVES") is None                        # a leading digit is not an identifier


# --- reader 2: fixed-width legacy records ----------------------------------------------


def test_the_fixed_width_layout_comes_from_the_copybook(cores):
    """Field names and offsets are PARSED FROM `COPYBOOK.cpy`, never typed in here.

    The offsets are the running sum of the declared PIC widths, and the total is checked against
    the contract's `record_bytes`. There is no table of column positions in the adapter to drift
    away from the copybook.
    """
    layout = cores["legacy_records"]._reader._layout
    copybook = (gov.GOV_PACK / "legacy_records" / "COPYBOOK.cpy").read_text(encoding="utf-8")
    assert [name for name, *_ in layout][:4] == [
        "lm_rec_type", "lm_legacy_key", "lm_surname", "lm_forename"]
    assert layout[-1][3] == int(gov.contract_entry("legacy_records")["record_bytes"]) == 220
    # every declared PIC clause is in the parsed layout, in order
    declared = [line.split()[1] for line in copybook.splitlines()
                if " PIC " in line and line.strip().startswith("05")]
    assert len(declared) == len(layout)


def test_a_copybook_that_does_not_add_up_is_refused(tmp_path, gov_engine, captures, gov_purposes):
    """An off-by-N layout produces well-formed citations to the wrong fields. Fail closed."""
    root = tmp_path / "estate"
    shutil.copytree(gov.GOV_PACK / "legacy_records", root / "legacy_records")
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")
    copybook = root / "legacy_records" / "COPYBOOK.cpy"
    doctored = [line.replace("X(30)", "X(29)") if "LM-SURNAME" in line else line
                for line in copybook.read_text(encoding="utf-8").splitlines()]
    copybook.write_text("\n".join(doctored), encoding="utf-8")
    with pytest.raises(DocumentSourceError, match="sum to 219"):
        DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                     capture_store=captures, sources_yaml=root / "sources.yaml")


def test_a_padded_field_is_derived_and_replays_to_what_was_emitted(cores, ingest):
    """`LM-SURNAME` is 30 bytes of which 22 are pad, so the trimmed name is NOT the cited bytes.

    §4a: if the emitted bytes differ from the cited bytes, the pointer kind is `derived` or the
    value is ungrounded. There is no third option — so the cell carries
    `fixed_width_trim@v1(width=30)` over a span covering the whole declared field, and replaying
    that rule over the cited characters has to produce exactly what was emitted.
    """
    core = cores["legacy_records"]
    _req, result = read(core, ingest, "legacy_master",
                        ("lm_legacy_key", "lm_rec_type", "lm_surname", "lm_dob_cymd"), (),
                        ("lm_legacy_key",), limit=20)
    columns = list(result.rows.columns)
    surnames = [row[columns.index("lm_surname")] for row in result.rows.rows]
    derived = [c for c in surnames if isinstance(c, GroundedValue)]
    assert derived, "the fixture has surnames"
    for cell in derived:
        assert cell.pointer.kind == "derived"
        assert cell.pointer.derivation == "fixed_width_trim@v1(width=30)"
        parent = cell.pointer.derived_from[0]
        assert parent.kind == "doc_span"
        assert parent.char_end - parent.char_start == 30
        raw = core._captures.get(parent.capture_digest).decode("utf-8")[
            parent.char_start:parent.char_end]
        assert raw.rstrip(" ") == cell.value
        assert cell.value == cell.value.strip()          # no pad survived
        # the wall itself, replayed
        assert_grounded(cell, [raw])


def test_a_zero_filled_date_is_cited_verbatim_not_reinterpreted(cores, ingest):
    """`00000000` and `19980230` are what the record holds, and both are emitted as they are.

    The copybook calls the first "unknown" and the estate ships the second on purpose. Neither is
    repaired and neither is reinterpreted: what an impossible date means is a question for the
    reader of the case, not a judgement an adapter has standing to make.
    """
    core = cores["legacy_records"]
    _req, result = read(core, ingest, "legacy_master", ("lm_dob_cymd", "lm_last_upd_cymd"), (),
                        ("lm_dob_cymd",), limit=400)
    values = [c.value for row in result.rows.rows for c in row if isinstance(c, GroundedValue)]
    assert any(v == "00000000" for v in values), "the estate ships zero-filled dates"
    for row in result.rows.rows:
        for cell in row:
            if isinstance(cell, GroundedValue):
                assert cell.pointer.kind == "doc_span"     # a 9(n) field needs no transformation
                assert len(cell.value) == 8


def test_an_all_pad_field_is_absent_rather_than_the_empty_string(cores, ingest):
    """"The file says nothing here" and "the file says the empty string" are different facts."""
    core = cores["legacy_records"]
    _req, result = read(core, ingest, "legacy_master", ("lm_benefit_ref", "lm_permit_ref"), (),
                        ("lm_benefit_ref",), limit=200)
    cells = [c for row in result.rows.rows for c in row]
    assert any(isinstance(c, Absent) for c in cells), "the estate ships records with blank refs"
    assert all(c.value != "" for c in cells if isinstance(c, GroundedValue))


def test_the_legacy_entity_declares_no_key_because_it_has_none(cores, ingest):
    """Re-cut in full nightly, ~8% orphans, and some records carry a blank `LM-LEGACY-KEY`.

    So no column is a stable identity, the entity declares no key, and a projection that claims
    one is refused — it would mint citations addressing a row identity the source does not have.
    The identity is the LOCATOR: a content-addressed capture plus a byte range inside it.
    """
    core = cores["legacy_records"]
    assert core.describe().entity("legacy_master").key == ()
    with pytest.raises(UnorderedPointeredRead, match="key"):
        read(core, ingest, "legacy_master", ("lm_legacy_key",), ("lm_legacy_key",),
             ("lm_legacy_key",))


# --- reader 3: OCR bbox sidecars --------------------------------------------------------


def test_an_ocr_bbox_carries_page_coordinates_and_confidence(cores, ingest):
    """`ocr_bbox` is a real locator here: page, rectangle and `ocr_conf`, all from the sidecar."""
    core = cores["inspection_photos"]
    _req, result = read(core, ingest, "ocr_block",
                        ("image_id", "page", "line", "text", "confidence"), (), ("line",),
                        limit=5)
    columns = list(result.rows.columns)
    row = result.rows.rows[0]
    text = row[columns.index("text")]
    assert text.pointer.kind == "ocr_bbox"
    assert text.pointer.page == 1 and text.pointer.ocr_conf is not None
    assert None not in (text.pointer.x0, text.pointer.y0, text.pointer.x1, text.pointer.y1)
    sidecar = gov.sidecar(text.pointer.media_id)
    block = next(b for b in sidecar["ocr"]["blocks"] if b["text"] == text.value)
    x, y, w, h = block["bbox"]
    assert (text.pointer.x0, text.pointer.y0, text.pointer.x1, text.pointer.y1) == (
        x, y, x + w, y + h)
    assert text.pointer.ocr_conf == block["confidence"]
    # and the rectangle is inside the page the sidecar declares
    assert text.pointer.x1 <= sidecar["image"]["width"]
    assert text.pointer.y1 <= sidecar["image"]["height"]


def test_the_sidecar_geometry_is_checked_never_assumed(tmp_path, gov_engine, captures,
                                                       gov_purposes, ingest):
    """An unstated coordinate convention is a bbox that means a different rectangle per reader."""
    root = tmp_path / "estate"
    shutil.copytree(gov.GOV_PACK / "inspection_photos", root / "inspection_photos")
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")
    index = (root / "inspection_photos" / "INDEX.csv").read_text(encoding="utf-8").splitlines()
    first = index[1].split(",")[1]
    path = root / "inspection_photos" / first
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["ocr"]["coordinate_origin"] = "bottom_left"
    path.write_text(json.dumps(doc), encoding="utf-8")
    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match="coordinate_origin"):
        read(core, ingest, "ocr_block", ("text",), (), ("text",), limit=1)


def test_the_sidecar_metadata_trust_comes_from_the_contract_field_by_field(cores):
    """`inspection_id` is trusted and `application_id` is not, and both come from one JSON file.

    The estate is precise about why: `inspection_id`, `photo_batch_ref`, `captured_by` and
    `captured_at` come from the agency's own device fleet, while the sidecar's `application_id` is
    *"a convenience copy to be reconciled against that path, never trusted alone"*. **OCR text
    binds nothing.** So trust is a lookup, per field, in the contract — not a property of the file.
    """
    described = cores["inspection_photos"].describe()
    photo = {f.name: f.trust for f in described.entity("photo").fields}
    contract = gov.contract_entry("inspection_photos")
    for name in contract["trusted_fields"]:
        assert photo[name] is Trust.TRUSTED, name
    assert photo["application_id"] is Trust.UNTRUSTED
    assert photo["parcel_id"] is Trust.UNTRUSTED
    block = {f.name: f.trust for f in described.entity("ocr_block").fields}
    assert block["text"] is Trust.UNTRUSTED
    assert contract["untrusted_fields"] == ["inspection_photos.ocr.blocks[].text"]


# =======================================================================================
# 2. T3: the capture is the cited artefact
# =======================================================================================


def test_every_ledger_bound_read_is_captured_and_every_pointer_names_the_bytes(cores, ingest):
    """Every emitted pointer carries `capture_digest`, and the digest resolves to the read bytes.

    Without it a `doc_span` or `ocr_bbox` pointer cites a **file path**, and a path is a thing
    anyone with write access can change under it. Nobody could afterwards prove which bytes were
    parsed, and the Capture Store would be decoration.
    """
    for source_id, entity, fields, key, order in SOURCES:
        core = cores[source_id]
        _req, result = read(core, ingest, entity, fields, key, order, limit=4)
        seen = 0
        for row in result.rows.rows:
            for cell in row:
                if not isinstance(cell, GroundedValue):
                    continue
                pointer = cell.pointer
                digests = ([pointer.capture_digest] if pointer.kind != "derived"
                           else [p.capture_digest for p in pointer.derived_from])
                for digest in digests:
                    assert digest and digest.startswith("sha256:"), \
                        f"{source_id}.{entity}.{pointer.column} cites no captured bytes"
                    assert core._captures.exists(digest)
                    seen += 1
        assert seen, f"{source_id}.{entity} emitted nothing"


def test_a_capture_is_idempotent_by_content(cores, ingest):
    """Re-reading an unchanged file is not a second artefact: the digest IS the identity."""
    core = cores["case_files"]
    req_a, result_a = read(core, ingest, "document", ("document_id", "content"),
                           ("document_id",), ("document_id",), limit=3)
    core._artefacts.clear()          # force the reader back to the filesystem
    _req_b, result_b = read(core, ingest, "document", ("document_id", "content"),
                            ("document_id",), ("document_id",), limit=3)
    first = [c.pointer.capture_digest for row in result_a.rows.rows for c in row]
    second = [c.pointer.capture_digest for row in result_b.rows.rows for c in row]
    assert first == second


def test_a_pointer_with_no_capture_digest_is_unverifiable(cores, ingest):
    """A pointer that cites only a path cites something anyone can edit. Say so; do not check it."""
    core = cores["case_files"]
    _req, result = read(core, ingest, "document", ("document_id",), ("document_id",),
                        ("document_id",), limit=1)
    pointer = result.rows.rows[0][0].pointer
    stripped = SourcePointer(
        source_id=pointer.source_id, kind="doc_span", document_id=pointer.document_id,
        char_start=pointer.char_start, char_end=pointer.char_end, snapshot=pointer.snapshot,
        trust=pointer.trust)
    verdict, note = core.verify_with_note(stripped)
    assert verdict is Verdict.UNVERIFIABLE
    assert "capture_digest" in note


def test_verify_resolves_against_the_capture_and_the_live_file_only_answers_vanished(
        cores, ingest, tmp_path, gov_engine, gov_purposes):
    """For a source with no live-bytes check, the capture IS the answer.

    `legacy_records` is re-cut in full nightly and record offsets are not stable across cuts, so a
    byte-range pointer into last night's cut addresses different data today. The contract says so
    and declares `verify_against_capture` only — which means changing the live file must not move
    the verdict, because the live file was never what the citation rested on.
    """
    root = tmp_path / "estate"
    shutil.copytree(gov.GOV_PACK / "legacy_records", root / "legacy_records")
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")
    store = CaptureStore(root=tmp_path / "captures")
    core = DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                        capture_store=store, sources_yaml=root / "sources.yaml")
    _req, result = read(core, ingest, "legacy_master", ("lm_rec_type", "lm_surname"), (),
                        ("lm_surname",), limit=2)
    pointer = result.rows.rows[0][0].pointer
    assert core.verify(pointer) is Verdict.VERIFIED_AS_OF_CAPTURE

    shard = next((root / "legacy_records" / "data").glob("*.dat"))
    data = bytearray(shard.read_bytes())
    data[0:2] = b"ZZ"                                    # tonight's cut says something else
    shard.write_bytes(bytes(data))
    fresh = DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                         capture_store=store, sources_yaml=root / "sources.yaml")
    assert fresh.verify(pointer) is Verdict.VERIFIED_AS_OF_CAPTURE

    shard.unlink()
    gone = DocumentCore(source_id="legacy_records", root=root, policy=gov_engine,
                        capture_store=store, sources_yaml=root / "sources.yaml")
    verdict, note = gone.verify_with_note(pointer)
    assert verdict is Verdict.VANISHED
    assert "NOT evidence of deletion" in note


def test_a_shredded_capture_verifies_as_vanished_never_as_verified(cores, ingest, tmp_path,
                                                                   gov_engine, gov_purposes):
    """Captured content is personal data. An erasure leaves a tombstone, and a citation to it
    resolves to "destroyed under this authority" — which is an answer, where a missing file is not.
    """
    store = CaptureStore(root=tmp_path / "captures")
    core = DocumentCore(source_id="inspection_photos", root=gov.GOV_PACK, policy=gov_engine,
                        capture_store=store, sources_yaml=gov.GOV_SOURCES_YAML)
    _req, result = read(core, ingest, "photo", ("image_id", "inspection_id"), ("image_id",),
                        ("image_id",), limit=1)
    pointer = result.rows.rows[0][0].pointer
    assert core.verify(pointer) is Verdict.VERIFIED_AS_OF_CAPTURE
    store.shred(pointer.capture_digest, authority="dpo@aldermoor.example", reason="erasure R-42")
    verdict, note = core.verify_with_note(pointer)
    assert verdict is Verdict.VANISHED
    assert "erasure" in note and "dpo@aldermoor.example" in note


def test_an_ocr_bbox_reports_the_sidecar_and_the_image_separately(cores, ingest):
    """*"Both must be reported; reporting only the first would let a reader believe the photograph
    had been checked."*

    No image bytes ship and `image_present` is `false` throughout, so the sidecar corroborates only
    itself: `VERIFIED_AS_OF_CAPTURE` as to the sidecar, `UNVERIFIABLE` as to the image.
    """
    core = cores["inspection_photos"]
    _req, result = read(core, ingest, "ocr_block", ("text",), (), ("text",), limit=1)
    pointer = result.rows.rows[0][0].pointer
    verdict, note = core.verify_with_note(pointer)
    assert verdict is Verdict.VERIFIED_AS_OF_CAPTURE
    assert "UNVERIFIABLE as to the image" in note
    resolution = core.resolve_pointer(pointer)
    assert isinstance(resolution, PointerResolution)
    assert "UNVERIFIABLE as to the image" in resolution.note
    assert resolution.cell.value == result.rows.rows[0][0].value
    assert gov.sidecar(pointer.media_id)["image_present"] is False


def test_drift_benign_is_not_advertisable_for_a_document_source(cores):
    """§5: an adapter with no entity-level change signal never emits `DRIFT_BENIGN`.

    There is nothing here to compare a non-cited change against — `mtime` is the only candidate and
    the estate calls it *"wrong in both directions"* — so the honest answer is the one the lattice
    already has, and `assert_verdict_advertised` refuses the other rather than trusting the Core to
    remember.
    """
    for core in cores.values():
        described = core.describe()
        assert described.detects_benign_drift is False
        assert Verdict.DRIFT_BENIGN not in described.reachable_verdicts
        from databridge_adapter.verdicts import assert_verdict_advertised
        with pytest.raises(CapabilityNotAdvertised, match="DRIFT_BENIGN"):
            assert_verdict_advertised(Verdict.DRIFT_BENIGN, described)


def test_a_re_scan_of_a_case_file_is_drift_material(mutable_case_files, ingest):
    """`case_files` is the one source whose contract declares a live-bytes check, and says why:
    *"it is how a re-scan is DETECTED"*. A re-scan destroys the OCR pass the citation was made
    from, so it is material and it pages someone.
    """
    root, _store, build = mutable_case_files
    core = build()
    _req, result = read(core, ingest, "document", ("document_id", "content"), ("document_id",),
                        ("document_id",), limit=1)
    pointer = result.rows.rows[0][1].pointer
    assert core.verify(pointer) is Verdict.VERIFIED         # live bytes still match the capture

    page = core._reader.live_path_of(pointer.document_id)
    page.write_text(page.read_text(encoding="utf-8") + "\n[re-scanned 2027]\n", encoding="utf-8")
    verdict = build().verify(pointer)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert verdict.must_escalate is True


# =======================================================================================
# 3. §11 — the eight acceptance tests, for this source
# =======================================================================================


class TestTheEightAcceptanceTests:
    """`ADAPTER_AGENT_SPEC` §11, run against the three document readers.

    §11's own framing: *"If those eight pass for two adapters — one relational source, one mailbox
    — the design is real and every further source is an exercise."* This is the exercise, and the
    two it changes are 2 and 8: a T3 source's best verdict is `VERIFIED_AS_OF_CAPTURE`, and it may
    not report benign drift at all.
    """

    def test_1_no_ungrounded_cell_survives_validation(self, cores, ingest):
        """Every cell is `GroundedValue | Withheld | Absent`, and every verbatim pointer's cited
        location actually contains the value — checked by re-slicing the CAPTURE, not by trusting
        the Core's own bookkeeping."""
        for source_id, entity, fields, key, order in SOURCES:
            core = cores[source_id]
            _req, result = read(core, ingest, entity, fields, key, order, limit=4)
            assert result.rows.rows
            for row in result.rows.rows:
                assert len(row) == len(fields)
                for cell in row:
                    assert isinstance(cell, (GroundedValue, Withheld, Absent))
                    if not isinstance(cell, GroundedValue):
                        continue
                    pointer = cell.pointer
                    if pointer.kind == "derived":
                        parent = pointer.derived_from[0]
                        raw = core._captures.get(parent.capture_digest).decode("utf-8")[
                            parent.char_start:parent.char_end]
                        assert_grounded(cell, [raw])
                    elif pointer.kind == "doc_span":
                        whole = core._captures.get(pointer.capture_digest).decode("utf-8")
                        assert_grounded(cell, whole)
                        assert whole[pointer.char_start:pointer.char_end] == cell.value
                    else:
                        assert pointer.kind == "ocr_bbox"
                        assert_grounded(cell, cell.value)

    def test_1b_a_fabricated_value_is_refused_at_emission(self, cores, ingest, monkeypatch):
        """The wall runs at emission, not in review. A cell whose value is not at the offsets it
        cites refuses the whole result — a fabricated cell is not dropped quietly beside honest
        ones."""
        core = cores["case_files"]
        real = core._reader.read

        def lying_read(entity, *, limit):
            rows = real(entity, limit=limit)
            first = rows[0]
            cell = first.cells["document_id"]
            poisoned = DocCell(column="document_id", raw="CF-9999-9999",
                               artefact=cell.artefact, span=cell.span)
            return [DocRow(row_id=first.row_id, cells={**first.cells, "document_id": poisoned},
                           sort_values=first.sort_values)]

        monkeypatch.setattr(core._reader, "read", lying_read)
        with pytest.raises(UngroundedValue, match="fabricated"):
            read(core, ingest, "document", ("document_id",), ("document_id",), ("document_id",),
                 limit=1)

    def test_2_every_pointer_re_resolves_per_tier(self, cores, ingest):
        """T3's positive answer is `VERIFIED_AS_OF_CAPTURE` — or `VERIFIED` for the one source
        whose contract declares a live-bytes check. Demanding plain `VERIFIED` everywhere would
        assert something the design does not claim (§11.2)."""
        for source_id, entity, fields, key, order in SOURCES:
            core = cores[source_id]
            _req, result = read(core, ingest, entity, fields, key, order, limit=3)
            live_bytes = "verify_live_bytes" in gov.contract_entry(source_id)["capabilities"]
            expected = {Verdict.VERIFIED, Verdict.VERIFIED_AS_OF_CAPTURE} if live_bytes \
                else {Verdict.VERIFIED_AS_OF_CAPTURE}
            for row in result.rows.rows:
                for cell in row:
                    if isinstance(cell, GroundedValue):
                        assert core.verify(cell.pointer) in expected, \
                            f"{source_id}.{entity}.{cell.pointer.column}"

    def test_3_read_hash_is_deterministic_and_becomes_the_query_hash(self, cores, ingest):
        """Same request -> same hash, and `read_hash` BECOMES `SnapshotIdentity.query_hash` (D2).
        That is what makes a pointer verifiable by re-execution rather than by trust."""
        core = cores["case_files"]
        req_a, result = read(core, ingest, "document", ("document_id", "content"),
                             ("document_id",), ("document_id",), limit=3)
        req_b = CapabilityRequest(
            source_id="case_files", capability="document_read", principal=ingest,
            projection=Projection(entity="document", fields=("document_id", "content"),
                                  key=("document_id",)),
            order_by=("document_id",), limit=3)
        assert req_a.read_hash == req_b.read_hash
        assert result.rows.snapshot.query_hash == req_a.read_hash
        for row in result.rows.rows:
            for cell in row:
                if isinstance(cell, GroundedValue) and cell.pointer.kind != "derived":
                    assert cell.pointer.query_hash == req_a.read_hash

    def test_4_t3_refuses_the_past(self, cores, ingest):
        """`snapshot(at=<yesterday>)` RAISES. There is deliberately no branch substituting `now()`:
        a caller that believes it received the past and did not is the failure the tier system
        exists to prevent, and it is silent — the rows look exactly like rows."""
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        for source_id, entity, fields, key, order in SOURCES:
            core = cores[source_id]
            req = CapabilityRequest(
                source_id=source_id, capability="document_read", principal=ingest,
                projection=Projection(entity=entity, fields=fields, key=key),
                order_by=order, limit=2, as_of=yesterday)
            with pytest.raises(CannotPinPast, match="T3"):
                core.snapshot(req)
            with pytest.raises(CannotPinPast, match="Capture Store"):
                core.resolve(req)

    def test_5_untrusted_cannot_parameterise_but_can_be_re_resolved(self, gov_policy_files,
                                                                    captures, gov_purposes,
                                                                    ingest):
        """Both halves, because either alone is the wrong design.

        The value of an OCR'd case reference may not become a filter — *"untrusted values are
        quotable, not actionable"*. But the **same pointer** passed in `resolve` must succeed:
        using a locator to retrieve bytes is not letting the bytes decide something, and refusing
        it would make every piece of untrusted evidence in the estate unverifiable.
        """
        taint = RegistryTaintMap()
        engine = _engine(gov_policy_files["with_exception"], gov_policy_files["bindings"])
        engine._taint = taint
        core = DocumentCore(source_id="case_files", root=gov.GOV_PACK, policy=engine,
                            capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)
        _req, result = read(core, ingest, "document", ("document_id", "claimed_case_ref"),
                            ("document_id",), ("document_id",), limit=1)
        claimed = next(c for c in result.rows.rows[0]
                       if isinstance(c, GroundedValue) and c.pointer.column == "claimed_case_ref")
        assert claimed.pointer.trust is Trust.UNTRUSTED
        taint.register_value(claimed)

        # (a) as a parameter — a control-flow position — it raises.
        poisoned = CapabilityRequest(
            source_id="case_files", capability="document_read", principal=ingest,
            projection=Projection(entity="document", fields=("document_id",),
                                  key=("document_id",)),
            order_by=("document_id",), params={"case_ref": claimed.value}, limit=1)
        with pytest.raises(UntrustedInControlFlow, match="quotable, not actionable"):
            screen_sinks(poisoned, taint)
        with pytest.raises(UntrustedInControlFlow):
            core.resolve(poisoned)

        # (b) the SAME untrusted pointer, passed in `resolve`, comes back.
        again = CapabilityRequest(
            source_id="case_files", capability="document_read", principal=ingest,
            projection=Projection(entity="document", fields=("document_id", "claimed_case_ref"),
                                  key=("document_id",)),
            order_by=("document_id",), resolve=(claimed.pointer,), limit=250)
        re_read = core.resolve(again)
        values = [c.value for row in re_read.rows.rows for c in row
                  if isinstance(c, GroundedValue) and c.pointer.column == "claimed_case_ref"]
        assert claimed.value in values

    def test_6_withheld_is_not_unresolved(self, gov_policy_files, captures, gov_purposes,
                                          cores, ingest):
        """A policy-denied field is `Withheld` with a reason code; a field OCR damage destroyed is
        `Absent`. They are not the same fact: *"withheld by policy: <reason code>"* is appealable
        and names a rule in `roles.yaml`, while *"the page does not say"* is a fact about the page.

        The same document is read twice — once under the estate's literal free-text default and
        once under the machine ingest exception — because that is what makes the distinction
        visible. Under the default, `date_of_birth` comes back `Withheld` **whether or not the
        page carries it**: D4 is explicit that a denial *"deliberately says nothing about whether a
        value exists behind it"*, and a denial that leaked existence would be a membership oracle.
        Under the exception, the same cell on the same damaged page comes back `Absent`.
        """
        described = cores["case_files"].describe()
        labels = [f"case_files.document.{f.name}" for f in described.entity("document").fields]
        # The estate's LITERAL default: `free_text_policy.default: withheld`, with the
        # adapter_service ingest exception removed. Every OCR surface is then denied to everyone.
        roles = gov.write_roles(gov_policy_files["root"] / "roles_no_exception.yaml",
                                free_text_exception=False, extra_free_text=labels)
        engine = _engine(roles, gov_policy_files["bindings"])
        denied_core = DocumentCore(source_id="case_files", root=gov.GOV_PACK, policy=engine,
                                   capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)
        fields = ("document_id", "content", "date_of_birth", "oate_of_birth")
        _req, denied = read(denied_core, ingest, "document", fields, ("document_id",),
                            ("document_id",), limit=250)
        columns = list(denied.rows.columns)
        content = [row[columns.index("content")] for row in denied.rows.rows]
        assert content and all(isinstance(c, Withheld) for c in content)
        record = content[0].record
        assert record.reason_code == "POLICY_FREE_TEXT_UNREVIEWED"
        assert record.field_path == "document.content"
        assert record.policy_id
        # A withheld cell carries NO pointer — withholding is not a citation.
        assert not hasattr(content[0], "pointer")
        # D4: the denial is identical whether or not a value exists behind it.
        assert all(isinstance(row[columns.index("date_of_birth")], Withheld)
                   for row in denied.rows.rows)

        # Under the ingest exception, the SAME damaged pages resolve to `Absent` — which is the
        # other cell state, from the other cause, and it names the question that went unanswered.
        _req, allowed = read(cores["case_files"], ingest, "document", fields, ("document_id",),
                             ("document_id",), limit=250)
        damaged = [row for row in allowed.rows.rows
                   if isinstance(row[columns.index("oate_of_birth")], GroundedValue)]
        assert damaged, "the estate ships pages whose DATE OF BIRTH label is OCR-damaged"
        for row in damaged:
            absent = row[columns.index("date_of_birth")]
            assert isinstance(absent, Absent)
            assert absent.field_path == "document.date_of_birth"
        assert not any(isinstance(c, Withheld) for row in allowed.rows.rows for c in row)

    def test_7_the_shell_holds_no_credentials_and_no_handle_to_the_source(self, cores):
        """A Shell is built from a `SourceDescription` — metadata, no data, no path, no store.

        Honest scope, as §11.7 requires: this constrains the DOCUMENTED path. It is not a proof
        that a compromised Shell process cannot reach the filesystem; that is the sandbox's job,
        not this test's.
        """
        core = cores["case_files"]
        shell = AdapterShell(description=core.describe(), harness=None)
        assert not hasattr(shell, "resolve") and not hasattr(shell, "run")
        state = vars(shell)
        assert not any(isinstance(v, (Path, CaptureStore)) for v in state.values())
        assert not any(isinstance(v, DocumentCore) for v in state.values())
        brief = shell.describe_for_model()
        assert str(gov.GOV_PACK) not in brief        # no path to the estate
        assert "relative_path" not in brief          # not projectable, so never briefed
        assert "sha256:" not in brief                # no capture digests, and therefore no bytes
        for banned in ("CF-2024-0001", "APP-", "CLA-"):
            assert banned not in brief               # no values: the brief is schema, not data

    def test_8_drift_classification(self, mutable_case_files, ingest, cores):
        """Mutate a CITED value -> `DRIFT_MATERIAL`, always. And `DRIFT_BENIGN` only for adapters
        that advertise it — which none of these do, so the correct answer for an untouched artefact
        is the positive verdict, and asserting otherwise would test a promise the design does not
        make."""
        root, _store, build = mutable_case_files
        core = build()
        _req, result = read(core, ingest, "document", ("document_id", "name"), ("document_id",),
                            ("document_id",), limit=1)
        cited = next(c for c in result.rows.rows[0]
                     if isinstance(c, GroundedValue) and c.pointer.column == "name")
        page = core._reader.live_path_of(cited.pointer.document_id)
        page.write_text(page.read_text(encoding="utf-8").replace(cited.value, "SOMEONE ELSE"),
                        encoding="utf-8")
        assert build().verify(cited.pointer) is Verdict.DRIFT_MATERIAL
        for core_ in cores.values():
            assert core_.describe().detects_benign_drift is False


# =======================================================================================
# 4. the planted injections, contained end to end
# =======================================================================================


def test_the_planted_ocr_injections_are_ingested_kept_and_contained(cores, gov_policy_files,
                                                                    captures, gov_purposes,
                                                                    ingest):
    """INJ-09 (a case-file page) and INJ-10 (a photographed placard), end to end.

    `INJECTIONS.md` sets the bar and it has four parts, all of which are asserted here:

    1. the text is **ingested and kept**, with a pointer and `trust=untrusted`;
    2. nothing in it changes a decision, a status, a grant, a plan or a route;
    3. it can still be **shown to a human and cited**, because hiding it would destroy the audit
       trail that makes the estate worth reading;
    4. anything **derived** from it is untrusted too — the lattice does not launder.

    > *"A system that refuses to ingest these has not passed. It has just moved the failure
    > somewhere harder to see."*
    """
    planted = {i["id"]: i for i in gov.planted_injections()}
    inj09, inj10 = planted["INJ-09"], planted["INJ-10"]
    assert inj09["planted_at"].startswith("case_files/")
    assert inj10["planted_at"].startswith("inspection_photos/")

    taint = RegistryTaintMap()
    engine = _engine(gov_policy_files["with_exception"], gov_policy_files["bindings"])
    engine._taint = taint

    # --- INJ-09: OCR of a hand-written margin annotation on a scanned page ---------------
    core = DocumentCore(source_id="case_files", root=gov.GOV_PACK, policy=engine,
                        capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)
    _req, result = read(core, ingest, "document",
                        ("document_id", "content", "note_to_electronic_reader"),
                        ("document_id",), ("document_id",), limit=250)
    columns = list(result.rows.columns)
    # `INJECTIONS.md` prints the payload unwrapped; the scanned page carries it re-flowed over two
    # lines. Compared on collapsed whitespace, because the wrapping is a fact about the scanner and
    # not about the payload — and re-flowing is exactly the OCR damage this estate ships.
    def flat(text: str) -> str:
        return " ".join(text.split())

    hit = next(row for row in result.rows.rows
               if isinstance(row[columns.index("content")], GroundedValue)
               and flat(inj09["payload"]) in flat(row[columns.index("content")].value))
    content = hit[columns.index("content")]
    annotation = hit[columns.index("note_to_electronic_reader")]

    # (1) kept, with a pointer, untrusted
    assert content.pointer.trust is Trust.UNTRUSTED
    assert annotation.pointer.trust is Trust.UNTRUSTED
    assert content.pointer.capture_digest
    # (3) quotable — and the citation round-trips against the CAPTURE
    captured = core._captures.get(annotation.pointer.capture_digest).decode("utf-8")
    assert captured[annotation.pointer.char_start:annotation.pointer.char_end] == annotation.value
    assert flat(annotation.value) in flat(inj09["payload"])
    assert core.verify(annotation.pointer).is_positive

    # (2) it may not become a filter, a capability, an entity or an order term
    taint.register_value(annotation)
    taint.register_value(content)
    for sink_kw in ({"params": {"note": annotation.value}},):
        with pytest.raises(UntrustedInControlFlow):
            screen_sinks(CapabilityRequest(
                source_id="case_files", capability="document_read", principal=ingest,
                projection=Projection(entity="document", fields=("document_id",),
                                      key=("document_id",)),
                order_by=("document_id",), limit=1, **sink_kw), taint)

    # --- INJ-10: a placard in a photograph, read by OCR ----------------------------------
    photos = DocumentCore(source_id="inspection_photos", root=gov.GOV_PACK, policy=engine,
                          capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)
    image_id = Path(inj10["planted_at"]).stem
    _req, blocks = read(photos, ingest, "ocr_block", ("image_id", "line", "text", "confidence"),
                        (), ("line",), limit=3000)
    cols = list(blocks.rows.columns)
    payload = next(row[cols.index("text")] for row in blocks.rows.rows
                   if isinstance(row[cols.index("text")], GroundedValue)
                   and row[cols.index("text")].value == inj10["payload"])
    assert payload.pointer.kind == "ocr_bbox"
    assert payload.pointer.trust is Trust.UNTRUSTED
    assert payload.pointer.media_id == image_id
    assert payload.pointer.capture_digest
    assert photos.verify(payload.pointer) is Verdict.VERIFIED_AS_OF_CAPTURE

    # (4) the lattice does not launder: the trusted metadata on the SAME sidecar stays trusted,
    #     and nothing derived from the OCR text becomes so.
    _req, photo_rows = read(photos, ingest, "photo", ("image_id", "inspection_id", "captured_by"),
                            ("image_id",), ("image_id",), limit=420)
    pcols = list(photo_rows.rows.columns)
    same = next(row for row in photo_rows.rows.rows
                if isinstance(row[pcols.index("image_id")], GroundedValue)
                and row[pcols.index("image_id")].value == image_id)
    assert same[pcols.index("inspection_id")].pointer.trust is Trust.TRUSTED
    assert payload.pointer.trust is Trust.UNTRUSTED
    # ...and the OCR text binds nothing: it is not the entity's key and not in any row rule.
    assert photos.describe().entity("ocr_block").key == ()

    taint.register_value(payload)
    with pytest.raises(UntrustedInControlFlow):
        screen_sinks(CapabilityRequest(
            source_id="inspection_photos", capability="document_read", principal=ingest,
            projection=Projection(entity="ocr_block", fields=("text",)),
            order_by=("text",), params={"placard": payload.value}, limit=1), taint)


def test_the_untrusted_case_ref_is_exposed_under_the_name_the_contract_gives_it(cores):
    """`case_ref` is OCR'd off a form a member of the public filled in, so it is a **claim**.

    The contract renames it — ``exposed_as: claimed_case_ref`` — and the reader reads that rename
    out of the contract rather than spelling it here, so the estate can change what it calls the
    claim without a code change. It may be stored, cited and displayed; it may not be a row
    predicate, may not create a traversable edge, and may not decide who sees the document.
    """
    binding = gov.contract_entry("case_files")["trusted_binding"]
    assert binding["status"] == "claimed_only"
    fields = {f.name: f for f in cores["case_files"].describe().entity("document").fields}
    assert binding["exposed_as"] in fields
    assert binding["field"] not in fields
    assert fields[binding["exposed_as"]].trust is Trust.UNTRUSTED


# =======================================================================================
# 5. policy — the estate's roles, honoured rather than worked around
# =======================================================================================


def test_free_text_is_default_deny_and_the_exception_is_the_ingest_grant(gov_policy_files):
    """`free_text_policy.default: withheld`, *"regardless of any grant over the table that contains
    it"*. The one exception in the translated model is the estate's own machine ingest grant."""
    model = load_role_model(gov_policy_files["with_exception"])
    assert model.is_free_text("case_files", "document", "content")
    assert model.is_free_text("inspection_photos", "ocr_block", "text")
    assert [e["role"] for e in model.free_text_exceptions] == ["adapter_service"]
    assert model.free_text_exceptions[0]["purposes"] == ["ingest"]


def test_a_human_caseworker_read_of_a_document_source_fails_closed(gov_policy_files, captures,
                                                                   gov_purposes):
    """**A finding, asserted rather than worked around.**

    Every human grant over a document source in this estate is scoped through a DATABASE:
    `case_ref_trusted IN @benefits.my_caseload` resolves to a subquery over `benefits.claims`. One
    request reaches one adapter and one adapter holds one source's credentials, so the scope
    cannot be evaluated here — and the estate's own note is that a row rule which silently does not
    run is *"the exact shape of a policy that permits everything while appearing not to"*.

    So the read is REFUSED, by name. The fix is the estate's `trusted_case_bindings.case_files`
    intake mapping, or a cross-source join performed in the graph where the pointers meet — not a
    relaxation here.
    """
    engine = _engine(gov_policy_files["with_exception"], gov_policy_files["bindings"])
    core = DocumentCore(source_id="case_files", root=gov.GOV_PACK, policy=engine,
                        capture_store=captures, sources_yaml=gov.GOV_SOURCES_YAML)
    caseworker = Principal(actor="orchestrator@walnut", on_behalf_of="caseworker-12",
                           purpose="benefits.eligibility", grant_id="grant-cw-12")
    with pytest.raises(RowRuleNotCompilable, match="another source"):
        read(core, caseworker, "document", ("document_id",), ("document_id",), ("document_id",))


def test_the_plan_declares_capture_and_refuses_to_claim_completeness(cores, ingest):
    """A plan over an unversioned drop cannot promise a complete incremental read, and says so."""
    core = cores["case_files"]
    req = CapabilityRequest(
        source_id="case_files", capability="document_read", principal=ingest,
        projection=Projection(entity="document", fields=("document_id",), key=("document_id",)),
        order_by=("document_id",), limit=10)
    plan = core.plan(req)
    assert plan.requires_capture is True
    assert plan.read_hash == req.read_hash
    assert plan.estimated_cost.llm_tokens == 0
    assert "no change cursor" in plan.completeness_caveat
    assert "mtime" in plan.completeness_caveat            # the rejected candidate, named
    assert "not detectable" in plan.completeness_caveat
    assert any("Capture Store" in op for op in plan.operations)


def test_an_unadvertised_capability_is_rejected_and_never_reconsidered(cores, ingest):
    """§10: the rejection is final. A screen that feeds its refusal back to a model until something
    passes is a screen that eventually passes anything."""
    core = cores["case_files"]
    with pytest.raises(CapabilityNotAdvertised):
        core.resolve(CapabilityRequest(
            source_id="case_files", capability="row_read", principal=ingest,
            projection=Projection(entity="document", fields=("document_id",),
                                  key=("document_id",)),
            order_by=("document_id",), limit=1))


def test_the_document_core_conforms_to_the_five_verb_port(cores):
    """One port, all sources. The orchestrator selects by `source_id` and knows only this Protocol.

    `assert_conforms` checks the signatures, not just that the attributes exist — a
    `runtime_checkable` Protocol would accept `resolve = None`.
    """
    from databridge_adapter import assert_conforms

    for core in cores.values():
        assert_conforms(core)
        assert core.pinning_tier == "T3"
        assert core.capabilities == {"structural_read", "document_read"}


def test_resolution_mode_re_fetches_the_cited_row_and_not_the_estate(cores, ingest):
    """A pointer is a LOCATOR, and re-resolving it must not depend on the read that surfaced it.

    Matched on the locator rather than on the artefact, because in a document drop every structural
    cell in the source cites the same captured `INDEX.csv`: matching by artefact would re-resolve
    one citation into every row in the estate.
    """
    core = cores["case_files"]
    _req, first = read(core, ingest, "document", ("document_id", "claimed_case_ref"),
                       ("document_id",), ("document_id",), limit=5)
    pointer = first.rows.rows[2][0].pointer
    again = CapabilityRequest(
        source_id="case_files", capability="document_read", principal=ingest,
        projection=Projection(entity="document", fields=("document_id", "claimed_case_ref"),
                              key=("document_id",)),
        order_by=("document_id",), resolve=(pointer,), limit=250)
    result = core.resolve(again)
    assert len(result.rows.rows) == 1
    assert result.rows.rows[0][0].value == first.rows.rows[2][0].value
    # the re-resolved pointer cites the same bytes, and a NEW read hash — two different reads
    assert result.rows.rows[0][0].pointer.capture_digest == pointer.capture_digest
    assert again.read_hash != _req.read_hash


# --- a fabricated confidence is a value with no source ------------------------------------


def test_a_sidecar_block_with_a_null_confidence_is_refused(tmp_path, gov_engine, captures,
                                                           gov_purposes, ingest):
    """`ocr_conf` must be the engine's own number, or the read must refuse.

    The reader computed `float(block.get("confidence") or 0.0)`, which invented a confidence
    twice over: a `null` — the sidecar producer's way of saying *"this line came from a PDF text
    layer, no recognition ran, so there is no confidence to report"* — became `0.0`; and a
    genuinely measured `0.0` became indistinguishable from an absent one, because `0.0 or 0.0` is
    `0.0` either way. The two most different statements an engine can make arrived identical.

    `docling_extract` is deliberately careful never to emit a confidence it does not have.
    Consuming its output by manufacturing the number discards that at the last step, and puts a
    value with no source into the one pointer field whose job is to report how much the recogniser
    trusted itself.

    Found by the sidecar-producer workstream reading the consumer it had to satisfy — neither
    side's own review caught it.
    """
    root = tmp_path / "estate"
    shutil.copytree(gov.GOV_PACK / "inspection_photos", root / "inspection_photos")
    shutil.copy(gov.GOV_SOURCES_YAML, root / "sources.yaml")

    sidecars = sorted((root / "inspection_photos" / "sidecars").glob("*.json"))
    assert sidecars, "fixture has no sidecars"
    target = sidecars[0]
    doc = json.loads(target.read_text(encoding="utf-8"))
    blocks = doc.get("ocr", {}).get("blocks") or []
    assert blocks, f"{target.name} declares no OCR blocks"
    blocks[0]["confidence"] = None
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    core = DocumentCore(source_id="inspection_photos", root=root, policy=gov_engine,
                        capture_store=captures, sources_yaml=root / "sources.yaml")
    with pytest.raises(DocumentSourceError, match="confidence"):
        read(core, ingest, "ocr_block", ("image_id", "line", "text", "confidence"), (),
             ("line",), limit=500)


def test_every_ocr_block_carries_a_real_confidence(cores, ingest):
    """0.0 is a *measurement* — the recogniser saying it has no faith in this line at all — and it
    must reach the pointer unchanged rather than being erased into the value an absent confidence
    used to produce."""
    _req, result = read(cores["inspection_photos"], ingest, "ocr_block",
                        ("image_id", "line", "text", "confidence"), (), ("line",), limit=100)
    columns = list(result.rows.columns)
    pointers = [row[columns.index("text")].pointer for row in result.rows.rows]
    assert pointers, "no OCR block was read"
    assert all(p.ocr_conf is not None for p in pointers), (
        "every block in `ocr.blocks` is a recognised line and must carry the engine's own "
        "confidence; a None here means one was invented or dropped")
