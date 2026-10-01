"""`/intelligence` — the Business Intelligence surface (T-42/T-43), under the HEALTHCARE pack.

This file is the healthcare-bound fixture for the generic surface: the clinic seed is loaded into
an in-memory store explicitly (`BrainTools.load_seed()` — the gateway itself only loads it when
`WALNUT_LOAD_CLINIC_SEED=1`), and the generic figures are asserted to still say what the clinical
ones said. `test_intelligence_generic.py` covers the same surface under a synthetic pack.

The router is mounted onto a bare app here rather than exercised through `create_app()`. That is
deliberate and it is the point of the test: `/intelligence` must not depend on the demo state the
full gateway lifespan builds, and it must survive each of its data sources being absent — which is
only provable if the test can take one away.

What is guarded:

1. **Fail open.** No graph, or a traversal that raises, degrades to a reason string with a 200.
   A dashboard must not 500 because an aggregate failed (T-43), and a silent `0` would be worse
   than an outage.
2. **Every figure carries its denominator and its members**, so the page can link a number to the
   traversal behind it instead of asserting it.
3. **Hard constraint 1.** The blocked-intake payload says how many candidates a document named and
   never which — no name, no ranking, no selection, in any field.
4. **Structural counts stay separate from time saved.** `/impact` owns the only minutes claim this
   system makes; nothing here restates it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.intake_routes import _register_intake_routes
from databridge_gateway.intelligence_routes import _register_intelligence_routes

from conftest import AUTH_HEADERS


def _brain():
    from brain_server import BrainTools
    from databridge_evals.e_intake import gold_harness

    brain = BrainTools(harness=gold_harness())
    brain.load_seed()
    return brain


@pytest.fixture(scope="module")
def app():
    """The composition main.py will have: intake state + the clinic graph + this router."""
    a = FastAPI()
    _register_intake_routes(a)
    _register_intelligence_routes(a)
    a.state.brain = _brain()
    return a


@pytest.fixture(scope="module")
def client(app):
    # R-015: `/intelligence*` now requires a credential. `/intake/*` on this same app does not
    # (out of R-015's scope), so sending the header on every call is harmless there too.
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def chit():
    from databridge_intake.paths import fixtures_dir

    return (fixtures_dir() / "chits" / "chit_01_meridian_referral.txt").read_text(encoding="utf-8")


# --- the graph figures -------------------------------------------------------------
def test_the_graph_figures_are_present_and_counted_over_the_seed(client):
    body = client.get("/intelligence").json()
    graph = body["graph"]
    assert body["graph_unavailable"] is None
    assert graph["documents"] > 0
    assert graph["nodes"] > 0 and graph["edges"] > 0
    assert graph["issuer_mix"], "the seed's paperwork came from somewhere"
    # `subjects` is a Count, and both halves are asserted separately because they are different
    # claims: `n` is the registry rows a document has claimed, `of` is the rows on file.
    assert graph["subjects"]["n"] > 0
    assert graph["subjects"]["of"] > graph["subjects"]["n"]
    # The kinds present, from the pack's own declarations — and the subject kind is flagged.
    assert any(k["subject"] for k in graph["kinds"])
    assert sum(k["n"] for k in graph["kinds"]) == graph["nodes"]


def test_every_figure_carries_its_denominator_and_the_ids_behind_it(client):
    graph = client.get("/intelligence").json()["graph"]
    from databridge_gateway.intelligence_surface import COUNT_FIGURES

    for key in COUNT_FIGURES:
        count = graph[key]
        assert count["label"], f"{key} is a bare number with no label"
        assert count["of"] >= count["n"]
        if count.get("members_withheld"):
            # The one figure where two of this page's rules collide, and the collision is
            # ANNOUNCED rather than resolved quietly. `conflicts` counts REGISTRY ROWS, so its
            # members are patients: "every figure names the ids it counted" and "this page never
            # names a patient" cannot both hold, and constraint 1 is the one that wins. An empty
            # `members` with no stated reason would read as a bug, or worse, as a zero.
            from databridge_gateway.intelligence_surface import MEMBERS_WITHHELD

            assert count["members_withheld"] == MEMBERS_WITHHELD
            assert count["members"] == []
            assert count["n"] >= 0 and count["of"] >= count["n"]
            continue
        assert len(count["members"]) == count["n"], (
            f"{key}: {count['n']} counted, {len(count['members'])} ids given — a figure nobody "
            "can walk to is indistinguishable from a literal")
        for member in count["members"]:
            assert member in graph["entities"], f"{member} is named but carries no label"
    for issuer in graph["issuer_mix"]:
        assert issuer["documents"] == len(issuer["members"])
        assert issuer["issuer_id"] in graph["entities"]


def test_the_coverage_gap_names_what_no_agreement_includes(client):
    graph = client.get("/intelligence").json()["graph"]
    gap = graph["uncovered_references"]
    assert gap["n"] == len(gap["members"]) > 0, "the seed has a coverage gap"
    for member in gap["members"]:
        entity = graph["entities"][member]
        assert entity["label"] and entity["kind"]


def test_the_subjects_figure_keeps_its_numbers_and_withholds_its_members(client):
    """`patients` used to pass through the sanitiser untouched — not because it was safe, but
    because it was an `int` with nothing in it to sanitise. As a `Count` its members are PATIENT
    node ids, and this page never names a patient (constraint 1). The numbers survive; the ids do
    not; and the withholding is ANNOUNCED, because an empty `members` with no stated reason reads
    as a bug or, worse, as a zero."""
    from databridge_gateway.intelligence_surface import MEMBERS_WITHHELD

    graph = client.get("/intelligence").json()["graph"]
    subjects = graph["subjects"]
    assert subjects["n"] > 0 and subjects["of"] > subjects["n"]
    assert subjects["label"]
    assert subjects["members"] == []
    assert subjects["members_withheld"] == MEMBERS_WITHHELD
    # …and no subject reached the entity table this page renders labels from, either — derived
    # from the pack's subject declaration rather than a kind name.
    subject_kinds = set(graph["withholding"]["subject_kinds"])
    assert subject_kinds
    assert all(e["kind"] not in subject_kinds for e in graph["entities"].values())
    for ranked in (*graph["most_connected"], *graph["recently_ingested"]):
        assert graph["entities"][ranked["id"]]["kind"] not in subject_kinds


# --- the bi-temporal rewind --------------------------------------------------------
def test_as_of_rewinds_the_graph_figures(client):
    live = client.get("/intelligence").json()["graph"]
    early = client.get("/intelligence", params={"as_of": "2026-01-02"}).json()
    assert early["as_of"].startswith("2026-01-02")
    assert early["graph"]["documents"] == 0 < live["documents"]
    # The registry and the rule table predate the paperwork, so the rows are on file — but nobody
    # has been claimed yet, because no document exists to claim them. Rewinding must move the two
    # halves of the Count independently; a single number could not have said this.
    assert early["graph"]["subjects"]["of"] > 0
    assert early["graph"]["subjects"]["n"] == 0


def test_as_of_accepts_a_naive_iso_datetime_and_a_z_suffix(client):
    naive = client.get("/intelligence", params={"as_of": "2026-08-01T00:00:00"}).json()
    zulu = client.get("/intelligence", params={"as_of": "2026-08-01T00:00:00Z"}).json()
    assert naive["graph"] == zulu["graph"]


def test_a_malformed_as_of_is_refused_rather_than_silently_ignored(client):
    r = client.get("/intelligence", params={"as_of": "last tuesday"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_AS_OF"


# --- fail open ---------------------------------------------------------------------
def test_no_graph_degrades_to_a_reason_not_a_500_and_not_a_zero():
    bare = FastAPI()
    _register_intelligence_routes(bare)
    with TestClient(bare, headers=AUTH_HEADERS) as c:
        r = c.get("/intelligence")
    assert r.status_code == 200
    body = r.json()
    assert body["graph"] is None and body["graph_unavailable"]
    assert body["queue"] is None and body["queue_unavailable"]


def test_a_traversal_that_raises_degrades_to_a_reason_not_a_500(app, monkeypatch):
    import databridge_graph.analytics as analytics_mod

    def boom(*_a, **_k):
        raise RuntimeError("traversal exploded")

    monkeypatch.setattr(analytics_mod, "compute", boom)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        body = c.get("/intelligence").json()
    assert body["graph"] is None
    assert "failed" in body["graph_unavailable"]
    # the rest of the page still renders — one failed aggregate does not take the surface down
    assert body["queue"] is not None


def test_a_broken_intake_queue_degrades_the_same_way(app, monkeypatch):
    class Exploding(dict):
        def items(self):
            raise RuntimeError("queue exploded")

    monkeypatch.setattr(app.state, "intakes", Exploding())
    with TestClient(app, headers=AUTH_HEADERS) as c:
        body = c.get("/intelligence").json()
    assert body["queue"] is None and body["queue_unavailable"]
    assert body["graph"] is not None


# --- the queue, and hard constraint 1 ----------------------------------------------
def test_an_uploaded_intake_is_reported_blocked_on_a_human(client, chit):
    up = client.post("/intake/upload",
                     json={"document_id": "intel__chit_01.txt", "text": chit})
    assert up.status_code == 200, up.text
    queue = client.get("/intelligence").json()["queue"]
    assert queue["intakes"] >= 1
    entry = next(b for b in queue["blocked"] if b["intake_id"] == up.json()["intake_id"])
    assert entry["blocked_because"], "a blocked intake must say what it is waiting on"
    assert entry["identity_verified"] is False
    assert queue["blocked_count"] == len(queue["blocked"])


def test_the_queue_never_names_or_ranks_a_registry_candidate(client, chit):
    """Hard constraint 1. The page may report 'n candidates, needs a human'; it may not hand the
    client anything it could render as a choice."""
    client.post("/intake/upload", json={"document_id": "intel__chit_01b.txt", "text": chit})
    queue = client.get("/intelligence").json()["queue"]
    assert queue["blocked"], "the fixture should be waiting on a human"
    for entry in queue["blocked"]:
        assert isinstance(entry["candidate_count"], int)
        for forbidden in ("candidates", "patient_id", "selected_patient_id", "full_name",
                          "nric", "top_candidate", "score"):
            assert forbidden not in entry, f"{forbidden} would let a dashboard pick a person"


# --- R-22: an attestation cannot outlive its subject on the dashboard --------------
def test_the_queue_does_not_report_a_replaced_draft_as_identity_verified(client, chit):
    """R-22. The queue joined a confirmation to the current draft by `intake_id` alone — and
    `intake_id` is derived from the document, so it is identical before and after the document is
    re-read. A replaced draft therefore reported `identity_verified: true` off an in-person check
    made about the reading it replaced, on the one screen a manager reads to decide the counter is
    keeping up (CLAUDE.md §Hard constraints 1).
    """
    payload = {"document_id": "intel__stale_attestation__chit_01.txt", "text": chit}
    card = client.post("/intake/upload", json=payload).json()
    intake_id = card["intake_id"]

    sel = client.post(f"/intake/{intake_id}/select-patient", json={
        "patient_id": card["identity"]["candidates"][0]["patient_id"],
        "staff_id": "staff:front_desk_01", "attested_in_person": True})
    assert sel.status_code == 200, sel.text

    def entry():
        # Ready-to-commit intakes are listed apart from blocked ones (R-017 t08 #7); the R-22
        # question — is the attestation still about THIS draft — is asked of both.
        queue = client.get("/intelligence").json()["queue"]
        return next(b for b in queue["blocked"] + queue["ready_to_commit"]
                    if b["intake_id"] == intake_id)

    assert entry()["identity_verified"] is True

    # The same file dropped again: same intake, freshly extracted record, attestation retained.
    assert client.post("/intake/upload", json=payload).status_code == 200

    stale = entry()
    assert stale["identity_verified"] is False, (
        "the dashboard is reporting an identity check made about a replaced draft as verification "
        "of the one now in the queue")
    assert stale["blocked_because"], (
        "an intake nobody can commit must say what it is waiting on — an empty reason beside a "
        "blocked row is the silent zero this page refuses everywhere else")


def test_a_ready_to_commit_intake_is_not_counted_as_blocked(client, chit):
    """R-017 t08 #7. Attested and selected, not yet committed: the gate would open, so nothing is
    blocking it. It used to be appended to `blocked` with an empty reason, inflating
    `blocked_count`. It is listed as ready instead, and what it waits on is said."""
    card = client.post("/intake/upload", json={"document_id": "intel__ready__chit_01.txt",
                                               "text": chit}).json()
    intake_id = card["intake_id"]
    before = client.get("/intelligence").json()["queue"]
    assert intake_id in {b["intake_id"] for b in before["blocked"]}
    sel = client.post(f"/intake/{intake_id}/select-patient", json={
        "patient_id": card["identity"]["candidates"][0]["patient_id"],
        "staff_id": "staff:front_desk_01", "attested_in_person": True})
    assert sel.status_code == 200, sel.text
    queue = client.get("/intelligence").json()["queue"]
    assert intake_id not in {b["intake_id"] for b in queue["blocked"]}
    ready = next(b for b in queue["ready_to_commit"] if b["intake_id"] == intake_id)
    assert ready["identity_verified"] is True and ready["waiting_on"]
    assert queue["blocked_count"] == len(queue["blocked"])
    assert queue["ready_count"] == len(queue["ready_to_commit"])
    assert all(b["blocked_because"] for b in queue["blocked"]), "a blocked row names its reason"
    assert (queue["committed"] + queue["blocked_count"] + queue["ready_count"]
            == queue["intakes"])


# --- structural counts are not performance claims ----------------------------------
def test_the_payload_carries_no_time_saving_claim(client, chit):
    client.post("/intake/upload", json={"document_id": "intel__chit_01c.txt", "text": chit})
    flat = str(client.get("/intelligence").json()).lower()
    for forbidden in ("saved_min", "minutes_saved", "saved_pct", "hours_saved",
                      "projected_morning_hours_saved", "baseline_total"):
        assert forbidden not in flat, (
            f"{forbidden} belongs to /impact; a structural count rendered beside a performance "
            "claim reads as one")


def test_the_ingestion_register_is_present_and_empty_when_nothing_ran(client, tmp_path,
                                                                     monkeypatch):
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "none"))
    body = client.get("/intelligence").json()
    assert body["ingestion_unavailable"] is None
    assert body["ingestion"]["sources"] == [] and body["ingestion"]["note"]


def test_the_response_says_what_kind_of_numbers_these_are(client):
    note = client.get("/intelligence").json()["note"].lower()
    assert "structural" in note and "denominator" in note


# --- the subgraph behind a figure --------------------------------------------------
def test_a_figures_member_resolves_to_the_traversal_behind_it(client):
    graph = client.get("/intelligence").json()["graph"]
    member = graph["issuer_mix"][0]["members"][0]
    r = client.get(f"/intelligence/subgraph/{member}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["root"] == member
    assert body["nodes"] and body["edges"]
    assert any(e["source"] == member or e["target"] == member for e in body["edges"])


def test_every_node_in_a_subgraph_carries_its_pointers(client):
    graph = client.get("/intelligence").json()["graph"]
    member = graph["ambiguity"]["members"][0] if graph["ambiguity"]["members"] else \
        graph["issuer_mix"][0]["members"][0]
    body = client.get(f"/intelligence/subgraph/{member}").json()
    for node in body["nodes"]:
        assert body["sources"].get(node["id"]), (
            f"{node['id']} appears in a subgraph with no SourcePointer — the anti-fabrication "
            "wall says that node should not exist")


def test_an_unknown_entity_is_a_404_not_an_empty_graph(client):
    r = client.get("/intelligence/subgraph/document:no_such_document.txt")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_ENTITY"


def test_subgraph_depth_is_bounded(client):
    graph = client.get("/intelligence").json()["graph"]
    member = graph["issuer_mix"][0]["issuer_id"]
    assert client.get(f"/intelligence/subgraph/{member}", params={"depth": 99}).json()["depth"] == 3
    assert client.get(f"/intelligence/subgraph/{member}", params={"depth": 0}).json()["depth"] == 1


def test_the_drilldown_withholds_the_result_when_its_citations_cannot_be_loaded(client, app):
    """R-21. The drill-down exists so a figure on the page can be CHECKED. Catching a pointer
    lookup failure, setting `sources = {}` and returning the nodes anyway renders the values with
    the checking apparatus quietly removed — and an empty map is indistinguishable from "this
    entity genuinely has no citations", so the failure reads as a fact about the graph."""
    brain = app.state.brain
    entity = brain.store.open_nodes()[0]["id"]

    ok = client.get(f"/intelligence/subgraph/{entity}")
    assert ok.status_code == 200 and ok.json()["sources"]

    class OnlyPointersBroken:
        """Everything works EXCEPT the pointer read, so the traversal succeeds and only the
        evidence is missing — which is precisely the state that used to return values."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "sources_for":
                raise RuntimeError("pointer store is down")
            return getattr(self._real, name)

    original = brain.store
    brain.store = OnlyPointersBroken(original)
    try:
        broken = client.get(f"/intelligence/subgraph/{entity}")
    finally:
        brain.store = original

    # Either guard may fire — `row_to_node` reads pointers during the traversal itself, so the
    # walk can fail before the citation step. The invariant is the same whichever does: values
    # never come back without the evidence for them.
    assert broken.status_code == 503
    body = broken.json()
    assert body["refusal"]["code"] in {"EVIDENCE_UNAVAILABLE", "SUBGRAPH_FAILED"}
    assert "nodes" not in body, "values were returned without the evidence for them"


def test_every_entity_the_drilldown_returns_is_cited(client, app):
    """The invariant, asserted unconditionally. The first version of this test only checked nodes
    that HAD no sources — and `GraphNode` refuses construction without a pointer, so that branch
    could never run and the test could not fail."""
    entity = app.state.brain.store.open_nodes()[0]["id"]
    body = client.get(f"/intelligence/subgraph/{entity}").json()
    assert body["nodes"], "nothing traversed, so nothing was checked"
    for node in body["nodes"]:
        assert body["sources"].get(node["id"]), f"{node['id']} returned with no citation"


# --- R-015: no credential, no BI surface ---------------------------------------------
def test_both_routes_refuse_an_unauthenticated_caller(app):
    bare = TestClient(app)
    entity = app.state.brain.store.open_nodes()[0]["id"]
    for resp in (bare.get("/intelligence"), bare.get(f"/intelligence/subgraph/{entity}")):
        assert resp.status_code == 401
        assert resp.json()["refusal"]["code"] == "UNAUTHENTICATED"
