"""E2E run 2 (2026-10-01), findings N-07, N-08 and N-09 — reproduced offline, then closed.

* **N-07** — no tabular dataset could be materialised from English. A model wrote
  ``relation: "self"`` (it had never been shown the closed set), and the offline planner then wrote
  a per-row plan with no decision instant — for the plain request, a ``graph_tensor``, because the
  request said "as the graph and facts hold them now".
* **N-08** — a materialised dataset shipped no per-cell pointers a user could download.
* **N-09** — "download withheld when a check fails" could not be exercised.

The run's catalogue (``RUN2_CATALOGUE``) is the entity list `GET /materialise/catalogue` returned
in that run (``var/e2e-20261001T0122/artefacts/http/0177_p3_mat_catalogue.json``), as INPUT. The
requests are the run's own, verbatim, plus paraphrases. Every expectation is derived from the rule
the code applies (which fields are instants, which the request names), not written down.
Offline throughout: the "model" is a `FakeBackend`, so nothing here depends on a live model.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import get_args

import pytest

from databridge_core import GroundedValue
from databridge_graph.packs import deployment
from databridge_harness import FakeBackend, Harness

from databridge_materialise import (
    FeatureAxisRefused,
    LabelAxisRefused,
    PlanDraftInvalid,
    PlanNeedsInput,
    ShapeNotImplemented,
    compile_plan,
)
from databridge_materialise.agent import (
    RELATION_MEANINGS,
    AgentCatalogue,
    DataAgent,
    EntityCatalogue,
    PlanDraft,
    built_shapes,
    gold_harness,
    normalise_draft,
    plan_schema_prompt,
)
from databridge_materialise.plan import ColumnType, Relation, ShapeKind
from databridge_materialise.platform import (
    POINTER_SIDECAR_SUFFIX,
    PlatformMaterialiser,
    RunRefused,
)
from databridge_materialise.testing import (
    BUILD_TIME,
    EPOCH,
    FILES_SOURCE,
    span,
    whole,
    withhold_published_run,
    write_capture,
)

# --- the run's requests, verbatim (artefacts/p3_mat.json, p3c_mat2.json) --------------------
RUN_PLAIN = ("One row per invoice with its customer, project, issue date, due date, net, VAT, "
             "total and status, as the graph and facts hold them now.")
RUN_LABELLED = ("One row per invoice at its issue date, with the customer, project, net and VAT "
                "amounts as features; the label is whether the invoice is still unpaid 30 days "
                "after it was issued.")
RUN_LEAKY = ("One row per invoice at its issue date; the label is whether the invoice is paid; "
             "include the invoice status as a feature.")
RUN_CHUNKS = "A chunk corpus of every passage indexed for search, for retrieval evaluation."

# --- paraphrases that NAME an outcome field the catalogue holds ------------------------------
NAMED_OUTCOME = ("One row per invoice at its issue date with net and VAT as features; the label "
                 "is the invoice status 30 days after issue.")
NAMED_OUTCOME_2 = "Predict invoice status 30 days after the issue date, one row per invoice."
NAMED_OUTCOME_LEAKY = ("One row per invoice at its issue date; the label is the invoice status "
                       "after 30 days; include the invoice status as a feature.")


def _graph(name, *attrs, links=()):
    return EntityCatalogue(
        name=name, key=("_id",), valid_time_field="_valid_from", system_time_field="_system_from",
        fields=("_id", "_valid_from", "_valid_to", "_system_from", *links, "label",
                "canonical_id", *attrs))


def _facts(table, key, *fields):
    return EntityCatalogue(name=f"facts.larkspur_db.{table}", key=(key,),
                           system_time_field="_extracted_at",
                           fields=(key, *fields, "_extracted_at"))


#: GET /materialise/catalogue in run 2, as input data (subset of entities; every field as served).
RUN2_CATALOGUE = AgentCatalogue(
    source_id="walnut-platform", packs=("workspace",), purpose="dataset_materialisation",
    lawful_basis="legitimate_interests", principal_actor="e2e2-analyst",
    entities=(
        _graph("invoice", "due_on", "issued_on", "net_gbp", "status", "total_gbp", "vat_gbp",
               links=("billed_to_id", "mentions_id", "part_of_id")),
        _graph("organisation", "city", "created_at", "domain", "name", "sector",
               links=("located_in_id", "part_of_id", "party_to_id")),
        _graph("project", "contract_ref", "end_date", "name", "start_date", "status",
               links=("part_of_id",)),
        _graph("document", "agent_run_id", "amount", "created_at", "created_by", "customer_id",
               "due_on", "invoice_no", "note", "notes", "region", "source_pointer", "title",
               "written_on"),
        EntityCatalogue(name="facts.larkspur_db.invoices", key=("invoice_no",),
                        system_time_field="_extracted_at",
                        feature_candidates=("net_gbp", "vat_gbp", "total_gbp"),
                        fields=("invoice_no", "customer_id", "project_id", "issued_on", "due_on",
                                "net_gbp", "vat_gbp", "total_gbp", "status", "_extracted_at")),
        _facts("projects", "project_id", "name", "customer_id", "manager_id", "contract_ref",
               "status", "start_date", "end_date"),
        EntityCatalogue(name="passages.larkspur-files.default", key=("chunk_id",),
                        system_time_field="ingested_at", text_field="content",
                        fields=("chunk_id", "doc_id", "content", "locator", "title",
                                "retrieved_at", "ingested_at")),
    ))


def _agent(harness=None, catalogue=RUN2_CATALOGUE):
    return DataAgent(harness or gold_harness(), catalogue, build_time=BUILD_TIME)


def _model(*answers):
    """A FakeBackend standing in for a model: answer ``i`` on call ``i`` (the last repeats)."""
    calls: list[str] = []

    def reply(*, messages=None, **_):
        calls.append(messages[-1]["content"])
        return answers[min(len(calls), len(answers)) - 1]

    return Harness(FakeBackend(reply), profile="cloud", cache=None), calls


def _self_draft(entity="invoice"):
    """What a model wrote in run 2: a field of the row itself, spelled ``relation: "self"``."""
    return json.dumps({
        "shape": "tabular", "entity": entity, "decision_time_field": "issued_on",
        "feature_axis": "system_time", "feature_as_of": "per_row",
        "features": [{"path": f"{entity}.net_gbp", "entity": entity, "field_name": "net_gbp",
                      "relation": "self", "type": "number"}],
        "label": {"path": f"{entity}.status", "entity": entity, "field_name": "status",
                  "join_key": "_id", "valid_time_field": "_valid_from",
                  "system_time_field": "_system_from", "type": "category"},
        "label_axis": "valid_time", "label_window": "P30D",
        "label_read_at_system_time": "build_time", "label_maturity_lag": "P14D",
        "row_order": [f"{entity}._id"], "null_policy": "keep"})


# ==========================================================================================
# N-07 (a): the model is shown the schema it is judged by
# ==========================================================================================
def test_the_prompt_schema_is_the_validated_schema():
    """Every closed set the plan validates against is in the prompt, generated from `plan.py`."""
    text = plan_schema_prompt()
    assert set(RELATION_MEANINGS) == set(get_args(Relation)), (
        "every relation the plan accepts has a meaning shown to the model, and nothing else")
    for value in (*get_args(Relation), *get_args(ColumnType), *built_shapes()):
        assert value in text, value
    unbuilt = [k for k in get_args(ShapeKind) if k not in built_shapes()]
    assert unbuilt and all(k in text.split("NOT built")[0] for k in unbuilt), (
        "unbuilt shapes are named as NOT built, so the model is not invited to pick one")
    schema = json.dumps(PlanDraft.model_json_schema())
    assert all(r in schema for r in get_args(Relation)), (
        "the structured-output schema itself lists the relations, not a bare string")
    assert "CATALOGUE" not in text and "REQUEST" not in text, (
        "the offline planner splits the prompt on those headers")


# ==========================================================================================
# N-07 (b): deterministic, recorded normalisation — never widening
# ==========================================================================================
def test_the_run2_model_draft_with_relation_self_now_authors_a_plan_and_says_so():
    """The run's exact failure: before the fix, `ColumnSource(relation='self')` raised a
    ValidationError and the model's draft was discarded."""
    harness, calls = _model(_self_draft())
    authored = _agent(harness).author(NAMED_OUTCOME)
    assert len(calls) == 1, "repaired deterministically, no second model call needed"
    assert plan_schema_prompt() in calls[0], "the model is SHOWN the schema it is judged by"
    col = authored.plan.column("invoice.net_gbp")
    assert col.source.relation == "cohort_row"
    assert col.type == "float64"
    assert any("'self' -> 'cohort_row'" in r for r in authored.repairs), authored.repairs
    assert any("'number' -> 'float64'" in r for r in authored.repairs), authored.repairs
    assert any("system_time_field filled with '_system_from'" in r for r in authored.repairs)
    compile_plan(authored.plan)


@pytest.mark.parametrize("spelling", ["self", "Same-Row", "own", "prior", "lag", "count",
                                      "aggregate", "point in time"])
def test_no_relation_synonym_lands_on_an_axis_relation(spelling):
    """Axis relations (`cohort_key`, `decision_time`) are exempt from the system-time cut. A
    repair landing there would widen what a feature may see, so none does."""
    draft = PlanDraft.model_validate_json(_self_draft())
    draft = draft.model_copy(update={"features": [
        draft.features[0].model_copy(update={"relation": spelling})]})
    normal, repairs = normalise_draft(draft, RUN2_CATALOGUE)
    assert normal.features[0].relation in {"cohort_row", "as_of_prior", "aggregate_count"}
    assert repairs, "a repair is never silent"


def test_an_unmappable_or_cross_entity_relation_is_refused_with_the_accepted_set():
    draft = PlanDraft.model_validate_json(_self_draft())
    magic = draft.model_copy(update={"features": [
        draft.features[0].model_copy(update={"relation": "magic"})]})
    with pytest.raises(PlanDraftInvalid) as err:
        normalise_draft(magic, RUN2_CATALOGUE)
    assert all(r in str(err.value) for r in get_args(Relation))

    other = draft.model_copy(update={"features": [
        draft.features[0].model_copy(update={"entity": "organisation"})]})
    with pytest.raises(PlanDraftInvalid, match="another entity needs as_of_prior"):
        normalise_draft(other, RUN2_CATALOGUE)


def test_an_axis_spelling_keeps_its_meaning_so_the_compiler_still_refuses_it():
    """`valid` is normalised to `valid_time` — the SAME meaning — and the compiler refuses it.
    Normalisation fixes spelling; it never fixes judgement."""
    draft = PlanDraft.model_validate_json(_self_draft()).model_copy(
        update={"feature_axis": "Valid"})
    normal, _ = normalise_draft(draft, RUN2_CATALOGUE)
    assert normal.feature_axis == "valid_time"
    with pytest.raises(FeatureAxisRefused):
        compile_plan(_agent().to_plan(normal))


def test_a_draft_the_gate_refuses_is_redrafted_once_with_the_refusal_shown():
    bad = json.loads(_self_draft())
    bad["features"][0]["relation"] = "magic"
    harness, calls = _model(json.dumps(bad), _self_draft())
    authored = _agent(harness).author(NAMED_OUTCOME)
    assert len(calls) == 2
    assert "YOUR PREVIOUS DRAFT WAS REFUSED" in calls[1] and "'magic'" in calls[1]
    assert "YOUR PREVIOUS DRAFT WAS REFUSED" not in calls[0]
    assert any("re-drafted once" in r for r in authored.repairs)
    compile_plan(authored.plan)


def test_a_draft_refused_twice_is_refused_not_guessed():
    bad = json.loads(_self_draft())
    bad["features"][0]["relation"] = "magic"
    harness, calls = _model(json.dumps(bad))
    with pytest.raises(PlanDraftInvalid, match="twice"):
        _agent(harness).author(NAMED_OUTCOME)
    assert len(calls) == 2, "one re-draft, not a loop"


def test_a_model_that_asks_is_heard():
    harness, _ = _model(json.dumps({"entity": "invoice", "question": "Which outcome?"}))
    with pytest.raises(PlanNeedsInput, match="Which outcome"):
        _agent(harness).author(RUN_PLAIN)


# ==========================================================================================
# N-07 (c): the offline planner authors a plan that compiles, or asks
# ==========================================================================================
@pytest.mark.parametrize("request_", [RUN_PLAIN, RUN_LABELLED, RUN_LEAKY])
def test_the_runs_requests_ask_instead_of_authoring_an_uncompilable_plan(request_):
    """No field of the run's estate records whether an invoice was paid — `status` is a string
    the run's requests never name — so the honest answer is a question naming the candidates.
    Before the fix: FeatureAxisRefused (and, for the plain request, a `graph_tensor`)."""
    agent = _agent()
    draft = agent.draft(request_)
    assert draft.shape in built_shapes(), "never an unbuilt shape for a request that named none"
    with pytest.raises(PlanNeedsInput) as err:
        agent.author(request_)
    msg = str(err.value)
    invoice = RUN2_CATALOGUE.entity("invoice")
    assert "status" in msg and "issued_on" in msg, "the question names the candidates"
    assert all(f in invoice.fields for f in ("status", "issued_on"))


@pytest.mark.parametrize("request_", [NAMED_OUTCOME, NAMED_OUTCOME_2])
def test_a_request_naming_its_outcome_compiles_on_the_runs_catalogue(request_):
    authored = _agent().author(request_)
    plan = authored.plan
    compile_plan(plan)
    assert plan.shape.kind == "tabular" and plan.selection.entity == "invoice"
    # The decision instant is the instant field the request names — derived from the catalogue.
    decision = plan.selection.cohort.decision_time_field
    assert decision in RUN2_CATALOGUE.entity("invoice").fields and decision.endswith("_on")
    assert "issue" in request_.lower() and decision.startswith("issue")
    assert plan.shape.label.source.field == "status"
    assert plan.temporal.label_window.within == "P30D", "the request's own window"
    assert plan.temporal.feature_axis == "system_time"


def test_a_request_naming_its_outcome_as_a_feature_is_refused_by_the_compiler():
    """The planner does what was asked; the GATE refuses it — the label in the feature matrix."""
    with pytest.raises(LabelAxisRefused, match="label's own field"):
        compile_plan(_agent().author(NAMED_OUTCOME_LEAKY).plan)


def test_the_word_graph_is_not_a_request_for_a_graph_tensor():
    assert _agent().draft(RUN_PLAIN).shape == "tabular"
    with pytest.raises(ShapeNotImplemented, match="Built shapes"):
        _agent().author("a graph tensor of invoices for a GNN")


def test_the_chunk_request_still_plans_a_corpus():
    plan = _agent().author(RUN_CHUNKS).plan
    assert plan.shape.kind == "chunk_corpus"
    assert plan.selection.entity.startswith("passages.")


# ==========================================================================================
# The same, end to end through the platform, over a run-2-shaped workspace
# ==========================================================================================
@pytest.fixture(scope="module")
def vocab():
    with deployment("workspace") as v:
        yield v


@pytest.fixture()
def ledger(tmp_path, vocab):
    """Invoices shaped like run 2's: string attributes (`issued_on`, `net_gbp`, `status`, …), no
    boolean outcome — but with a history, so a point-in-time build has rows to emit."""
    from databridge_graph import GraphStore
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

    root = tmp_path / "ledger"
    cap, facts, pas = root / "capture", root / "facts", root / "passages"
    for d in (cap, facts, pas):
        d.mkdir(parents=True)
    store = GraphStore(str(root / "workspace.db"))
    orgs = []
    for name in ("Acme Holdings", "Birchwood Ltd"):
        text = f"Customer record. Organisation: {name}."
        sha = write_capture(cap, text, {"source": FILES_SOURCE, "title": name})
        oid = f"organisation:{name.split()[0].lower()}"
        orgs.append((oid, name))
        store.add_node(GraphNode(id=oid, kind=NodeKind("organisation"), label=name,
                                 attr_sources={"label": span(sha, text, name)},
                                 sources=(whole(sha, text),),
                                 valid_from=EPOCH - timedelta(days=400),
                                 system_from=EPOCH - timedelta(days=30)))
    for n in range(8):
        oid, oname = orgs[n % 2]
        issued = EPOCH + timedelta(days=20 * n)
        number = f"INV-{n:04d}"
        attrs = {"issued_on": issued.date().isoformat(),
                 "net_gbp": f"{1000 + 50 * n}.00", "vat_gbp": f"{200 + 10 * n}.00",
                 "status": "paid" if n % 3 else "open"}
        text = (f"Invoice {number} to {oname}. Issued {attrs['issued_on']}. Net "
                f"{attrs['net_gbp']}. VAT {attrs['vat_gbp']}. Status {attrs['status']}.")
        sha = write_capture(cap, text, {"source": FILES_SOURCE, "title": number})
        iid = f"invoice:{number.lower()}"
        store.add_node(GraphNode(
            id=iid, kind=NodeKind("invoice"), label=number, attrs=attrs,
            attr_sources={"label": span(sha, text, number),
                          **{k: span(sha, text, v) for k, v in attrs.items()}},
            sources=(whole(sha, text),), valid_from=issued + timedelta(days=1),
            system_from=issued + timedelta(days=2)))
        store.add_edge(GraphEdge(id=f"billed_to:{iid}->{oid}", src_id=iid, dst_id=oid,
                                 kind=EdgeKind("billed_to"), sources=(span(sha, text, oname),),
                                 valid_from=issued + timedelta(days=1),
                                 system_from=issued + timedelta(days=2)))
    m = PlatformMaterialiser(store_provider=lambda: store, vocabulary=vocab, facts_root=facts,
                             passages_root=pas, capture_root=cap, out_root=tmp_path / "out",
                             register_emissions=False, clock=lambda: BUILD_TIME)
    yield m
    store.close()


def test_the_run_requests_are_answered_with_a_question_through_the_platform(ledger):
    with pytest.raises(RunRefused) as err:
        ledger.run({"request": RUN_PLAIN}, by="staff")
    assert err.value.code == "PlanNeedsInput"
    assert "status" in err.value.message


@pytest.mark.parametrize("model", [False, True])
def test_a_tabular_dataset_is_materialised_from_english(ledger, model):
    """N-07 closed: English in, a tabular dataset out — offline planner, and a model whose draft
    uses run 2's `relation: "self"` (repaired, and the repair is in the receipt)."""
    if model:
        ledger._harness, _ = _model(_self_draft())
    result = ledger.run({"request": NAMED_OUTCOME}, by="staff")
    assert result["shape"] == "tabular" and result["entity"] == "invoice"
    assert result["rows"]["emitted"] > 0
    assert result["pointer_check"]["all_resolved"] and result["pointer_check"]["all_match"]
    if model:
        assert result["produced_by"]["model"] != "offline/gold-planner"
        assert any("'self' -> 'cohort_row'" in n for n in result["notes"]), result["notes"]


# ==========================================================================================
# N-08: a downloadable per-cell pointer sidecar
# ==========================================================================================
def test_the_dataset_ships_a_per_cell_pointer_file_listed_in_the_receipt(ledger):
    result = ledger.run({"request": NAMED_OUTCOME}, by="staff")
    plan_hash = result["plan_hash"]
    roles = ledger.file_roles(plan_hash)
    pointer_files = [f for f, r in roles.items() if r == "pointers"]
    assert len(pointer_files) == 1 and pointer_files[0].endswith(POINTER_SIDECAR_SUFFIX)
    name = pointer_files[0]
    doc = ledger.receipt(plan_hash)
    assert doc["receipt"]["pointer_manifest"]["uri"] == name
    assert doc["platform"]["pointer_file"]["name"] == name
    assert ledger.file_withheld(plan_hash, name) is None, "served while every check clears"

    data_name = next(f for f, r in roles.items() if r == "dataset")
    rows = [json.loads(x) for x in
            ledger.file_path(plan_hash, data_name).read_text("utf-8").splitlines() if x]
    lines = [json.loads(x) for x in
             ledger.file_path(plan_hash, name).read_text("utf-8").splitlines() if x]
    columns = doc["platform"]["columns"]
    assert len(lines) == len(rows) * len(columns), "one line per cell"
    assert {(ln["row"], ln["column"]) for ln in lines} == {
        (r, c) for r in range(len(rows)) for c in columns}, "keyed by row and column"
    grounded = [ln for ln in lines if ln["state"] == "grounded"]
    assert len(grounded) == doc["receipt"]["cells"]["grounded"] > 0
    assert doc["receipt"]["pointer_manifest"]["entries"] == len(grounded)

    # The anti-hallucination proof, made by a DOWNLOADER: each grounded line's pointer re-reads,
    # from the source, to exactly the value in that row and column of the dataset file.
    from databridge_core import SourcePointer

    reader = ledger.reader(system_as_of=BUILD_TIME)
    checked = 0
    for ln in grounded:
        pointer = SourcePointer.model_validate(ln["pointer"])
        ok, match = reader.cell_check(GroundedValue(value=rows[ln["row"]][ln["column"]],
                                                    pointer=pointer, confidence=1.0))
        if pointer.kind == "doc_span":
            assert ok and match, ln
            checked += 1
    assert checked > 0, "non-vacuous: span pointers were re-read from the downloaded file"


# ==========================================================================================
# N-09: the withheld-download path, exercisable
# ==========================================================================================
@pytest.mark.parametrize("check", ["pointer", "leak", "missing"])
def test_a_published_run_whose_record_fails_a_check_withholds_its_data(ledger, check):
    result = ledger.run({"request": NAMED_OUTCOME}, by="staff")
    plan_hash = result["plan_hash"]
    roles = ledger.file_roles(plan_hash)
    gated = [f for f, r in roles.items() if r in ("dataset", "pointers")]
    assert len(gated) == 2 and all(ledger.file_withheld(plan_hash, f) is None for f in gated)

    withhold_published_run(ledger.out_root, plan_hash, check=check)
    for f in gated:
        assert ledger.file_withheld(plan_hash, f), (check, f)
        assert ledger.file_path(plan_hash, f) is None
    for f in ("receipt.json", "plan.yaml"):
        assert ledger.file_withheld(plan_hash, f) is None and ledger.file_path(plan_hash, f)


def test_a_plain_english_duration_is_normalised_and_a_calendar_one_is_not():
    """A model writes "30 days" where the plan wants P30D. Days and weeks are fixed lengths and
    are repaired (recorded); "1 month" is not a fixed length and is left for the plan's own
    duration rule to refuse, with its reason."""
    draft = PlanDraft.model_validate_json(_self_draft()).model_copy(
        update={"label_window": "30 days", "label_maturity_lag": "2 weeks"})
    normal, repairs = normalise_draft(draft, RUN2_CATALOGUE)
    assert (normal.label_window, normal.label_maturity_lag) == ("P30D", "P14D")
    assert any("'30 days' -> 'P30D'" in r for r in repairs)
    month = draft.model_copy(update={"label_window": "1 month"})
    assert normalise_draft(month, RUN2_CATALOGUE)[0].label_window == "1 month"
