"""`/trace` and the `TraceRecorder` — the audit surface, and the four rules behind it.

The surface's value is that you cannot type a trace into it. So the tests are mostly about what
the recorder REFUSES to let a trace say:

1. **Nothing is seeded.** A fresh gateway serves an empty list, and there is no write route.
2. **A step that cannot be recorded is recorded as unrecordable.** Every gap becomes a defect with
   a code, and `integrity.complete` reports it without the reader having to notice by eye.
3. **The ledger append precedes the value it authorised** (`AGENT_PLANE.md` §7 property 3). A
   release with no covering append is still recorded — with a `LEDGER_APPEND_NOT_RECORDED` defect
   beside it, because hiding the disclosure to keep the trace clean is the worse failure.
4. **Withheld and absent stay different facts**, and an unlabelled cell is never counted as
   trusted.

The last test is the one that matters most: a real `POST /intelligence/ask` produces a trace with
real capability steps, real pointers and real cells. Nothing in this module fabricates one.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.trace_recorder import TraceRecorder
from databridge_gateway.trace_routes import register_trace_routes

from conftest import AUTH_HEADERS


@pytest.fixture()
def app():
    a = FastAPI()
    register_trace_routes(a)
    return a


@pytest.fixture()
def client(app):
    # R-015: both routes now require a credential.
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


# --- 1. nothing is seeded ------------------------------------------------------------
def test_a_fresh_gateway_holds_no_traces(client):
    body = client.get("/trace").json()
    assert body["traces"] == []
    assert body["total_held"] == 0
    assert body["evicted"] == 0


def test_there_is_no_route_that_writes_a_trace(app):
    """A `POST /trace` would make every trace indistinguishable from one somebody typed."""
    writes = [(r.path, sorted(r.methods)) for r in app.routes
              if getattr(r, "path", "").startswith("/trace")
              and {"POST", "PUT", "PATCH", "DELETE"} & set(getattr(r, "methods", ()))]
    assert writes == []


def test_an_unknown_trace_is_a_404_and_not_an_empty_trace(client):
    r = client.get("/trace/req-999999")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_TRACE"


# --- 2. an unrecordable step is recorded ---------------------------------------------
def test_a_missing_purpose_is_a_defect_and_not_a_default():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.principal(actor="svc", on_behalf_of=None, purpose=None)
    trace = tr.close("answered")
    assert trace.principal["purpose"] is None
    assert "PURPOSE_NOT_RECORDED" in trace.integrity()["defect_codes"]
    assert trace.integrity()["complete"] is False


def test_a_denial_with_no_reason_code_is_a_defect():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.policy(field_path="x.y", withheld=True, policy_id="p1", reason_code=None)
    trace = tr.close("answered")
    assert "DENIAL_NOT_ATTRIBUTABLE" in trace.integrity()["defect_codes"]


def test_an_unknown_pointer_origin_is_refused_rather_than_guessed():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.pointer(origin="probably_the_graph", pointer_id="p")
    trace = tr.close("answered")
    assert "POINTER_ORIGIN_NOT_RECORDED" in trace.integrity()["defect_codes"]
    assert trace._of("pointer")[0].detail["origin"] is None


def test_a_handler_that_raises_leaves_a_closed_failed_trace():
    rec = TraceRecorder()
    with pytest.raises(RuntimeError):
        with rec.begin(surface="s", agent="a", version="v1") as tr:
            tr.capability(source_id="src", capability="read")
            raise RuntimeError("boom")
    trace = rec.get(tr.request_id)
    assert trace.outcome == "failed"
    assert "STEP_UNRECORDABLE" in trace.integrity()["defect_codes"]


# --- 3. the ledger append precedes the value -----------------------------------------
def test_a_release_with_no_ledger_append_is_recorded_with_a_defect():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.released(field_path="account.balance")
    trace = tr.close("answered")
    released = trace._of("released")
    assert len(released) == 1, "the disclosure is recorded even though it was unauthorised"
    assert released[0].detail["ledger_covered"] is False
    assert "LEDGER_APPEND_NOT_RECORDED" in trace.integrity()["defect_codes"]


def test_a_ledger_append_before_the_release_leaves_the_trace_clean_and_ordered():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.principal(actor="svc", on_behalf_of="j.tan", purpose="reading")
    # Codex f1 #4: coverage is (source, field), so both name the source they concern.
    tr.pointer(origin="live_source", pointer_id="p-1", source_id="core-banking")
    tr.ledger_append(entry_id="40219", covers=["account.balance"], source_id="core-banking")
    tr.released(field_path="account.balance", source_id="core-banking", pointer_id="p-1")
    trace = tr.close("answered")
    assert trace.integrity()["complete"] is True
    assert trace._of("released")[0].detail["covered_by"] == "40219"
    seqs = {s.kind: s.seq for s in trace.steps}
    assert seqs["ledger_append"] < seqs["released"], (
        "the audit row must precede the disclosure it authorised (AGENT_PLANE §7)")


def test_an_append_that_does_not_cover_the_field_does_not_authorise_it():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.ledger_append(entry_id="1", covers=["account.opened_on"])
    tr.released(field_path="account.balance")
    trace = tr.close("answered")
    assert "LEDGER_APPEND_NOT_RECORDED" in trace.integrity()["defect_codes"]


# --- 4. withheld, absent, and trust --------------------------------------------------
def test_withheld_and_absent_are_separate_rows_and_never_merged():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.cell(state="withheld", field_path="p.notes", reason_code="purpose_not_permitted",
            policy_id="P-14", source_id="core")
    tr.cell(state="absent", field_path="p.middle_name", source_id="core")
    trace = tr.close("answered")
    kinds = {row["kind"]: row for row in trace.view()["results"]}
    assert kinds["withheld"]["count"] == 1
    assert kinds["absent"]["count"] == 1
    assert "purpose_not_permitted" in kinds["withheld"]["detail"]
    assert trace.summary()["withheld"] == 1 and trace.summary()["absent"] == 1


def test_a_withheld_cell_with_no_reason_code_is_a_defect():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.cell(state="withheld", field_path="p.notes")
    trace = tr.close("answered")
    assert "WITHHOLDING_REASON_NOT_RECORDED" in trace.integrity()["defect_codes"]


def test_an_unlabelled_cell_is_reported_unlabelled_and_not_as_trusted():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.cell(state="grounded", field_path="p.name", trust="trusted")
    tr.cell(state="grounded", field_path="p.dob")
    trace = tr.close("answered")
    grounded = next(r for r in trace.view()["results"] if r["kind"] == "grounded")
    assert "1 trusted" in grounded["detail"]
    assert "1 unlabelled" in grounded["detail"]


def test_the_graph_branch_splits_history_from_live_bytes():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    for _ in range(3):
        tr.pointer(origin="graph", as_of="2026-06-30T00:00:00Z")
    tr.pointer(origin="live_source")
    trace = tr.close("answered")
    branch = next(b for b in trace.view()["branches"] if b["kind"] == "graph")
    rows = {row["label"]: row["value"] for row in branch["rows"]}
    assert rows["pointers resolved"] == "4"
    assert rows["answered from history"] == "3"
    assert rows["needed live bytes"] == "1"
    assert rows["values stored"] == "0"


def test_an_unmeasured_cost_is_null_and_never_zero():
    """A `0` in an audit record is a measurement. `None` is "nobody measured"."""
    rec = TraceRecorder()
    tr = rec.begin(surface="s", agent="a", version="v1")
    tr.cost(wall_ms=12)
    trace = tr.close("answered")
    assert trace.view()["request"]["budget_used"] is None
    assert trace.view()["answer"]["certificate_risk"] is None


def test_eviction_is_reported_rather_than_silent():
    rec = TraceRecorder(capacity=2)
    for _ in range(4):
        rec.begin(surface="s").close("answered")
    listed = rec.list()
    assert listed["total_held"] == 2
    assert listed["evicted"] == 2


def test_a_trace_closed_twice_keeps_its_first_outcome():
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.close("answered")
    tr.close("refused")
    trace = rec.get(tr.request_id)
    assert trace.outcome == "answered"
    assert "TRACE_CLOSED_TWICE" in trace.integrity()["defect_codes"]


# --- 5. traces come from real activity -----------------------------------------------
@pytest.fixture(scope="module")
def live_client():
    """The real BI surface, with the deterministic gold backend. No trace is planted."""
    from databridge_gateway.intelligence_routes import _register_intelligence_routes

    a = FastAPI()
    _register_intelligence_routes(a)
    register_trace_routes(a)
    from brain_server import BrainTools
    from databridge_evals.e_intake import gold_harness

    brain = BrainTools(harness=gold_harness())
    brain.load_seed()
    a.state.brain = brain
    with TestClient(a, headers=AUTH_HEADERS) as c:
        yield c


def test_a_real_request_records_a_real_trace(live_client):
    before = live_client.get("/trace").json()["total_held"]
    r = live_client.post("/intelligence/ask",
                         json={"question": "which issuers sent the most documents this week, "
                                           "and what is still blocked?"})
    assert r.status_code == 200, r.text
    listed = live_client.get("/trace").json()
    assert listed["total_held"] == before + 1

    detail = live_client.get(f"/trace/{listed['traces'][0]['request_id']}").json()
    assert detail["outcome"] in ("answered", "refused")
    assert detail["capabilities"] >= 1, "the tool calls the agent made are the capability steps"
    assert detail["principal"]["actor"], "an unattributable read is not an auditable one"
    # This surface constructs no Principal, so the purpose is missing — and the trace SAYS it is
    # missing rather than filling one in. That defect is the recorder working, not failing.
    assert detail["principal"]["purpose"] is None
    assert "PURPOSE_NOT_RECORDED" in detail["integrity"]["defect_codes"]
    kinds = {s["kind"] for s in detail["step_list"]}
    assert {"principal", "capability", "cost", "closed"} <= kinds


def test_the_real_trace_reports_the_ledger_gap_it_actually_has(live_client):
    """`/intelligence/ask` discloses values from a read-only traversal without appending a read
    entry first, which `AGENT_PLANE.md` §7 property 3 requires. The recorder is supposed to notice.

    If this test ever fails because the defect is gone, the fix landed and this test should assert
    the ordering instead — but it must never be deleted to make the trace look clean."""
    live_client.post("/intelligence/ask",
                     json={"question": "which issuers sent the most documents this week?"})
    listed = live_client.get("/trace").json()
    detail = live_client.get(f"/trace/{listed['traces'][0]['request_id']}").json()
    released = [s for s in detail["step_list"] if s["kind"] == "released"]
    if released:
        assert "LEDGER_APPEND_NOT_RECORDED" in detail["integrity"]["defect_codes"]
        assert all(s["detail"]["ledger_covered"] is False for s in released)


# =======================================================================================
# Codex f1 (first review of Activity & trace) -- ownership, ledger coverage, bounds
# =======================================================================================
import json                                                           # noqa: E402

from conftest import auth_headers                                     # noqa: E402

from databridge_gateway import trace_recorder as _tr_mod              # noqa: E402


def _record(app, *, on_behalf_of, surface="s"):
    """Record one trace the way a gateway surface does: the authenticated principal is the
    principal step's `on_behalf_of`."""
    from databridge_gateway.trace_recorder import recorder_for
    tr = recorder_for(app).begin(surface=surface, agent="a", version="v1")
    tr.principal(actor="svc", on_behalf_of=on_behalf_of, purpose="reading")
    tr.close("answered")
    return tr.request_id


@pytest.fixture()
def two_owners(app):
    return {"analyst-7": _record(app, on_behalf_of="analyst-7"),
            "fcc-3": _record(app, on_behalf_of="fcc-3"),
            None: _record(app, on_behalf_of=None)}


def test_a_trace_is_listed_only_to_its_owner(app, two_owners):
    for who in ("analyst-7", "fcc-3"):
        with TestClient(app, headers=auth_headers(who)) as c:
            body = c.get("/trace").json()
        ids = {t["request_id"] for t in body["traces"]}
        assert ids == {two_owners[who]}, (who, ids)
        assert body["total_held"] == len(ids), "the count is the viewer's, never the store's"
        assert body["scope"] == "own"


def test_another_principals_trace_is_indistinguishable_from_a_missing_one(app, two_owners):
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        theirs = c.get(f"/trace/{two_owners['fcc-3']}")
        ownerless = c.get(f"/trace/{two_owners[None]}")
        missing = c.get("/trace/req-999999-00000000")
        mine = c.get(f"/trace/{two_owners['analyst-7']}")
    assert mine.status_code == 200
    assert theirs.status_code == ownerless.status_code == missing.status_code == 404
    # Byte-for-byte the same body: nothing in the refusal varies with whether the trace exists.
    assert theirs.content == ownerless.content == missing.content


def test_an_ownerless_or_ambiguous_trace_is_admin_only(app, monkeypatch):
    from databridge_gateway.trace_recorder import recorder_for
    ownerless = _record(app, on_behalf_of=None)
    tr = recorder_for(app).begin(surface="s")
    tr.principal(actor="svc", on_behalf_of="analyst-7", purpose="p")
    tr.principal(actor="svc", on_behalf_of="fcc-3", purpose="p")
    tr.close("answered")
    ambiguous = tr.request_id
    assert recorder_for(app).get(ambiguous).owner is None

    for who in ("analyst-7", "fcc-3", "staff"):
        with TestClient(app, headers=auth_headers(who)) as c:
            listed = {t["request_id"] for t in c.get("/trace").json()["traces"]}
            assert not listed & {ownerless, ambiguous}, who
            assert c.get(f"/trace/{ambiguous}").status_code == 404

    monkeypatch.setenv("WALNUT_AUDIT_ROLES", json.dumps({"audit-1": ["trace_admin"]}))
    with TestClient(app, headers=auth_headers("audit-1")) as c:
        body = c.get("/trace").json()
        assert {ownerless, ambiguous} <= {t["request_id"] for t in body["traces"]}
        assert body["scope"] == "all"
        assert c.get(f"/trace/{ownerless}").status_code == 200


@pytest.mark.parametrize("config", ["not json", json.dumps(["audit-1"]),
                                    json.dumps({"audit-1": ["root"]}),
                                    json.dumps({"audit-1": "trace_admin"}),
                                    json.dumps({"audit-1": ["activity_operator"]})])
def test_a_malformed_unknown_or_other_role_config_grants_no_trace_admin(app, monkeypatch, config):
    ownerless = _record(app, on_behalf_of=None)
    monkeypatch.setenv("WALNUT_AUDIT_ROLES", config)
    with TestClient(app, headers=auth_headers("audit-1")) as c:
        assert c.get(f"/trace/{ownerless}").status_code == 404
        assert c.get("/trace").json()["scope"] == "own"


def test_the_role_vocabulary_is_closed():
    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.trace_routes import has_audit_role
    with pytest.raises(ValueError):
        has_audit_role(AuthenticatedPrincipal(principal_id="x"), "superuser")


def test_a_declared_owner_the_principal_step_contradicts_is_no_owner():
    rec = TraceRecorder()
    tr = rec.begin(surface="s", owner="analyst-7")
    assert tr.trace.owner == "analyst-7"
    tr.principal(actor="svc", on_behalf_of="fcc-3", purpose="p")
    assert tr.trace.owner is None


def test_eviction_counts_are_the_viewers_own():
    rec = TraceRecorder(capacity=2)
    order = ["fcc-3", "fcc-3", "analyst-7", "analyst-7"]
    for who in order:
        t = rec.begin(surface="s")
        t.principal(actor="svc", on_behalf_of=who, purpose="p")
    # Derived: the first len(order) - capacity traces were evicted.
    gone = order[:len(order) - 2]
    assert rec.list_for("analyst-7")["evicted"] == gone.count("analyst-7")
    assert rec.list_for("fcc-3")["evicted"] == gone.count("fcc-3") > 0
    assert rec.list_for("x", admin=True)["evicted"] == rec.evicted == len(gone)


def test_trace_ids_are_ordered_but_carry_a_random_suffix():
    rec = TraceRecorder()
    ids = [rec.begin(surface="s").request_id for _ in range(3)]
    assert [i.split("-")[1] for i in ids] == ["000001", "000002", "000003"]
    suffixes = [i.split("-")[2] for i in ids]
    assert all(len(x) == 8 for x in suffixes) and len(set(suffixes)) == len(suffixes)


# --- ledger coverage (Codex f1 #4) ---------------------------------------------------
def _release_after(**append):
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.pointer(origin="graph", pointer_id="p-1", source_id="core-banking")
    tr.ledger_append(**append)
    tr.released(field_path="account.balance", source_id="core-banking", pointer_id="p-1")
    return tr.close("answered")


@pytest.mark.parametrize("append", [
    {"entry_id": "x", "covers": [], "source_id": "core-banking"},          # empty is not a wildcard
    {"entry_id": "x", "covers": ["*"], "source_id": "other-source"},       # another source
    {"entry_id": "x", "covers": ["account.balance"], "source_id": "other"},
    {"entry_id": "x", "covers": ["*"]},                                    # no source recorded
    {"entry_id": "", "covers": ["*"], "source_id": "core-banking"},        # no entry recorded
])
def test_an_append_that_does_not_explicitly_cover_the_release_never_certifies_it(append):
    trace = _release_after(**append)
    released = trace._of("released")[0].detail
    assert released["ledger_covered"] is False
    assert released["covered_by"] is None
    assert "LEDGER_APPEND_NOT_RECORDED" in trace.integrity()["defect_codes"]


@pytest.mark.parametrize("covers", [["*"], ["account.balance"]])
def test_an_explicit_same_source_append_certifies_and_names_its_entry(covers):
    trace = _release_after(entry_id="e-1", covers=covers, source_id="core-banking")
    released = trace._of("released")[0].detail
    assert released["ledger_covered"] is True and released["covered_by"] == "e-1"
    assert trace.integrity()["complete"] is True


def test_a_released_pointer_must_have_been_recorded():
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.pointer(origin="graph", pointer_id="p-1", source_id="src")
    tr.ledger_append(entry_id="e", covers=["*"], source_id="src")
    tr.released(field_path="f", source_id="src", pointer_id="p-1")
    tr.released(field_path="g", source_id="src", pointer_id="p-404")
    tr.released(field_path="h", source_id="src")
    trace = tr.close("answered")
    got = {s.detail["field_path"]: s.detail["pointer_recorded"] for s in trace._of("released")}
    # Codex f2 #2: a release naming NO pointer is not "the call site did not say" -- the value has
    # no recorded pointer, and that is a defect like any other unrecorded pointer
    assert got == {"f": True, "g": False, "h": False}
    reasons = [d for d in trace.defects if d["code"] == "RELEASE_POINTER_NOT_RECORDED"]
    assert {d["detail"]["field_path"] for d in reasons} == {"g", "h"}
    assert "RELEASE_POINTER_NOT_RECORDED" in trace.integrity()["defect_codes"]


# --- bounded work (Codex f1 #6) ------------------------------------------------------
def test_a_trace_holds_a_bounded_number_of_steps_and_says_so(monkeypatch):
    monkeypatch.setattr(_tr_mod, "MAX_STEPS_PER_TRACE", 5)
    monkeypatch.setattr(_tr_mod, "MAX_DEFECTS_PER_TRACE", 3)
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.principal(actor="svc", on_behalf_of="analyst-7", purpose="p")
    for i in range(20):
        tr.note(key=f"k{i}", text="t")
    # A principal step past the bound is not stored but still counts for ownership.
    tr.principal(actor="svc", on_behalf_of="fcc-3", purpose="p")
    for _ in range(10):
        tr.released(field_path="f", source_id="s")                        # a defect each
    trace = rec.get(tr.request_id)
    assert len(trace.steps) == _tr_mod.MAX_STEPS_PER_TRACE
    integ = trace.integrity()
    assert integ["steps_dropped"] > 0 and integ["complete"] is False
    assert len(trace.defects) == _tr_mod.MAX_DEFECTS_PER_TRACE and integ["defects_dropped"] > 0
    assert integ["defect_count"] == len(trace.defects) + integ["defects_dropped"]
    assert "STEP_LIMIT_REACHED" in integ["defect_codes"]
    assert trace.owner is None, "a dropped second principal must still make ownership ambiguous"


# --- Codex f2 (final) — ACT3 ---------------------------------------------------------
def test_f2_02_a_pointer_of_one_source_never_vouches_for_a_release_of_another():
    """Codex f2 #2: the pointer must match by id AND source; a release naming none is a defect."""
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.pointer(origin="graph", pointer_id="p-a", source_id="source-a")
    tr.ledger_append(entry_id="e-b", covers=["*"], source_id="source-b")
    tr.released(field_path="x", source_id="source-b", pointer_id="p-a")      # A's pointer
    trace = tr.close("answered")
    [rel] = [s.detail for s in trace._of("released")]
    assert rel["ledger_covered"] is True                    # non-vacuous: only the pointer fails
    assert rel["pointer_recorded"] is False
    assert trace.integrity()["complete"] is False
    assert "RELEASE_POINTER_NOT_RECORDED" in trace.integrity()["defect_codes"]
    # the same release with B's own pointer is clean
    tr2 = rec.begin(surface="s")
    tr2.pointer(origin="graph", pointer_id="p-a", source_id="source-b")
    tr2.ledger_append(entry_id="e-b", covers=["*"], source_id="source-b")
    tr2.released(field_path="x", source_id="source-b", pointer_id="p-a")
    assert tr2.close("answered").integrity()["complete"] is True


def test_f2_04_after_steps_are_dropped_no_count_is_presented_as_a_total(monkeypatch):
    """Codex f2 #4: counts over the stored prefix are null (summary) or labelled lower bounds
    (view) once a step was dropped; before that they are exact."""
    monkeypatch.setattr(_tr_mod, "MAX_STEPS_PER_TRACE", 6)
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.principal(actor="svc", on_behalf_of="analyst-7", purpose="p")
    tr.pointer(origin="graph", pointer_id="p", source_id="s")
    tr.capability(source_id="s", capability="read")
    tr.cell(state="grounded", field_path="a", source_id="s", trust="trusted")
    exact = rec.get(tr.request_id)
    assert exact.summary()["grounded"] == 1 and exact.summary()["at_least"] is None
    assert exact.view()["counts_exact"] is True
    for i in range(10):
        tr.cell(state="grounded", field_path=f"b{i}", source_id="s", trust="trusted")
        tr.cost(llm_tokens=5)
    trace = rec.get(tr.request_id)
    assert trace.steps_dropped > 0                                     # non-vacuous
    summary = trace.summary()
    for key in ("capabilities", "withheld", "absent", "grounded", "escalations"):
        assert summary[key] is None, key
    assert summary["at_least"]["grounded"] == len([s for s in trace._of("cell")])
    assert summary["steps"] == len(trace.steps) + trace.steps_dropped
    view = trace.view()
    assert view["counts_exact"] is False
    assert view["request"]["budget_used"] is None
    assert view["answer"]["citations"] is None
    assert all(r["lower_bound"] is True and r["detail"].startswith("at least")
               for r in view["results"]) and view["results"]
    graph = next(b for b in view["branches"] if b["kind"] == "graph")
    assert graph["rows"][0]["value"].startswith("at least")


def test_f2_07_a_step_is_bounded_in_size_and_the_trace_in_bytes(monkeypatch):
    """Codex f2 #7: one step cannot carry an unbounded payload, and a trace has a byte bound."""
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.note(key="k", text="x" * (_tr_mod.MAX_DETAIL_STR * 50))
    tr.capability(source_id="s", capability="read",
                  fields=[f"f{i}" for i in range(_tr_mod.MAX_DETAIL_ITEMS * 5)])
    tr.budget(max_llm_tokens=1, nested={"deep": ["y" * 10_000] * 1000})
    trace = rec.get(tr.request_id)
    note, cap, budget = trace.steps
    assert len(note.detail["text"]) == _tr_mod.MAX_DETAIL_STR
    assert len(cap.detail["fields"]) == _tr_mod.MAX_DETAIL_ITEMS
    assert "nested" not in budget.detail and budget.detail["max_llm_tokens"] == 1
    assert all(s.detail.get("bounded") for s in (note, cap, budget))
    assert "STEP_DETAIL_BOUNDED" in trace.integrity()["defect_codes"]
    assert len(json.dumps(trace.to_dict())) < 200_000
    # the whole trace is bounded in bytes too
    monkeypatch.setattr(_tr_mod, "MAX_TRACE_BYTES", 20_000)
    tr2 = rec.begin(surface="s")
    for i in range(200):
        tr2.note(key=f"k{i}", text="z" * 1000)
    t2 = rec.get(tr2.request_id)
    assert t2.steps_dropped > 0 and t2.bytes_held <= 20_000 + _tr_mod.MAX_DEFECT_BYTES
    assert "TRACE_BYTES_LIMIT_REACHED" in t2.integrity()["defect_codes"]


def test_f2_09_a_step_stores_only_its_kinds_fields():
    """Codex f2 #9: a trace step is an allow-list too. `budget(**rest)` was a pass-through: a key
    no budget names is not stored (it is named in `bounded`, with a defect)."""
    rec = TraceRecorder()
    tr = rec.begin(surface="s")
    tr.budget(max_llm_tokens=10, max_steps=3, admission_id="adm-1", api_secret="SECRET-VALUE")
    trace = rec.get(tr.request_id)
    [budget] = trace._of("budget")
    assert "api_secret" not in budget.detail and "SECRET-VALUE" not in json.dumps(trace.to_dict())
    assert budget.detail["max_steps"] == 3 and budget.detail["admission_id"] == "adm-1"
    assert any("api_secret" in b for b in budget.detail["bounded"])
    # the runtime's real budget call stores whole and leaves the trace complete
    tr2 = rec.begin(surface="s")
    tr2.budget(max_llm_tokens=10, max_steps=3, max_tool_calls=4, admission_id="adm-2")
    assert "bounded" not in rec.get(tr2.request_id)._of("budget")[0].detail
    assert rec.get(tr2.request_id).integrity()["complete"] is True
