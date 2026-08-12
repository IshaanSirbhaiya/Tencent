"""Evidence sheets on disk — the provenance story as a folder someone can open.

The export exists so the claim "every value traces to a source" can be *checked* without running
the app: one markdown file per entity, regenerable, diffable, readable in any editor. That makes
two things load-bearing, and these tests are about those two things.

**The file must be the sheet.** Byte for byte. An exporter that reflows, adds front-matter or
appends a newline has quietly created a second renderer, and a reader checking the file is then
checking something the app never showed and the model never read.

**The filename is an attack surface, and the sheet's escaping does not help there.** Entity ids
carry document filenames (`document:chit_01_meridian_referral.txt`), and document text is
attacker-controlled. Markdown escaping decides how a value RENDERS; a filename decides WHICH FILE
IS WRITTEN. So the hostile inputs here are built rather than hoped for: traversal, absolute paths,
drive letters, newlines, and the Windows device names that swallow writes without erroring.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from databridge_core import SourcePointer

from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.node_sheet import node_markdown
from databridge_graph.schema import GraphNode, NodeKind
from databridge_graph.sheet_export import (
    INDEX_NAME,
    MANIFEST_NAME,
    SheetExport,
    assign_filenames,
    export_sheets,
    iter_zip_chunks,
    sheet_filename,
)
from databridge_graph.store import GraphStore

_UTC = dt.timezone.utc
_WHEN = dt.datetime(2026, 1, 1, tzinfo=_UTC)
# May 2026: the coverage rules, packages, tests and patients are in force; not one referral
# document has arrived yet. The same instant the /brain demo rewinds to.
_BEFORE_THE_DOCUMENTS = dt.datetime(2026, 5, 1, tzinfo=_UTC)


@pytest.fixture(scope="module")
def store():
    return fresh_clinic_store()


@pytest.fixture(scope="module")
def open_ids(store) -> list[str]:
    return [row["id"] for row in store.open_nodes()]


def _pointer() -> SourcePointer:
    return SourcePointer(source_id="evil.txt", kind="doc_span", document_id="evil.txt",
                         char_start=0, char_end=4)


def _one_node_store(entity_id: str, *, label: str = "An ordinary label") -> GraphStore:
    store = GraphStore()
    store.add_node(GraphNode(id=entity_id, kind=NodeKind.DOCUMENT, label=label,
                             sources=[_pointer()], valid_from=_WHEN, system_from=_WHEN))
    return store


def _written(directory) -> list:
    return sorted(p for p in directory.rglob("*") if p.is_file())


# --- the export is the sheet -------------------------------------------------------------------


def test_every_open_node_produces_a_file(store, open_ids, tmp_path):
    manifest = export_sheets(store, tmp_path / "sheets")
    assert manifest.count == len(open_ids), "an open node went unexported"
    assert not manifest.skipped, f"a current export skipped something: {manifest.skipped}"
    exported = {f.entity_id for f in manifest.files}
    assert exported == set(open_ids)
    for record in manifest.files:
        assert (tmp_path / "sheets" / record.filename).is_file()
    assert (tmp_path / "sheets" / INDEX_NAME).is_file()
    assert (tmp_path / "sheets" / MANIFEST_NAME).is_file()


def test_exported_bytes_are_exactly_the_sheet(store, tmp_path):
    """No wrapping, no tidying, no trailing-newline convention — and no Windows text-mode CRLF.

    The moment the file differs from `node_markdown`, the export becomes a second renderer, and a
    reader auditing the folder is auditing something the app never displayed.
    """
    manifest = export_sheets(store, tmp_path / "sheets")
    for record in manifest.files:
        expected = node_markdown(store, record.entity_id)
        assert expected is not None
        actual = (tmp_path / "sheets" / record.filename).read_bytes()
        assert actual == expected.encode("utf-8"), f"{record.entity_id} was rewritten on write"
        assert b"\r\n" not in actual, "text mode rewrote the line endings"


def test_the_index_links_every_sheet_and_groups_it_by_kind(store, tmp_path):
    manifest = export_sheets(store, tmp_path / "sheets")
    index = (tmp_path / "sheets" / INDEX_NAME).read_text(encoding="utf-8")
    for record in manifest.files:
        assert f"]({record.filename})" in index, f"{record.entity_id} is not linked from the index"
    for kind in {f.kind for f in manifest.files}:
        assert f"## {kind} (" in index
    assert "**Generated:**" in index and "**As of:**" in index


def test_the_manifest_file_matches_what_was_written(store, tmp_path):
    manifest = export_sheets(store, tmp_path / "sheets")
    written = json.loads((tmp_path / "sheets" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written["count"] == manifest.count
    assert {f["filename"] for f in written["files"]} == {f.filename for f in manifest.files}
    on_disk = {p.name for p in _written(tmp_path / "sheets")} - {INDEX_NAME, MANIFEST_NAME}
    assert on_disk == {f["filename"] for f in written["files"]}


def test_the_export_never_writes_a_node_the_renderer_refuses(tmp_path):
    """The export must not become a way to read a node `node_markdown` returns None for."""
    store = _one_node_store("document:only-one")
    manifest = export_sheets(store, tmp_path / "sheets")
    assert [f.entity_id for f in manifest.files] == ["document:only-one"]
    assert node_markdown(store, "document:ghost") is None
    assert not (tmp_path / "sheets" / sheet_filename("document:ghost")).exists()


# --- filenames: derived from the id, hostile by assumption -------------------------------------


HOSTILE_IDS = [
    "document:../../../evil",
    "document:..\\..\\..\\evil",
    "document:/etc/passwd",
    "document:C:\\Windows\\System32\\drivers\\etc\\hosts",
    "document:evil\nrm -rf /",
    "document:evil\r\n../escaped",
    "..",
    "../..",
    "document:....//....//evil",
    "document:evil\x00.md",
    "document:sheet.md\x00.txt",
    "\\\\server\\share\\evil",
]


@pytest.mark.parametrize("entity_id", HOSTILE_IDS)
def test_a_hostile_id_stays_inside_the_export_directory(entity_id, tmp_path):
    """Nothing is written outside `dest`, and nothing below it either — one flat directory.

    `dest` is buried four levels down deliberately: an escape of `../../../..` or shallower then
    lands somewhere `tmp_path.rglob` can still see, so the assertion below is evidence rather than
    an assumption that the traversal would have been noticed.
    """
    dest = tmp_path / "a" / "b" / "c" / "sheets"
    store = _one_node_store(entity_id)

    manifest = export_sheets(store, dest)

    assert manifest.count == 1
    name = manifest.files[0].filename
    assert "/" not in name and "\\" not in name and ":" not in name
    assert name not in ("..", ".") and not name.startswith(".")
    assert "\n" not in name and "\r" not in name and "\x00" not in name
    # The only files anywhere under tmp_path are this export's three, all directly in `dest`.
    assert _written(tmp_path) == sorted(
        [dest / name, dest / INDEX_NAME, dest / MANIFEST_NAME])


@pytest.mark.parametrize("entity_id", HOSTILE_IDS)
def test_a_hostile_id_still_yields_a_readable_sheet(entity_id, tmp_path):
    """Safety must not be achieved by dropping the node — that would be a silent hole."""
    store = _one_node_store(entity_id)
    manifest = export_sheets(store, tmp_path / "sheets")
    body = (tmp_path / "sheets" / manifest.files[0].filename).read_bytes()
    assert body == node_markdown(store, entity_id).encode("utf-8")


@pytest.mark.parametrize("device", ["NUL", "CON", "con", "aux", "COM1", "lpt9", "PRN"])
def test_a_windows_device_name_never_becomes_a_filename(device, tmp_path):
    """`NUL.md` is the null device on Windows: the write SUCCEEDS and produces no file, so the
    sheet would vanish while the export reported success."""
    name = sheet_filename(device)
    assert name.split(".")[0].casefold() not in {
        "nul", "con", "aux", "prn", "com1", "lpt9"}
    store = _one_node_store(device)
    manifest = export_sheets(store, tmp_path / "sheets")
    target = tmp_path / "sheets" / manifest.files[0].filename
    assert target.is_file() and target.read_bytes()


@pytest.mark.parametrize("reserved", [INDEX_NAME, MANIFEST_NAME])
def test_a_node_cannot_overwrite_the_exports_own_files(reserved, tmp_path):
    entity_id = reserved.split(".")[0]          # "index" / "manifest"
    store = _one_node_store(entity_id)
    manifest = export_sheets(store, tmp_path / "sheets")
    assert manifest.files[0].filename != reserved
    index = (tmp_path / "sheets" / INDEX_NAME).read_text(encoding="utf-8")
    assert index.startswith("# Clinic evidence sheets"), "a node overwrote the index"


def test_a_label_never_reaches_the_filename(tmp_path):
    """Labels are document text. The name comes from the id and only from the id."""
    store = _one_node_store("document:benign", label="../../../etc/passwd\nCON")
    manifest = export_sheets(store, tmp_path / "sheets")
    assert manifest.files[0].filename == sheet_filename("document:benign")
    assert "passwd" not in manifest.files[0].filename


def test_two_ids_that_sanitise_alike_both_survive(tmp_path):
    """`patient:a/b` and `patient:a-b` both want `patient-a-b.md`, and on Windows/macOS so does
    `PATIENT:A-B`. A silent overwrite would leave the export looking complete and one sheet short.
    """
    colliding = ["patient:a/b", "patient:a-b", "PATIENT:A-B", "patient:a\\b"]
    names = assign_filenames(colliding)
    assert len({n.casefold() for n in names.values()}) == len(colliding)

    store = GraphStore()
    for i, entity_id in enumerate(colliding):
        store.add_node(GraphNode(id=entity_id, kind=NodeKind.PATIENT, label=f"P{i}",
                                 sources=[_pointer()], valid_from=_WHEN, system_from=_WHEN))
    manifest = export_sheets(store, tmp_path / "sheets")
    assert manifest.count == len(colliding)
    on_disk = {p.name for p in _written(tmp_path / "sheets")}
    assert len(on_disk) == len(colliding) + 2                      # + index.md + manifest.json
    for record in manifest.files:
        assert (tmp_path / "sheets" / record.filename).read_bytes() == \
            node_markdown(store, record.entity_id).encode("utf-8")


def test_a_filename_does_not_move_when_the_graph_grows():
    """Regenerable AND diffable: an export after a new node arrives must not renumber the old
    files. That is why a collision takes a hash of the id rather than a `-2` counter."""
    before = assign_filenames(["patient:a/b", "patient:a-b", "org:solo"])
    after = assign_filenames(["patient:a/b", "patient:a-b", "org:solo", "document:new.txt"])
    for entity_id, name in before.items():
        assert after[entity_id] == name, f"{entity_id} moved when an unrelated node was added"


def test_names_are_bounded_and_stable_for_a_very_long_id():
    long_id = "document:" + "x" * 4000
    name = sheet_filename(long_id)
    assert len(name) <= 110 and name.endswith(".md")
    assert sheet_filename(long_id) == name


# --- rewinding ---------------------------------------------------------------------------------


def test_an_as_of_export_omits_nodes_that_did_not_exist_then(store, tmp_path):
    manifest = export_sheets(store, tmp_path / "sheets", as_of=_BEFORE_THE_DOCUMENTS)

    exported = {f.entity_id for f in manifest.files}
    skipped = {s.entity_id for s in manifest.skipped}
    documents = {row["id"] for row in store.open_nodes() if row["kind"] == "document"}
    assert documents, "the seed has no documents to rewind past"
    assert documents <= skipped, "a document that did not exist yet was exported"
    assert not (documents & exported)
    assert all("as_of" in s.reason for s in manifest.skipped)
    for entity_id in documents:
        # omitted, not written empty
        assert not any(f.entity_id == entity_id for f in manifest.files)
    # and what DID exist is still there, rendered as of that instant
    patients = {row["id"] for row in store.open_nodes() if row["kind"] == "patient"}
    assert patients <= exported
    for record in manifest.files:
        expected = node_markdown(store, record.entity_id, as_of=_BEFORE_THE_DOCUMENTS)
        assert (tmp_path / "sheets" / record.filename).read_bytes() == expected.encode("utf-8")


def test_a_historical_index_does_not_name_what_did_not_exist_yet(store, tmp_path):
    """A table of contents listing entities the clinic had never heard of at that instant is the
    same leak of present state the sheet's `as_of` handling exists to prevent, moved one file
    sideways. The RUN's record of what it skipped belongs in the manifest instead."""
    export_sheets(store, tmp_path / "sheets", as_of=_BEFORE_THE_DOCUMENTS)
    index = (tmp_path / "sheets" / INDEX_NAME).read_text(encoding="utf-8")
    documents = [row["id"] for row in store.open_nodes() if row["kind"] == "document"]
    for entity_id in documents:
        assert entity_id not in index
    assert "## Omitted" in index and MANIFEST_NAME in index

    skipped = json.loads((tmp_path / "sheets" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(documents) <= {s["entity_id"] for s in skipped["skipped"]}


def test_a_naive_as_of_is_read_as_utc_rather_than_crashing(store, tmp_path):
    """Every instant in the store is UTC. A naive datetime used to raise `TypeError` from inside
    the comparison — a 500 for what is really a missing suffix on a query parameter."""
    manifest = export_sheets(store, tmp_path / "sheets",
                             as_of=dt.datetime(2026, 5, 1))                # no tzinfo
    assert manifest.as_of == _BEFORE_THE_DOCUMENTS
    assert manifest.count > 0


# --- the zip stream ----------------------------------------------------------------------------


def test_zip_chunks_form_a_real_archive_matching_the_manifest(store):
    export = SheetExport(store, root="clinic-evidence-sheets")
    payload = b"".join(iter_zip_chunks(export))

    archive = zipfile.ZipFile(io.BytesIO(payload))
    assert archive.testzip() is None
    names = set(archive.namelist())
    manifest = json.loads(archive.read("clinic-evidence-sheets/manifest.json"))
    assert names == {f"clinic-evidence-sheets/{f['filename']}" for f in manifest["files"]} | {
        "clinic-evidence-sheets/index.md", "clinic-evidence-sheets/manifest.json"}
    for record in manifest["files"]:
        entry = archive.read(f"clinic-evidence-sheets/{record['filename']}")
        assert entry == node_markdown(store, record["entity_id"]).encode("utf-8")


def test_the_zip_is_streamed_in_pieces_rather_than_assembled_whole(store):
    """More than one chunk for a 69-node graph is the observable proof that the archive is not
    built as one buffer and then handed over."""
    chunks = list(iter_zip_chunks(SheetExport(store)))
    assert len(chunks) > 10
