"""R-021 — the first Codex review of the materialisation wiring (f1), closed finding by finding.

Every test here failed before its fix. Each builds its own synthetic workspace in ``tmp_path``
(`databridge_materialise.testing`) when it mutates a store, so no test can poison another's.
Offline throughout: no model, no network.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from databridge_core import GroundedValue, Withheld
from databridge_graph.packs import deployment

from databridge_materialise.agent import AuthoredPlan, DataAgent, gold_harness
from databridge_materialise.errors import SourceCannotReconstructInstant
from databridge_materialise.platform import (
    PLATFORM_BACKEND,
    PlatformMaterialiser,
    RunRefused,
    build_catalogue,
    pointer_check,
)
from databridge_materialise.platform_reader import PlatformReader
from databridge_materialise.receipt import CellCounts
from databridge_materialise.testing import (
    BUILD_TIME,
    build_workspace,
    invoice_plan,
    passages_corpus,
    person_plan,
    pinned,
)

FRAUD_REQUEST = "one row per invoice at issue time, label = overdue after 30 days"


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
    """No passages: faster, for tests about the graph and the facts store."""
    w = build_workspace(tmp_path / "ws", with_passages=False)
    yield w
    w.store.close()


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


def _facts_file(w):
    return next(w.facts.glob("*_facts.sqlite"))


def _touch_facts(w):
    """A real change to the facts store's bytes (a new row), as a re-extraction would make."""
    conn = sqlite3.connect(_facts_file(w))
    conn.execute("INSERT INTO payments VALUES ('PAY-999', 'INV-001', 1, 1, 'x@y.example')")
    conn.commit()
    conn.close()


def _pinned(plan, snapshots):
    return plan.model_copy(update={"temporal": plan.temporal.model_copy(
        update={"source_snapshots": tuple(snapshots)})})


def _passage_entity(reader):
    return next(n for n, e in reader.entities().items() if e.origin == "passages")


# =================================================================================================
# Finding 2 — a caller cannot authorise their own direct-identifier disclosure
# =================================================================================================
def test_f2_retain_is_refused_when_no_approver_is_configured(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": person_plan(pii="retain").model_dump(mode="json")}, by="staff")
    assert err.value.code == "IDENTIFIER_DISCLOSURE_NOT_AUTHORISED"
    assert not any((tmp_path / "out").glob("[0-9a-f]*")), "nothing published"


def test_f2_a_caller_approving_their_own_disclosure_is_refused(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out",
                      identifier_disclosure_approver=lambda **ctx: ctx["principal"])
    with pytest.raises(RunRefused) as err:
        m.run({"plan": person_plan(pii="retain").model_dump(mode="json")}, by="staff")
    assert err.value.code == "IDENTIFIER_DISCLOSURE_NOT_AUTHORISED"


def test_f2_a_separate_approver_authorises_retain_and_is_recorded(ws_np, vocab, tmp_path):
    seen = {}

    def approver(**ctx):
        seen.update(ctx)
        return "dpo-1"

    m = _materialiser(ws_np, vocab, tmp_path / "out", identifier_disclosure_approver=approver)
    result = m.run({"plan": pinned(person_plan(pii="retain"), m).model_dump(mode="json")},
                   by="staff")
    assert seen["principal"] == "staff" and seen["purpose"] == "analytics"
    doc = m.receipt(result["plan_hash"])
    assert doc["platform"]["identifiers"] == "retain"
    assert doc["platform"]["identifier_disclosure_approved_by"] == "dpo-1"


def test_f2_suppress_needs_no_approver(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    result = m.run({"plan": pinned(person_plan(), m).model_dump(mode="json")}, by="staff")
    assert result["rows"]["emitted"] > 0


# =================================================================================================
# Finding 3 — source pins are enforced
# =================================================================================================
def test_f3_a_pinned_plan_runs_while_its_sources_match(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    pins = m.reader(system_as_of=BUILD_TIME).snapshots()
    assert pins, "non-vacuous: the workspace has sources to pin"
    result = m.run({"plan": _pinned(invoice_plan(), pins).model_dump(mode="json")}, by="staff")
    doc = m.receipt(result["plan_hash"])
    assert doc["platform"]["sources"] == [s.model_dump(mode="json") for s in pins]


def test_f3_a_changed_pinned_source_refuses_the_same_plan_hash(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    pins = m.reader(system_as_of=BUILD_TIME).snapshots()
    body = {"plan": _pinned(invoice_plan(), pins).model_dump(mode="json")}
    first = m.run(body, by="staff")
    _touch_facts(ws_np)
    with pytest.raises(RunRefused) as err:
        m.run(body, by="staff")
    assert err.value.code == "SourcePinViolated"
    assert m.receipt(first["plan_hash"])["receipt"]["content_hash"] == first["content_hash"]


def test_f3_a_source_changing_during_a_plan_run_refuses_it(ws_np, vocab, tmp_path, monkeypatch):
    import databridge_materialise.platform as platform_mod

    real = platform_mod.materialise

    def mid_run_change(*a, **kw):
        out = real(*a, **kw)
        _touch_facts(ws_np)
        return out

    monkeypatch.setattr(platform_mod, "materialise", mid_run_change)
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    assert err.value.code == "SourcePinViolated"
    assert not any((tmp_path / "out").glob("[0-9a-f]*")), "nothing published"


def test_f3_a_source_changing_during_a_request_run_refuses_it(ws_np, vocab, tmp_path,
                                                              monkeypatch):
    real = DataAgent.materialise

    def mid_run_change(self, *a, **kw):
        out = real(self, *a, **kw)
        _touch_facts(ws_np)
        return out

    monkeypatch.setattr(DataAgent, "materialise", mid_run_change)
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"request": FRAUD_REQUEST}, by="staff")
    assert err.value.code == "SourcePinViolated"


# =================================================================================================
# Finding 4 — passage provenance: no unverifiable value ships; metadata is not a coordinate
# =================================================================================================
def test_f4_a_passage_whose_capture_is_missing_is_excluded_and_recorded(ws, vocab):
    base = _reader(ws, vocab, system_as_of=datetime.now(timezone.utc) + timedelta(days=1))
    name = _passage_entity(base)
    rows = base.read(name, ["content"]).rows
    digests = {r["content"].pointer.capture_digest for r in rows
               if isinstance(r["content"], GroundedValue)}
    gone = sorted(digests)[0]
    for p in ws.capture.glob(f"{gone[:2]}/{gone}*"):
        p.unlink()
    reader = _reader(ws, vocab, system_as_of=datetime.now(timezone.utc) + timedelta(days=1))
    read = reader.read(name, ["content", "title"])
    assert read.rows, "non-vacuous: the other document's passages remain"
    for r in read.rows:
        cell = r["content"]
        if isinstance(cell, GroundedValue):
            assert cell.pointer.capture_digest != gone
            assert reader.resolve(cell.pointer) is not None
    assert any("capture" in n and "excluded" in n for n in reader.notes), reader.notes


def test_f4_a_passage_title_is_not_passed_off_as_a_coordinate(ws, vocab):
    reader = _reader(ws, vocab, system_as_of=datetime.now(timezone.utc) + timedelta(days=1))
    read = reader.read(_passage_entity(reader), ["title", "chunk_id"])
    assert read.rows
    assert not any(isinstance(r["title"], GroundedValue) for r in read.rows), (
        "a title is not what the chunk-body span says; it needs its own pointer or is Absent")
    # Round 2 (Finding 1): a coordinate cites the STORE RECORD it is a column of, and re-reads
    # to exactly its value — there is no resolve-only "structural" pass any more.
    for r in read.rows:
        ptr = r["chunk_id"].pointer
        assert (ptr.kind, ptr.table, ptr.column, ptr.row_id) == (
            "table_cell", "chunks", "chunk_id", r["chunk_id"].value)
        assert reader.cell_check(r["chunk_id"]) == (True, True)


def test_f4_a_failed_pointer_check_blocks_publication(ws_np, vocab, tmp_path, monkeypatch):
    monkeypatch.setattr(PlatformReader, "cell_check",
                        lambda self, cell: (False, False))
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    assert err.value.code == "POINTER_CHECK_FAILED"
    assert not list((tmp_path / "out").iterdir()), "no run directory and no staging left behind"


# =================================================================================================
# Finding 6 — an agent-shaped receipt describes the SHAPED dataset
# =================================================================================================
def test_f6_the_agent_receipt_is_measured_on_the_shaped_rectangle(ws_np, vocab):
    reader = _reader(ws_np, vocab)
    catalogue = build_catalogue(reader, principal="staff")
    agent = DataAgent(gold_harness(), catalogue, build_time=BUILD_TIME,
                      snapshots=reader.snapshots(), backend=PLATFORM_BACKEND)
    authored = agent.author(FRAUD_REQUEST)
    plain = agent.materialise(FRAUD_REQUEST, reader, authored=authored)
    droppable = [c for c in plain.dataset.columns
                 if c != authored.plan.shape.label.path
                 and c not in (authored.plan.shape.columns[0].path,)
                 and any(isinstance(row[plain.dataset.index(c)], GroundedValue)
                         for row in plain.dataset.rows)]
    assert droppable, "non-vacuous: a grounded, non-key, non-label column exists to drop"
    directed = AuthoredPlan(plan=authored.plan, draft=authored.draft.model_copy(
        update={"shaping": [*authored.draft.shaping, f"drop:{droppable[-1]}"]}))
    built = agent.materialise(FRAUD_REQUEST, reader, authored=directed)
    ds, rc = built.dataset, built.receipt
    assert droppable[-1] not in ds.columns
    assert rc.cells == CellCounts(**ds.cell_counts())
    assert rc.pointer_manifest.entries == len(ds.pointers())
    assert rc.pointer_manifest.entries < plain.receipt.pointer_manifest.entries
    assert rc.columns_fully_absent == ds.fully_absent_columns()
    assert rc.content_hash == ds.content_hash()


# =================================================================================================
# Finding 7 — the reverse index registers the FINAL pointer set, after publication, both pathways
# =================================================================================================
class _Recorder:
    def __init__(self, out):
        self.out, self.calls = out, []

    def record_emission(self, kind, ref, pointers, **kw):
        pointers = list(pointers)
        self.calls.append({"kind": kind, "ref": ref, "n": len(pointers),
                           "published": (self.out / ref / "receipt.json").is_file(), **kw})
        return len(self.calls)


class _StoreProxy:
    def __init__(self, store, sink):
        self._store, self.reverse_index = store, sink

    def __getattr__(self, name):
        return getattr(self._store, name)


@pytest.mark.parametrize("body", ["plan", "request"])
def test_f7_registration_is_after_publication_and_cites_the_shipped_pointers(ws_np, vocab,
                                                                            tmp_path, body):
    out = tmp_path / "out"
    sink = _Recorder(out)
    m = _materialiser(ws_np, vocab, out, store=_StoreProxy(ws_np.store, sink))
    payload = ({"plan": pinned(invoice_plan(), m).model_dump(mode="json")} if body == "plan"
               else {"request": FRAUD_REQUEST})
    result = m.run(payload, by="staff")
    assert len(sink.calls) == 1, sink.calls
    call = sink.calls[0]
    assert call["ref"] == result["plan_hash"] and call["published"] is True
    doc = m.receipt(result["plan_hash"])
    assert call["n"] == doc["receipt"]["pointer_manifest"]["entries"] > 0


# =================================================================================================
# Finding 8 — a prior run is never deleted
# =================================================================================================
def test_f8_an_identical_rerun_keeps_the_first_receipt_byte_for_byte(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    body = {"plan": pinned(invoice_plan(), m).model_dump(mode="json")}
    first = m.run(body, by="staff")
    path = tmp_path / "out" / first["plan_hash"] / "receipt.json"
    before = path.read_bytes()
    second = m.run(body, by="staff")
    assert second["content_hash"] == first["content_hash"]
    assert path.read_bytes() == before, "the prior receipt was overwritten"
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".staging")]


def test_f8_a_different_dataset_under_an_existing_plan_hash_is_refused(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    body = {"plan": pinned(invoice_plan(), m).model_dump(mode="json")}
    first = m.run(body, by="staff")
    path = tmp_path / "out" / first["plan_hash"] / "receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["receipt"]["content_hash"] = "0" * 64           # an earlier, different run
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RunRefused) as err:
        m.run(body, by="staff")
    assert err.value.code == "CONTENT_COLLISION"
    assert path.read_bytes() == before


# =================================================================================================
# Finding 9 — capture digests are validated and verified before any path use
# =================================================================================================
@pytest.mark.parametrize("digest", ["../../etc/passwd", "..\\..\\target", "ab" * 31 + "ZZ",
                                    "AB" * 32, "ab" * 33])
def test_f9_a_malformed_digest_never_reaches_the_filesystem(ws_np, vocab, digest):
    assert _reader(ws_np, vocab)._capture(digest) is None


def test_f9_a_traversal_digest_cannot_read_a_file_outside_the_capture_root(ws_np, vocab):
    # `<capture>/<digest[:2]>/<digest>.txt` with digest "../outside" is <capture>/../../outside.txt
    outside = ws_np.capture.parent.parent / "outside.txt"
    outside.write_text("Invoice INV-001 for amount 999999", encoding="utf-8")
    assert _reader(ws_np, vocab)._capture("../outside") is None


def test_f9_a_tampered_capture_does_not_verify(ws_np, vocab):
    from databridge_adapter import Verdict

    reader = _reader(ws_np, vocab)
    read = reader.read("invoice", ["amount"])
    ptr = next(r["amount"].pointer for r in read.rows if isinstance(r["amount"], GroundedValue))
    assert reader.verify(ptr) == Verdict.VERIFIED_AS_OF_CAPTURE
    blob = ws_np.capture / ptr.capture_digest[:2] / ptr.capture_digest
    blob.write_bytes(blob.read_bytes() + b" tampered")
    assert _reader(ws_np, vocab).verify(ptr) == Verdict.UNVERIFIABLE


# =================================================================================================
# Finding 10 — the temporal cut is enforced in the reader, for facts and passages alike
# =================================================================================================
def test_f10_a_passage_ingested_after_the_cut_is_not_read(ws, vocab):
    reader = _reader(ws, vocab, system_as_of=BUILD_TIME - timedelta(days=3650))
    assert reader.read(_passage_entity(reader), ["content"]).rows == ()
    later = _reader(ws, vocab, system_as_of=datetime.now(timezone.utc) + timedelta(days=1))
    assert later.read(_passage_entity(later), ["content"]).rows, "non-vacuous"


def test_f10_a_whole_file_facts_store_cannot_answer_an_earlier_instant(ws_np, vocab):
    reader = _reader(ws_np, vocab, system_as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    name = next(n for n, e in reader.entities().items() if e.origin == "facts")
    with pytest.raises(SourceCannotReconstructInstant):
        reader.read(name, ["paid_amount"])
    assert _reader(ws_np, vocab).read(name, ["paid_amount"]).rows, "non-vacuous at BUILD_TIME"


def test_f10_a_corpus_plan_is_read_at_its_corpus_instant_and_inconsistent_cuts_refuse(
        ws, vocab, tmp_path):
    cut = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    plan = passages_corpus(corpus_as_of=cut)
    bad = plan.model_copy(update={"temporal": plan.temporal.model_copy(
        update={"build_time": cut - timedelta(days=30)})})
    m = _materialiser(ws, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": bad.model_dump(mode="json")}, by="staff")
    assert err.value.code == "PLAN_TEMPORAL_INCONSISTENT"
    ok = m.run({"plan": pinned(plan, m).model_dump(mode="json")}, by="staff")
    assert m.receipt(ok["plan_hash"])["platform"]["system_as_of"] == cut.isoformat()


# =================================================================================================
# Finding 11 — discovery records every source it rejects
# =================================================================================================
def test_f11_a_configured_facts_source_that_is_missing_is_recorded(ws_np, vocab):
    reader = _reader(ws_np, vocab, facts_sources=["larkspur-db", "no-such-db"])
    reasons = [x for x in reader.excluded() if "no-such-db" in x["entity"]]
    assert reasons and reasons[0]["origin"] == "facts"


def test_f11_a_non_passage_file_in_the_passage_directory_is_recorded(ws_np, vocab):
    (ws_np.passages / "garbage.sqlite").write_bytes(b"not a database at all")
    reasons = [x for x in _reader(ws_np, vocab).excluded() if "garbage.sqlite" in x["entity"]]
    assert reasons and reasons[0]["origin"] == "passages"


def test_f11_two_passage_stores_claiming_one_entity_name_are_both_reported(ws, vocab):
    import shutil

    src = next(p for p in ws.passages.iterdir() if p.suffix == ".sqlite")
    shutil.copy(src, ws.passages / f"copy-{src.name}")
    reader = _reader(ws, vocab)
    dupes = [x for x in reader.excluded() if x.get("origin") == "passages"
             and "duplicate" in x["reason"]]
    assert len(dupes) == 2, reader.excluded()
    assert dupes[0]["entity"] == dupes[1]["entity"]
    assert dupes[0]["entity"] not in reader.entities(), "an ambiguous name is not offered"


# =================================================================================================
# gateway items handed over by the Data page fix (R-021-data-page #1, #5)
# =================================================================================================
def test_file_roles_and_a_server_side_plan_hash_check(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    result = m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    roles = result["file_roles"]
    assert set(roles) == set(result["files"])
    assert roles["receipt.json"] == "receipt" and roles["plan.yaml"] == "plan"
    assert [f for f, r in roles.items() if r == "dataset"], "non-vacuous: a dataset file"
    check = m.plan_verification(result["plan_hash"])
    assert check == {"plan_hash": result["plan_hash"], "recomputed": result["plan_hash"],
                     "matches": True}
    plan_file = tmp_path / "out" / result["plan_hash"] / "plan.yaml"
    plan_file.write_text(plan_file.read_text(encoding="utf-8").replace("xgboost", "lightgbm"),
                         encoding="utf-8")
    assert m.plan_verification(result["plan_hash"])["matches"] is False


def test_a_dataset_file_is_not_served_after_a_failed_pointer_check(ws_np, vocab, tmp_path):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    result = m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    data = next(f for f, r in result["file_roles"].items() if r == "dataset")
    assert m.file_path(result["plan_hash"], data) is not None
    path = tmp_path / "out" / result["plan_hash"] / "receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["platform"]["pointer_check"]["all_match"] = False         # a legacy run on disk
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert m.file_path(result["plan_hash"], data) is None
    assert m.file_path(result["plan_hash"], "receipt.json") is not None


# =================================================================================================
# Data-page hand-over #2 — the leakage check gates publication and download like the pointer check
# =================================================================================================
@pytest.mark.parametrize("leak", [
    None,                                                             # missing
    {"passed": True, "threshold": 0.05},                              # incomplete
    {"passed": False, "max_class_delta_observed": 0.4, "threshold": 0.05},
    {"passed": True, "max_class_delta_observed": 0.4, "threshold": 0.05},   # contradicts itself
])
def test_a_dataset_is_not_served_unless_its_leak_check_clears_it(ws_np, vocab, tmp_path, leak):
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    result = m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    data = next(f for f, r in result["file_roles"].items() if r == "dataset")
    assert m.file_path(result["plan_hash"], data) is not None, "non-vacuous: served before"
    path = tmp_path / "out" / result["plan_hash"] / "receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["receipt"]["missingness_leak_check"] = leak
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert m.file_path(result["plan_hash"], data) is None
    assert "leak check" in m.file_withheld(result["plan_hash"], data)
    assert m.file_path(result["plan_hash"], "plan.yaml") is not None


def test_a_self_contradicting_leak_check_blocks_publication(ws_np, vocab, tmp_path, monkeypatch):
    import databridge_materialise.platform as platform_mod
    from databridge_materialise.receipt import LeakCheckResult

    real = platform_mod.materialise

    def contradicting(*a, **kw):
        built = real(*a, **kw)
        bad = LeakCheckResult(max_class_delta_observed=0.9, threshold=0.05, passed=True)
        return type(built)(compiled=built.compiled, dataset=built.dataset,
                           receipt=built.receipt.model_copy(
                               update={"missingness_leak_check": bad}))

    monkeypatch.setattr(platform_mod, "materialise", contradicting)
    m = _materialiser(ws_np, vocab, tmp_path / "out")
    with pytest.raises(RunRefused) as err:
        m.run({"plan": pinned(invoice_plan(), m).model_dump(mode="json")}, by="staff")
    assert err.value.code == "LEAK_CHECK_FAILED"
    assert not list((tmp_path / "out").iterdir()), "nothing published"
