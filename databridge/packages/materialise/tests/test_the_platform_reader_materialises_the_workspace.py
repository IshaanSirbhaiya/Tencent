"""Layer 3 on the knowledge platform: the materialiser reads the shared graph, the facts stores and
the passage stores through `PlatformReader` (ROADMAP Phase F).

Everything runs on a synthetic workspace built in ``tmp_path`` (`databridge_materialise.testing`),
offline: the data agent's "model" is a `FakeBackend` over the deterministic gold planner. What is
proven:

* the catalogue is COMPUTED from the stores and the bound vocabulary — kinds with rows, attributes
  seen, facts tables, passage stores — never listed;
* the two built shapes (tabular, chunk_corpus) materialise with EVERY grounded cell's pointer
  re-read from its source and saying the value; the two unbuilt ones refuse by name;
* re-running a plan reproduces ``content_hash``;
* the leakage compiler still refuses a leaky plan over the new reader, before anything is read;
* a subject's identifiers are withheld under the default policy and disclosed only when the plan
  says ``retain``; identity is never resolved;
* a masked passage is withheld as an O1b transformed disclosure, not emitted under a verbatim
  pointer;
* the as-of read admits exactly the versions believed at the reader's system instant;
* an English fraud-style request compiles and runs through the data agent.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from databridge_core import Absent, GroundedValue, Withheld
from databridge_graph.packs import deployment
from databridge_harness import FakeBackend, Harness

from databridge_materialise import (
    FeatureAxisRefused,
    MaturityLagMissing,
    ShapeNotImplemented,
    gold_planner,
    materialise,
)
from databridge_materialise.platform import (
    PlatformMaterialiser,
    build_catalogue,
    pointer_check,
    shape_status,
)
from databridge_materialise.platform_reader import (
    IDENTIFIER_REASON,
    TRANSFORMED_REASON,
    PlatformReader,
)
from databridge_materialise.testing import (
    BUILD_TIME,
    build_workspace,
    invoice_plan,
    pinned,
    passages_corpus,
    person_plan,
)

FRAUD_REQUEST = "one row per invoice at issue time, label = overdue after 30 days"


@pytest.fixture(scope="module")
def vocab():
    with deployment("workspace") as v:
        yield v


@pytest.fixture(scope="module")
def ws(vocab, tmp_path_factory):
    w = build_workspace(tmp_path_factory.mktemp("workspace"))
    yield w
    w.store.close()


def _reader(ws, vocab, **kw):
    kw.setdefault("system_as_of", BUILD_TIME)
    kw.setdefault("principal", "staff")
    return PlatformReader(store=ws.store, vocabulary=vocab, facts_root=ws.facts,
                          passages_root=ws.passages, capture_root=ws.capture, **kw)


def _materialiser(ws, vocab, tmp_path, **kw):
    return PlatformMaterialiser(store_provider=lambda: ws.store, vocabulary=vocab,
                                facts_root=ws.facts, passages_root=ws.passages,
                                capture_root=ws.capture, out_root=tmp_path / "out",
                                clock=lambda: BUILD_TIME, **kw)


def _corpus_instant():
    # The passage store stamps `ingested_at` with the real clock, so the corpus instant is taken
    # after the build rather than pinned to BUILD_TIME.
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)


def _all_pointers_resolve(dataset, reader, plan=None):
    check = pointer_check(dataset, reader, plan=plan)
    assert check["grounded_cells"] > 0, "a pointer check over zero cells proves nothing"
    assert check["all_resolved"] and check["all_match"], check["failures"]
    return check


# ------------------------------------------------------------------------------------------
# the catalogue
# ------------------------------------------------------------------------------------------
def test_the_catalogue_is_computed_from_the_stores_and_the_vocabulary(ws, vocab):
    reader = _reader(ws, vocab)
    ents = reader.entities()
    kinds_with_rows = {r["kind"] for r in ws.store.fetchall(
        "SELECT DISTINCT kind FROM nodes WHERE system_to IS NULL")}
    graph = {n for n, e in ents.items() if e.origin == "graph"}
    assert graph == kinds_with_rows & set(vocab.node_kinds)
    assert graph, "non-vacuous: the workspace has graph kinds"
    assert {e.origin for e in ents.values()} == {"graph", "facts", "passages"}

    invoice = ents["invoice"]
    stored_attrs = {k for r in ws.store.fetchall("SELECT attrs_json FROM nodes WHERE kind = ?",
                                                 ("invoice",))
                    for k in __import__("json").loads(r["attrs_json"])}
    assert stored_attrs <= set(invoice.fields)
    assert "billed_to_id" in invoice.fields, "a functional edge becomes a join column"
    assert invoice.system_time_field == "_system_from"

    person = ents["person"]
    assert person.subject is vocab.is_subject("person") is True
    assert set(vocab.identifiers_for("person")) <= set(person.identifier_fields)

    catalogue = build_catalogue(reader, principal="staff")
    assert {e.name for e in catalogue.entities} == set(ents)


# ------------------------------------------------------------------------------------------
# the built shapes
# ------------------------------------------------------------------------------------------
def test_a_tabular_plan_materialises_with_every_pointer_resolving(ws, vocab, tmp_path):
    reader = _reader(ws, vocab)
    built = materialise(invoice_plan(), reader, out_dir=tmp_path, verify_sample=10_000)
    assert built.receipt.rows.emitted > 0
    # Every invoice is accounted for once, under one reason (E2E run 4 added `unlabelled`: the
    # window closed with no outcome in it — counted apart from maturity).
    assert built.receipt.rows.considered == len(ws.invoices)
    _all_pointers_resolve(built.dataset, reader)
    # every sampled pointer verified; nothing unverifiable slipped through as a pass
    assert built.receipt.verdicts and "drift_material" not in built.receipt.verdicts
    assert (tmp_path / f"{built.plan.output_stem}.jsonl").is_file()
    assert (tmp_path / f"{built.plan.output_stem}.croissant.json").is_file()


def test_features_are_cut_on_system_time_per_row(ws, vocab):
    """The prior-amount feature only ever uses an invoice the platform had RECORDED by T_d."""
    built = materialise(invoice_plan(), _reader(ws, vocab))
    cols = built.dataset.columns
    by_id = {i["id"]: i for i in ws.invoices}
    checked = 0
    for row in built.dataset.rows:
        me = by_id[row[cols.index("invoice._id")].value]
        prior = row[cols.index("invoice.prior_amount_same_customer")]
        admissible = [i for i in ws.invoices if i["org"] == me["org"]
                      and i["recorded"] <= me["issued"] and i["issued"] < me["issued"]]
        if admissible:
            want = max(admissible, key=lambda i: i["issued"])["amount"]
            assert isinstance(prior, GroundedValue) and prior.value == want
            checked += 1
        else:
            assert isinstance(prior, Absent)
        if "invoice.amount" in cols:
            own = row[cols.index("invoice.amount")]
            # an invoice is recorded AFTER it is issued, so its own amount was not known at T_d
            assert isinstance(own, Absent) if me["recorded"] > me["issued"] else True
    assert checked, "non-vacuous: some rows have an admissible prior"
    # E2E run 4 (Q-07): a feature the cut empties on EVERY emitted row is refused by name rather
    # than shipped as an all-null column. Derived from the workspace, not written down.
    emitted = [by_id[row[cols.index("invoice._id")].value] for row in built.dataset.rows]
    all_cut = bool(emitted) and all(i["recorded"] > i["issued"] for i in emitted)
    refused = [r for r in built.receipt.columns_refused if r.startswith("invoice.amount:")]
    assert ("invoice.amount" not in cols) == all_cut == bool(refused)
    # ...and the guard against over-refusal: a feature grounded on SOME emitted row stays a column
    assert "invoice.prior_amount_same_customer" in cols
    assert not any(r.startswith("invoice.prior_amount_same_customer:")
                   for r in built.receipt.columns_refused)


def test_a_chunk_corpus_over_the_passage_store_reslices(ws, vocab):
    reader = _reader(ws, vocab)
    built = materialise(passages_corpus(corpus_as_of=_corpus_instant()), reader)
    assert built.receipt.rows.emitted > 0
    # The corpus's coordinates are matched against what each chunk pointer encodes, which needs
    # the plan's chunking rule (R-021 round 2, Finding 1).
    _all_pointers_resolve(built.dataset, reader, plan=built.plan)


def test_a_corpus_before_anything_was_ingested_is_empty_not_backfilled(ws, vocab):
    early = datetime(2020, 1, 1, tzinfo=timezone.utc)
    built = materialise(passages_corpus(corpus_as_of=early),
                        _reader(ws, vocab, system_as_of=early))
    assert built.dataset.n_rows == 0


def test_only_the_shapes_with_builders_are_offered_and_the_rest_refuse_by_name(ws, vocab):
    status = {s["shape"]: s for s in shape_status()}
    assert status["tabular"]["built"] and status["chunk_corpus"]["built"]
    for kind in ("graph_tensor", "conversational"):
        assert status[kind]["built"] is False and status[kind]["reason"]
        with pytest.raises(ShapeNotImplemented):
            materialise(invoice_plan(kind=kind), _reader(ws, vocab))


# ------------------------------------------------------------------------------------------
# identity: reproducible, leak-refusing
# ------------------------------------------------------------------------------------------
def test_re_running_the_same_plan_reproduces_the_content_hash(ws, vocab):
    plan = invoice_plan()
    first = materialise(plan, _reader(ws, vocab))
    second = materialise(plan, _reader(ws, vocab))
    assert first.receipt.content_hash == second.receipt.content_hash
    assert first.receipt.plan_hash == second.receipt.plan_hash
    assert first.receipt.reproducible


@pytest.mark.parametrize("broken, error", [
    (dict(feature_axis="valid_time"), FeatureAxisRefused),
    (dict(maturity_lag=None), MaturityLagMissing),
])
def test_the_compiler_still_refuses_a_leaky_plan_over_the_platform_reader(ws, vocab, tmp_path,
                                                                          broken, error):
    with pytest.raises(error):
        materialise(invoice_plan(**broken), _reader(ws, vocab), out_dir=tmp_path / "o")
    assert not (tmp_path / "o").exists(), "refused before anything was written"


# ------------------------------------------------------------------------------------------
# identity of people: suppressed by default, never resolved
# ------------------------------------------------------------------------------------------
def test_a_subject_identifier_is_withheld_under_the_default_policy(ws, vocab):
    built = materialise(person_plan(), _reader(ws, vocab, identifiers="suppress"))
    cols = built.dataset.columns
    idents = {f"person.{f}" for f in ("_id", "label", *vocab.identifiers_for("person"))}
    assert built.dataset.n_rows > 0, "non-vacuous: at least one person row is emitted"
    for row in built.dataset.rows:
        for c, cell in zip(cols, row):
            if c in idents:
                assert isinstance(cell, Withheld), c
                assert cell.record.reason_code == IDENTIFIER_REASON
                assert cell.record.principal == "staff"
            elif c == "person.title":
                assert isinstance(cell, GroundedValue)
    emitted = {str(c.value) for row in built.dataset.rows for c in row
               if isinstance(c, GroundedValue)}
    for p in ws.people:
        assert not {p["email"], p["employee_id"], p["name"], p["id"]} & emitted


def test_retain_is_a_plan_decision_and_discloses_with_pointers(ws, vocab):
    reader = _reader(ws, vocab, identifiers="retain")
    built = materialise(person_plan(pii="retain"), reader)
    col = built.dataset.columns.index("person.email")
    emails = [r[col] for r in built.dataset.rows]
    assert emails and all(isinstance(c, GroundedValue) for c in emails)
    assert {c.value for c in emails} <= {p["email"] for p in ws.people}
    _all_pointers_resolve(built.dataset, reader)


def test_a_facts_column_named_like_an_identifier_is_withheld(ws, vocab):
    read = _reader(ws, vocab).read("facts.larkspur-db.payments", ["email", "paid_amount"])
    assert read.rows and all(isinstance(r["email"], Withheld) for r in read.rows)
    assert all(isinstance(r["paid_amount"], GroundedValue) for r in read.rows)


# ------------------------------------------------------------------------------------------
# O1b and as-of
# ------------------------------------------------------------------------------------------
def test_a_masked_passage_is_withheld_as_a_transformed_disclosure(vocab, tmp_path):
    w = build_workspace(tmp_path / "ws")
    store = next(w.passages.glob("*.sqlite"))
    conn = sqlite3.connect(store)
    chunk_id, content = conn.execute("SELECT chunk_id, content FROM chunks LIMIT 1").fetchone()
    # what the per-chunk screen does to a match: a SAME-LENGTH mask over the span
    conn.execute("UPDATE chunks SET content = ? WHERE chunk_id = ?",
                 ("#" * 12 + content[12:], chunk_id))
    conn.commit()
    conn.close()
    reader = _reader(w, vocab)
    read = reader.read("passages.larkspur-files.default", ["content"])
    by_id = {r["chunk_id"].value: r for r in read.rows}
    assert isinstance(by_id[chunk_id]["content"], Withheld)
    assert by_id[chunk_id]["content"].record.reason_code == TRANSFORMED_REASON
    others = [r for cid, r in by_id.items() if cid != chunk_id]
    assert others and all(isinstance(r["content"], GroundedValue) for r in others)
    assert any("O1b" in n for n in reader.notes), "the withholding is disclosed, not silent"
    w.store.close()


def test_the_graph_is_read_as_of_the_system_instant(vocab, tmp_path):
    w = build_workspace(tmp_path / "ws", with_passages=False)
    invoices = sorted(w.invoices, key=lambda i: i["recorded"])
    cut = invoices[len(invoices) // 2]["recorded"]
    reader = _reader(w, vocab, system_as_of=cut)
    ids = {r["_id"].value for r in reader.read("invoice", ["_id"]).rows}
    assert ids == {i["id"] for i in w.invoices if i["recorded"] <= cut}
    assert 0 < len(ids) < len(w.invoices), "non-vacuous: the cut excludes something"
    w.store.close()


# ------------------------------------------------------------------------------------------
# the data agent against the live catalogue
# ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("harness", [None, "fake-model"])
def test_a_fraud_style_request_compiles_and_runs_through_the_data_agent(ws, vocab, tmp_path,
                                                                        harness):
    h = (Harness(FakeBackend(gold_planner), profile="cloud", cache=None)
         if harness else None)
    m = _materialiser(ws, vocab, tmp_path, harness=h)
    result = m.run({"request": FRAUD_REQUEST}, by="staff")
    assert result["shaped_by"] == "agent" and result["reproducible"] is False
    assert result["entity"] == "invoice" and result["shape"] == "tabular"
    assert result["rows"]["emitted"] > 0
    check = result["pointer_check"]
    assert check["grounded_cells"] > 0 and check["all_resolved"] and check["all_match"]
    assert result["produced_by"]["agent"] == "data_agent"
    doc = m.receipt(result["plan_hash"])
    assert doc["receipt"]["content_hash"] == result["content_hash"]
    assert any(f.endswith(".jsonl") for f in result["files"])
    assert "receipt.json" in result["files"] and "plan.yaml" in result["files"]


def test_the_plan_pathway_writes_a_reproducible_receipt(ws, vocab, tmp_path):
    m = _materialiser(ws, vocab, tmp_path)
    body = {"plan": pinned(invoice_plan(), m).model_dump(mode="json")}
    a = m.run(body, by="staff")
    b = m.run(body, by="staff")
    assert a["reproducible"] is True and a["shaped_by"] == "plan"
    assert a["content_hash"] == b["content_hash"] and a["plan_hash"] == b["plan_hash"]
    assert a["pointer_check"]["all_resolved"]
    assert {"receipt.json", "plan.yaml"} <= set(a["files"])
    assert any(f.endswith(".croissant.json") for f in a["files"])


def test_a_plan_runs_only_as_the_principal_it_names(ws, vocab, tmp_path):
    from databridge_materialise.platform import RunRefused

    m = _materialiser(ws, vocab, tmp_path)
    with pytest.raises(RunRefused) as err:
        m.run({"plan": invoice_plan(principal="someone-else").model_dump(mode="json")},
              by="staff")
    assert err.value.code == "PRINCIPAL_MISMATCH"
