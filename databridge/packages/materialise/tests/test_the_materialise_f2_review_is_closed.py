"""R-021 round 2 — the second (final) Codex review of the materialisation wiring (f2), closed.

Each test proves one finding, and each fails when its fix is put back (the mutation run recorded
in `wiki/40-reviews/R-021-materialise.md`, Round 2). Every test builds its own synthetic workspace
in ``tmp_path`` (`databridge_materialise.testing`) when it mutates a store. Offline throughout.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from databridge_core import GroundedValue, Withheld
from databridge_graph.packs import deployment

from databridge_materialise import Dataset, materialise
from databridge_materialise.platform import PlatformMaterialiser, RunRefused, pointer_check
from databridge_materialise.platform_reader import PlatformReader, _file_sha256
from databridge_materialise.testing import (
    BUILD_TIME,
    DB_SOURCE,
    build_workspace,
    invoice_plan,
    passages_corpus,
    pinned,
)


@pytest.fixture(scope="module")
def vocab():
    with deployment("workspace") as v:
        yield v


@pytest.fixture()
def ws(tmp_path):
    w = build_workspace(tmp_path / "ws")
    yield w
    w.store.close()


@pytest.fixture()
def ws_np(tmp_path):
    w = build_workspace(tmp_path / "ws", with_passages=False)
    yield w
    w.store.close()


def _later() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)


def _reader(w, vocab, **kw):
    kw.setdefault("system_as_of", BUILD_TIME)
    kw.setdefault("principal", "staff")
    return PlatformReader(store=w.store, vocabulary=vocab, facts_root=w.facts,
                          passages_root=w.passages, capture_root=w.capture, **kw)


def _materialiser(w, vocab, out, *, store=None, **kw):
    return PlatformMaterialiser(store_provider=lambda: store if store is not None else w.store,
                                vocabulary=vocab, facts_root=w.facts, passages_root=w.passages,
                                capture_root=w.capture, out_root=out,
                                clock=kw.pop("clock", lambda: BUILD_TIME), **kw)


def _passage_entity(reader):
    return next(n for n, e in reader.entities().items() if e.origin == "passages")


def _passage_store(w) -> Path:
    return next(p for p in w.passages.iterdir() if p.suffix == ".sqlite")


def _refresh_manifest(facts: Path, source: str) -> None:
    """Re-seal a facts store the way the tabular stage does after writing it."""
    path = facts / f"{source}_facts.sqlite"
    man = facts / f"{source}_facts.json"
    doc = json.loads(man.read_text(encoding="utf-8")) if man.is_file() else {
        "source_id": source, "extracted_at": "2026-09-01T00:00:00+00:00", "status": "ok"}
    doc["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    man.write_text(json.dumps(doc), encoding="utf-8")


def _facts_store(facts: Path, source: str, table: str, columns, rows) -> None:
    """A minimal S2 facts store: one rows-mode table. ``columns``: ``[(name, class)]``."""
    path = facts / f"{source}_facts.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE _facts_tables (facts_table TEXT PRIMARY KEY, source_table TEXT, mode TEXT,
            rows_source INTEGER, rows_in_facts INTEGER, group_by TEXT, reason TEXT,
            extracted_at TEXT, source_sql TEXT);
        CREATE TABLE _facts_columns (facts_table TEXT, column_name TEXT, source_type TEXT,
            class TEXT, prose INTEGER, distinct_n INTEGER, non_null INTEGER, lo TEXT, hi TEXT);
        CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    q = '"' + table.replace('"', '""') + '"'
    db.execute(f"CREATE TABLE {q} ({', '.join(chr(34) + c + chr(34) for c, _ in columns)})")
    db.executemany(f"INSERT INTO {q} VALUES ({', '.join('?' for _ in columns)})", rows)
    n = len(rows)
    for j, (c, cls) in enumerate(columns):
        db.execute("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                   (table, c, cls, cls, 0, len({r[j] for r in rows}), n, None, None))
    db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
               (table, f"public.{table}", "rows", n, n, "[]", "all rows", "2026-09-01T00:00:00+00:00",
                f"SELECT * FROM {table}"))
    db.executemany("INSERT INTO _facts_meta VALUES (?,?)",
                   [("source_id", source), ("extracted_at", "2026-09-01T00:00:00+00:00")])
    db.commit()
    db.close()
    _refresh_manifest(facts, source)


# =================================================================================================
# Finding 1 — a coordinate cell cannot carry a value its pointer does not support
# =================================================================================================
def test_r2_f1_every_coordinate_cites_its_record_and_matches_exactly(ws, vocab):
    reader = _reader(ws, vocab, system_as_of=_later())
    name = _passage_entity(reader)
    fields = ["chunk_id", "doc_id", "locator", "ingested_at"]
    read = reader.read(name, fields)
    graph = reader.read("invoice", ["_id", "_valid_from", "_system_from", "billed_to_id"])
    cells = [r[f] for r in read.rows for f in fields] + [
        r[f] for r in graph.rows for f in ("_id", "_valid_from", "_system_from", "billed_to_id")]
    grounded = [c for c in cells if isinstance(c, GroundedValue)]
    assert len(grounded) > 20, "non-vacuous: coordinates were read"
    for c in grounded:
        assert c.pointer.kind == "table_cell", c.pointer
        assert reader.cell_check(c) == (True, True), c


def test_r2_f1_a_passage_coordinate_carrying_another_coordinates_value_fails(ws, vocab):
    reader = _reader(ws, vocab, system_as_of=_later())
    read = reader.read(_passage_entity(reader), ["chunk_id", "locator"])
    assert read.rows
    # chunk_id cells, each carrying its OWN pointer but its sibling locator's value
    rows = tuple((GroundedValue(value=r["locator"].value, pointer=r["chunk_id"].pointer,
                                confidence=1.0),) for r in read.rows
                 if isinstance(r["locator"], GroundedValue) and r["locator"].value
                 != r["chunk_id"].value)
    assert rows, "non-vacuous"
    check = pointer_check(Dataset(columns=("chunk_id",), rows=rows), reader)
    assert check["values_match"] == 0 and not check["all_match"], check


def test_r2_f1_a_graph_version_instant_swapped_for_another_fails(ws_np, vocab):
    reader = _reader(ws_np, vocab)
    read = reader.read("invoice", ["_valid_from", "_system_from"])
    rows = tuple((GroundedValue(value=r["_system_from"].value, pointer=r["_valid_from"].pointer,
                                confidence=1.0),) for r in read.rows
                 if isinstance(r["_valid_from"], GroundedValue)
                 and r["_valid_from"].value != r["_system_from"].value)
    assert rows, "non-vacuous: the workspace staggers valid and system time"
    check = pointer_check(Dataset(columns=("invoice._valid_from",), rows=rows), reader)
    assert check["values_match"] == 0, check


def test_r2_f1_a_chunk_corpus_offset_is_matched_not_merely_resolved(ws, vocab):
    reader = _reader(ws, vocab, system_as_of=_later())
    plan = passages_corpus(corpus_as_of=_later())
    built = materialise(plan, reader)
    ds = built.dataset
    ok = pointer_check(ds, reader, plan=plan)
    assert ok["grounded_cells"] > 0 and ok["all_resolved"] and ok["all_match"], ok["failures"]
    i = ds.columns.index("char_start")
    row0 = list(ds.rows[0])
    row0[i] = GroundedValue(value=row0[i].value + 1, pointer=row0[i].pointer, confidence=1.0)
    bad = Dataset(columns=ds.columns, rows=(tuple(row0),) + ds.rows[1:])
    check = pointer_check(bad, reader, plan=plan)
    assert not check["all_match"]
    assert check["failures"][0]["column"] == "char_start"


# =================================================================================================
# Finding 2 — a source pin sees committed WAL content
# =================================================================================================
@pytest.mark.parametrize("which", ["facts", "passages"])
def test_r2_f2_a_committed_wal_change_moves_the_pin(ws, vocab, which):
    if which == "facts":
        path, sid = ws.facts / f"{DB_SOURCE}_facts.sqlite", f"facts:{DB_SOURCE}"
        change = "INSERT INTO payments VALUES ('PAY-900', 'INV-001', 5, 1, 'z@z.example')"
    else:
        path = _passage_store(ws)
        sid = f"passages:{path.stem}"
        change = "UPDATE store_meta SET value = value || ' ' WHERE rowid = 1"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()

    def pin():
        return next(s.content_hash for s in _reader(ws, vocab).snapshots() if s.source_id == sid)

    before, main_before = pin(), _file_sha256(path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(change)
        writer.commit()
        assert _file_sha256(path) == main_before, (
            "non-vacuous: the committed change is in the WAL, not the main file")
        assert Path(f"{path}-wal").stat().st_size > 0
        assert pin() != before
    finally:
        writer.close()


def test_r2_f2_a_checkpoint_of_unchanged_rows_does_not_move_the_pin(ws, vocab):
    path = _passage_store(ws)
    sid = f"passages:{path.stem}"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    before = next(s.content_hash for s in _reader(ws, vocab).snapshots() if s.source_id == sid)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.close()
    assert next(s.content_hash for s in _reader(ws, vocab).snapshots()
                if s.source_id == sid) == before


# =================================================================================================
# Finding 3 — a plan must pin every source it reads
# =================================================================================================
def test_r2_f3_an_unpinned_plan_is_refused_naming_the_missing_pin(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": invoice_plan().model_dump(mode="json")}, by="staff")
    assert err.value.code == "SourcePinViolated" and "not pinned" in err.value.message
    assert not any((tmp_path / "out").glob("[0-9a-f]*")), "nothing published"


def test_r2_f3_pinning_only_an_unrelated_source_is_refused(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    unrelated = tuple(s for s in m.reader(system_as_of=BUILD_TIME).snapshots()
                      if s.source_id.startswith("facts:"))
    assert unrelated, "non-vacuous: there is an unrelated source to pin"
    plan = invoice_plan()
    plan = plan.model_copy(update={"temporal": plan.temporal.model_copy(
        update={"source_snapshots": unrelated})})
    with pytest.raises(RunRefused) as err:
        m.run({"plan": plan.model_dump(mode="json")}, by="staff")
    assert "graph:" in err.value.message and "not pinned" in err.value.message


def test_r2_f3_a_duplicated_pin_is_refused_and_a_covering_pin_set_runs(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    good = pinned(invoice_plan(), m)
    assert good.temporal.source_snapshots, "non-vacuous"
    dup = good.model_copy(update={"temporal": good.temporal.model_copy(update={
        "source_snapshots": good.temporal.source_snapshots * 2})})
    with pytest.raises(RunRefused) as err:
        m.run({"plan": dup.model_dump(mode="json")}, by="staff")
    assert "pinned 2 times" in err.value.message
    assert m.run({"plan": good.model_dump(mode="json")}, by="staff")["rows"]["emitted"] > 0


# =================================================================================================
# Finding 4 — the extracted text is authenticated, and the capture must name the document
# =================================================================================================
def _invoice_amount_pointer(reader):
    read = reader.read("invoice", ["amount"])
    return next(r["amount"].pointer for r in read.rows if isinstance(r["amount"], GroundedValue))


def test_r2_f4_an_edited_extracted_text_does_not_resolve(ws_np, vocab):
    from databridge_adapter import Verdict

    ptr = _invoice_amount_pointer(_reader(ws_np, vocab))
    assert _reader(ws_np, vocab).verify(ptr) == Verdict.VERIFIED_AS_OF_CAPTURE, "non-vacuous"
    txt = ws_np.capture / ptr.capture_digest[:2] / f"{ptr.capture_digest}.txt"
    txt.write_text(txt.read_text(encoding="utf-8").replace("net 30", "net 90"), encoding="utf-8")
    fresh = _reader(ws_np, vocab)
    assert fresh.resolve(ptr) is None
    assert fresh.verify(ptr) == Verdict.UNVERIFIABLE


@pytest.mark.parametrize("edit", ["retitled", "undigested"])
def test_r2_f4_capture_metadata_that_is_edited_or_unanchored_is_not_trusted(ws_np, vocab, edit):
    ptr = _invoice_amount_pointer(_reader(ws_np, vocab))
    meta = ws_np.capture / ptr.capture_digest[:2] / f"{ptr.capture_digest}.json"
    doc = json.loads(meta.read_text(encoding="utf-8"))
    if edit == "retitled":
        doc["title"] = "something else"                     # digest no longer matches
    else:
        doc.pop("meta_digest")                               # a legacy, unanchored record
    meta.write_text(json.dumps(doc), encoding="utf-8")
    assert _reader(ws_np, vocab).resolve(ptr) is None


def test_r2_f4_text_and_passages_tampered_consistently_are_excluded(ws, vocab):
    base = _reader(ws, vocab, system_as_of=_later())
    name = _passage_entity(base)
    rows = [r for r in base.read(name, ["content"]).rows if isinstance(r["content"], GroundedValue)]
    digest = rows[0]["content"].pointer.capture_digest
    txt = ws.capture / digest[:2] / f"{digest}.txt"
    text = txt.read_text(encoding="utf-8")
    forged = text.replace("i", "!")                         # same length: offsets still fit
    assert forged != text and len(forged) == len(text)
    txt.write_text(forged, encoding="utf-8", newline="")
    conn = sqlite3.connect(_passage_store(ws))
    for chunk_id, cs, ce in conn.execute(
            "SELECT c.chunk_id, c.char_start, c.char_end FROM chunks c JOIN documents d "
            "USING(doc_id) WHERE d.sha256 = ?", (digest,)).fetchall():
        conn.execute("UPDATE chunks SET content = ? WHERE chunk_id = ?", (forged[cs:ce], chunk_id))
    conn.commit()
    conn.close()
    reader = _reader(ws, vocab, system_as_of=_later())
    read = reader.read(name, ["content"])
    assert read.rows, "non-vacuous: the other document's passages remain"
    assert all(r["content"].pointer.capture_digest != digest for r in read.rows
               if isinstance(r["content"], GroundedValue))
    assert any("does not re-derive" in n for n in reader.notes), reader.notes


def test_r2_f4_a_passage_whose_capture_names_another_document_is_excluded(ws, vocab):
    base = _reader(ws, vocab, system_as_of=_later())
    name = _passage_entity(base)
    n_before = len(base.read(name, ["content"]).rows)
    conn = sqlite3.connect(_passage_store(ws))
    doc_id = conn.execute("SELECT doc_id FROM documents ORDER BY doc_id LIMIT 1").fetchone()[0]
    forged = hashlib.sha256(b"another document").hexdigest()
    conn.execute("UPDATE documents SET doc_id = ? WHERE doc_id = ?", (forged, doc_id))
    conn.execute("UPDATE chunks SET doc_id = ? WHERE doc_id = ?", (forged, doc_id))
    conn.commit()
    conn.close()
    reader = _reader(ws, vocab, system_as_of=_later())
    read = reader.read(name, ["content", "doc_id"])
    assert 0 < len(read.rows) < n_before
    assert all(r["doc_id"].value != forged for r in read.rows)
    assert reader.passages_unbound > 0


# =================================================================================================
# Finding 7 — facts cannot expose a direct identifier under suppression
# =================================================================================================
def test_r2_f7_a_differently_spelled_identifier_and_a_contact_valued_column_are_withheld(
        ws_np, vocab):
    rows = [(i, f"E-{i:03d}", f"person{i}@larkspur.example", f"note {i}") for i in range(1, 5)]
    _facts_store(ws_np.facts, "hr", "staff", [("row_no", "numeric"), ("Employee-ID", "text"),
                                              ("contact", "text"), ("note", "text")], rows)
    reader = _reader(ws_np, vocab)
    read = reader.read("facts.hr.staff", ["row_no", "Employee-ID", "contact", "note"])
    assert len(read.rows) == 4
    for r in read.rows:
        assert isinstance(r["Employee-ID"], Withheld), "a declared identifier, differently spelled"
        assert isinstance(r["contact"], Withheld), "values match the contact-identifier screen"
        assert isinstance(r["note"], GroundedValue), "non-vacuous: a plain text column is kept"
    assert any("contact-identifier screen" in n for n in reader.notes)
    retained = _reader(ws_np, vocab, identifiers="retain").read(
        "facts.hr.staff", ["row_no", "contact"])
    assert all(isinstance(r["contact"], GroundedValue) for r in retained.rows), (
        "retain is the (separately authorised) decision that discloses it")


# =================================================================================================
# Finding 8 — discovery never silently overwrites a name collision
# =================================================================================================
def test_r2_f8_two_facts_stores_yielding_one_entity_name_are_both_excluded(ws_np, vocab):
    _facts_store(ws_np.facts, "acme.eu", "orders", [("id", "numeric"), ("v", "numeric")],
                 [(1, 10), (2, 20)])
    _facts_store(ws_np.facts, "acme", "eu.orders", [("id", "numeric"), ("v", "numeric")],
                 [(1, 99), (2, 98)])
    reader = _reader(ws_np, vocab)
    name = "facts.acme.eu.orders"
    assert name not in reader.entities(), "an ambiguous name is offered to neither claimant"
    dupes = [x for x in reader.excluded() if x["entity"] == name and "duplicate" in x["reason"]]
    assert sorted(x["claimant"] for x in dupes) == ["facts:acme.eu/orders", "facts:acme/eu.orders"]


# =================================================================================================
# Finding 9 — a facts read never points the process environment at its own root
# =================================================================================================
def test_r2_f9_a_facts_read_does_not_touch_the_process_environment(ws_np, vocab, tmp_path,
                                                                   monkeypatch):
    from databridge_mcp_ingest.tabular import count as cq

    elsewhere = tmp_path / "someone-elses-facts"
    elsewhere.mkdir()
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(elsewhere))
    seen: list = []
    real = cq.count_query

    def spy(*a, **kw):
        seen.append(os.environ.get("WALNUT_FACTS_DIR"))
        return real(*a, **kw)

    monkeypatch.setattr(cq, "count_query", spy)
    reader = _reader(ws_np, vocab)
    name = next(n for n, e in reader.entities().items() if e.origin == "facts")
    assert reader.read(name, ["paid_amount"]).rows, "non-vacuous: the reader's own root is read"
    assert all(v == str(elsewhere) for v in seen), seen
    assert os.environ["WALNUT_FACTS_DIR"] == str(elsewhere)


# =================================================================================================
# Finding 10 — a failed or missing reverse-index registration is visible and recoverable
# =================================================================================================
class _Sink:
    def __init__(self, fail: bool):
        self.fail, self.calls = fail, []

    def record_emission(self, kind, ref, pointers, **kw):
        self.calls.append(ref)
        if self.fail:
            raise RuntimeError("index is down")
        return len(self.calls)


class _StoreProxy:
    def __init__(self, store, sink):
        self._store, self.reverse_index = store, sink

    def __getattr__(self, name):
        return getattr(self._store, name)


def test_r2_f10_a_failed_registration_is_recovered_by_an_identical_rerun(ws_np, vocab, tmp_path):
    out = tmp_path / "out"
    down = _Sink(fail=True)
    m = _materialiser(ws_np, vocab, out, store=_StoreProxy(ws_np.store, down))
    body = {"plan": pinned(invoice_plan(), m).model_dump(mode="json")}
    first = m.run(body, by="staff")
    assert first["index_registered"]["registered"] is False
    assert m.index_registration(first["plan_hash"])["registered"] is False
    listed = {r["plan_hash"]: r for r in m.receipts(by="staff")}
    assert listed[first["plan_hash"]]["index_registration"]["registered"] is False

    up = _Sink(fail=False)
    m2 = _materialiser(ws_np, vocab, out, store=_StoreProxy(ws_np.store, up))
    second = m2.run(body, by="staff")
    assert second["reused_prior_run"] is True
    assert up.calls == [first["plan_hash"]]
    assert second["index_registered"]["registered"] is True
    assert m2.index_registration(first["plan_hash"])["registered"] is True
    record = json.loads((out / first["plan_hash"] / "index_registration.json").read_text())
    assert [a["registered"] for a in record["attempts"]] == [False, True]


def test_r2_f10_a_run_published_without_any_registration_says_so_and_recovers(ws_np, vocab,
                                                                              tmp_path):
    out = tmp_path / "out"
    m = _materialiser(ws_np, vocab, out, register_emissions=False)   # as if it died after rename
    body = {"plan": pinned(invoice_plan(), m).model_dump(mode="json")}
    first = m.run(body, by="staff")
    assert m.index_registration(first["plan_hash"])["registered"] is None
    up = _Sink(fail=False)
    _materialiser(ws_np, vocab, out, store=_StoreProxy(ws_np.store, up)).run(body, by="staff")
    assert up.calls == [first["plan_hash"]]
    assert m.index_registration(first["plan_hash"])["registered"] is True
