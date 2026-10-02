"""R-028 consistency (ROUTES track): the Sources, source-batch, adapter-setup and materialise routes.

* **#6** no exception text in a public payload or a visible job record: a fixed sentence plus an
  ``error_ref`` (the text is in the server log under it). Every failure here raises an exception
  whose text carries a fake path and a fake credential; the payload must carry neither, and must
  carry a reference that the log names.
* **#10** materialise: owner OR operator (`agent_ownership.is_operator`), and one byte-identical
  404 body for a missing record and another principal's.
* **#11** the standard refusal codes, with the former specific code kept as ``reason_code``.
* **#7** a decision answers who made it as the common actor object, never a decorated id.

The expectations are derived from the rules (`public_errors`), never written down; both fixture
estates (workspace and finance) run where an estate is involved.
"""
from __future__ import annotations

import logging
import re
import types

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS, auth_headers
from databridge_gateway import materialise_routes, public_errors, sources_batch_routes
from databridge_gateway.public_errors import STANDARD_CODES, STANDARD_FOR
from test_feat8_adapter_setup import (PERSON, SID, _agent_token, _fresh_auth,  # noqa: F401
                                      _plan_approved_and_discovered, estate, keeper_of)
from test_materialise_designs_routes import REQUESTS, world  # noqa: F401  (fixture)
from test_platform_routes import FakePlan, _cmd, _mount, env  # noqa: F401  (fixture)

#: What a leaking failure would say: a path and a credential-shaped token.
LEAK_PATH = r"C:\vault\walnut\secret.key"
LEAK_TOKEN = "sk-LEAK-7f3a9"
LEAK = f"cannot open {LEAK_PATH}: token={LEAK_TOKEN}"
_REF = re.compile(r"ERR-[0-9a-f]{10}")


class Leaky(RuntimeError):
    """An internal failure whose text must never reach a payload."""


def _boom(*_a, **_kw):
    raise Leaky(LEAK)


def _no_leak(payload) -> None:
    text = payload if isinstance(payload, str) else repr(payload)
    assert LEAK_TOKEN not in text and "vault" not in text and "Leaky" not in text, text


def _ref_logged(payload, caplog) -> str:
    """The payload carries an ``error_ref`` and the server log names it with the exception."""
    text = payload if isinstance(payload, str) else repr(payload)
    refs = _REF.findall(text)
    assert refs, f"no error_ref in {text}"
    logged = "\n".join(r.getMessage() + (repr(r.exc_info[1]) if r.exc_info else "")
                       for r in caplog.records)
    assert refs[0] in logged and LEAK_TOKEN in logged, "the reference must lead to the details"
    return refs[0]


@pytest.fixture(autouse=True)
def _log_errors(caplog):
    caplog.set_level(logging.INFO)
    yield


# =================================================================================================
# the shared rule
# =================================================================================================
def test_the_public_sentence_rule():
    """A refusal's own sentence is public only when the whole chain is person-readable refusals and
    it names no path; anything else gets the fixed sentence and a reference."""
    from databridge_mcp_ingest.plans import PlanError

    fresh = PlanError("the plan calls write_file, which writes")
    assert public_errors.public_sentence(fresh) == "the plan calls write_file, which writes"
    with_path = PlanError(f"the plan at {LEAK_PATH} is unreadable")
    assert public_errors.public_sentence(with_path) is None
    try:
        try:
            raise OSError(LEAK)
        except OSError:
            raise PlanError("the plan could not be read") from None
    except PlanError as wrapped:
        assert public_errors.public_sentence(wrapped) is None      # raised while handling OSError
    assert public_errors.public_sentence(Leaky("plain words")) is None   # not a refusal class
    # a ledger that does not verify is LEDGER_INTEGRITY_FAILED even though its class is a
    # subclass of the approver gate's refusal; an approver refusal is the approver code
    from databridge_core.decision_ledger import LEDGER_INTEGRITY_FAILED
    from databridge_mcp_ingest.plans import ApprovalRefused, LedgerRefused

    log = logging.getLogger("r028")
    broken = LedgerRefused("the plan approval log does not verify (record 1)",
                           code=LEDGER_INTEGRITY_FAILED)
    code, msg, _reason, _ref = public_errors.from_exception(
        log, broken, default_code="PLAN_NOT_APPROVABLE", context="t")
    assert (code, msg) == (LEDGER_INTEGRITY_FAILED, broken.args[0])
    code, msg, reason, ref = public_errors.from_exception(
        log, ApprovalRefused("a str is not an authenticated principal"),
        default_code="PLAN_NOT_APPROVABLE", context="t")
    assert code == "APPROVER_NOT_AUTHENTICATED" and reason == "PLAN_NOT_APPROVABLE" and ref
    assert msg == public_errors.FIXED_MESSAGES[code]
    # non-vacuous: every standard code has a fixed sentence and every mapped code maps to one
    assert set(STANDARD_CODES) <= set(public_errors.FIXED_MESSAGES)
    assert set(STANDARD_FOR.values()) <= set(STANDARD_CODES)


# =================================================================================================
# #6 — source-batch
# =================================================================================================
def test_batch_connections_failure_is_a_fixed_sentence_and_a_reference(env, caplog):
    app, rt, _ = _mount(env)
    rt.ingest.list_connections = _boom
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for method, url in (("get", "/source-batch/plans"),
                            ("post", "/source-batch/plans/propose"),
                            ("post", "/source-batch/runs")):
            r = getattr(c, method)(url)
            assert r.status_code == 500, (url, r.text)
            assert r.json()["refusal"]["code"] == "SOURCES_UNREADABLE"
            _no_leak(r.text)
            assert r.json()["refusal"]["error_ref"] == _ref_logged(r.text, caplog)


def test_batch_review_row_and_precheck_never_show_the_exception(env, monkeypatch, caplog):
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "lk").status_code == 200
        rt.ingest.plans["lk"] = FakePlan(source_id="lk", steps=["documents"])
        monkeypatch.setattr(sources_batch_routes, "assess_plan", _boom)
        row = c.get("/source-batch/plans").json()["pending"][0]
        assert row["source_id"] == "lk" and row["batch_approvable"] is False
        _no_leak(row)
        assert row["error_ref"] == _ref_logged(row["blocked"], caplog)
        r = c.post("/source-batch/plans/approve", json={"items": [
            {"source_id": "lk", "plan_digest": row["plan_digest"]}]})
        res = r.json()["results"][0]
        assert res["outcome"] == "needs_individual_review", res
        _no_leak(res)
        assert res["error_ref"] == _ref_logged(res["reason"], caplog)


# =================================================================================================
# #6 — Sources (error paths)
# =================================================================================================
def test_sources_unknown_source_and_listing_failures_do_not_echo_the_package(env, caplog):
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        rt.ingest.load_connection = _boom
        r = c.get("/sources/nosuch")
        assert r.status_code == 404 and r.json()["refusal"]["code"] == "UNKNOWN_SOURCE"
        _no_leak(r.text)
        rt.ingest.list_connections = _boom
        r = c.get("/sources")
        assert r.status_code == 500
        _no_leak(r.text)
        _ref_logged(r.text, caplog)


# =================================================================================================
# #6 / #11 — adapter-setup
# =================================================================================================
def test_setup_need_and_sources_failures_do_not_echo_the_package(estate, monkeypatch, caplog):
    from databridge_gateway import adapter_setup_routes

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        monkeypatch.setattr(adapter_setup_routes, "setup_state", _boom)
        r = c.get("/adapter-setup/sources")
        assert r.status_code == 200
        problems = r.json()["problems"]
        assert problems                                             # non-vacuous
        _no_leak(problems)
        _ref_logged(problems, caplog)
        monkeypatch.setattr(rt.ingest, "load_connection", _boom)
        r = c.get(f"/adapter-setup/{SID}")
        assert r.status_code == 404 and r.json()["refusal"]["code"] == "UNKNOWN_SOURCE"
        _no_leak(r.text)
        monkeypatch.setattr(rt.ingest, "list_connections", _boom)
        r = c.get("/adapter-setup/sources")
        assert r.status_code == 500
        _no_leak(r.text)
        _ref_logged(r.text, caplog)
    rt.ingest, rt.manager = None, rt.manager
    rt.problems = [f"databridge_mcp_ingest: {LEAK}"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        for url in ("/adapter-setup/sources", f"/adapter-setup/{SID}"):
            r = c.get(url)
            assert r.status_code == 503 and r.json()["refusal"]["code"] == "SETUP_UNAVAILABLE"
            _no_leak(r.text)
            _ref_logged(r.text, caplog)


def test_setup_explain_failure_is_a_fixed_sentence_and_a_reference(estate, monkeypatch, caplog):
    from databridge_adapter_agents import explain

    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        monkeypatch.setattr(explain, "explain_mapping", _boom)
        r = c.post(f"/adapter-setup/{SID}/explain")
        assert r.status_code == 500 and r.json()["refusal"]["code"] == "EXPLAIN_FAILED", r.text
        _no_leak(r.text)
        assert r.json()["refusal"]["error_ref"] == _ref_logged(r.text, caplog)


def test_setup_handoff_failure_is_a_fixed_sentence_and_a_reference(estate, monkeypatch, caplog):
    from databridge_adapter_agents import adapt

    from databridge_gateway import adapter_setup_routes

    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
        # past the readiness gate, the first package call inside the hand-off raises
        monkeypatch.setattr(adapter_setup_routes, "_gate",
                            lambda rt, conn: {"problems": [], "pending": []})
        monkeypatch.setattr(adapt, "Policy", types.SimpleNamespace(of=_boom))
        r = c.post(f"/adapter-setup/{SID}/handoff", json={"deferred": []})
        assert r.status_code == 409, r.text
        assert r.json()["refusal"]["code"] == "ADAPTER_AGENT_NOT_ACTIVATABLE"
        _no_leak(r.text)
        assert r.json()["refusal"]["error_ref"] == _ref_logged(r.text, caplog)


def test_a_machine_documents_decision_answers_the_standard_code(estate, monkeypatch):
    """#11: the documents approval refused a machine as NOT_A_PERSON; it now answers the standard
    MACHINE_ACTOR (the former code kept as reason_code), as the hand-off does."""
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
    machine = _agent_token(monkeypatch, keeper_of())
    with TestClient(estate["app"], headers=machine) as c:
        r = c.post(f"/adapter-setup/{SID}/documents/approve",
                   json={"discovery_run_id": "x", "documents_digest": "y"})
        body = r.json()["refusal"]
        assert (body["code"], body["reason_code"]) == ("MACHINE_ACTOR", "NOT_A_PERSON"), body
        assert body["message"] == public_errors.FIXED_MESSAGES["MACHINE_ACTOR"]
        r = c.post(f"/adapter-setup/{SID}/handoff", json={"deferred": []})
        assert r.json()["refusal"]["code"] == "MACHINE_ACTOR"               # the same code


# =================================================================================================
# #11 / #7 — the batch answers standard codes and the actor object
# =================================================================================================
def test_a_batch_approval_of_an_approved_plan_is_already_final_and_names_the_actor(env):
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "fin").status_code == 200
        rt.ingest.plans["fin"] = FakePlan(source_id="fin", steps=["documents"], status="approved")
        digest = c.get("/sources/fin").json()["plan_digest"]
        r = c.post("/source-batch/plans/approve", json={"items": [
            {"source_id": "fin", "plan_digest": digest}]})
        assert r.status_code == 200, r.text
        row = r.json()["results"][0]
        assert (row["code"], row["reason_code"]) == (STANDARD_FOR["ALREADY_APPROVED"],
                                                     "ALREADY_APPROVED"), row
        actor = r.json()["actor"]
        assert actor["principal_id"] == "analyst-7"                   # never decorated
        assert set(actor) >= {"principal_id", "authenticated_by", "principal_type", "dev_only"}
        assert actor["dev_only"] is False and actor["principal_type"] != "machine"


# =================================================================================================
# #6 / #10 — materialise
# =================================================================================================
def _alice_design(c, name) -> dict:
    r = c.post("/materialise/designs", json={"request": REQUESTS[name]}, headers=AUTH_HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


def test_materialise_owner_or_operator_with_one_404_body(world, monkeypatch):
    name, _e, c = world
    doc = _alice_design(c, name)
    bob = auth_headers("analyst-7")
    monkeypatch.setenv("WALNUT_OPERATORS", "fcc-3")
    missing = c.get("/materialise/designs/dsg-000000000000", headers=bob)
    other = c.get(f"/materialise/designs/{doc['id']}", headers=bob)
    assert missing.status_code == other.status_code == 404
    assert missing.content == other.content                          # byte-identical
    assert doc["id"] not in {d["id"] for d in
                             c.get("/materialise/designs", headers=bob).json()["designs"]}
    # a job of Alice's: Bob 404 (the same body as a missing job), an operator sees it
    jobs = materialise_routes._jobs(c.app)
    job = jobs.start(materialise_routes.JOB_KIND, "r028-k", by="staff")
    jobs.finish(job["job_id"], status="succeeded", result={})
    j_missing = c.get("/materialise/jobs/no-such-job", headers=bob)
    j_other = c.get(f"/materialise/jobs/{job['job_id']}", headers=bob)
    assert j_missing.status_code == j_other.status_code == 404
    assert j_missing.content == j_other.content
    op = auth_headers("fcc-3")
    assert c.get(f"/materialise/designs/{doc['id']}", headers=op).status_code == 200
    assert doc["id"] in {d["id"] for d in
                         c.get("/materialise/designs", headers=op).json()["designs"]}
    assert c.get(f"/materialise/jobs/{job['job_id']}", headers=op).status_code == 200
    assert job["job_id"] in {j["job_id"] for j in
                             c.get("/materialise/jobs", headers=op).json()["jobs"]}
    # deciding stays the owner's: an operator cannot approve another principal's design
    r = c.post(f"/materialise/designs/{doc['id']}/approve", json={"digest": doc["digest"]},
               headers=op)
    assert r.status_code == 404 and r.content == missing.content


def test_materialise_jobs_and_handlers_never_show_the_exception(world, monkeypatch, caplog):
    name, e, c = world
    app = c.app
    impl = materialise_routes._materialiser(app)
    jobs = materialise_routes._jobs(app)
    doc = _alice_design(c, name)            # before anything below is patched
    # the labelled run's job
    job = jobs.start(materialise_routes.JOB_KIND, "r028-run", by="staff")
    materialise_routes._run_job(app, types.SimpleNamespace(run=_boom), {}, "staff",
                                job["job_id"])
    got = jobs.get(job["job_id"])
    assert got["status"] == "failed"
    _no_leak(got)
    assert got["error_ref"] == _ref_logged(got, caplog)
    # a CODED refusal raised while handling an internal error: its words are withheld too
    def coded(*_a, **_kw):
        try:
            raise OSError(LEAK)
        except OSError:
            from databridge_materialise.platform import RunRefused
            raise RunRefused("PLAN_INVALID", f"not a MaterialisationPlan: {LEAK}") from None
    job = jobs.start(materialise_routes.JOB_KIND, "r028-coded", by="staff")
    materialise_routes._run_job(app, types.SimpleNamespace(run=coded), {}, "staff",
                                job["job_id"])
    got = jobs.get(job["job_id"])
    assert got["status"] == "refused" and got["refusal"]["code"] == "PLAN_INVALID"
    _no_leak(got)
    _ref_logged(got["refusal"], caplog)
    # the design build job
    monkeypatch.setattr(materialise_routes, "_federation", lambda _app: None)
    job = jobs.start(materialise_routes.DESIGN_JOB_KIND, "r028-design", by="staff")
    materialise_routes._run_design_job(app, types.SimpleNamespace(build_design=_boom), "dsg-x",
                                       "d", "staff", None, job["job_id"])
    got = jobs.get(job["job_id"])
    assert got["status"] == "failed"
    _no_leak(got)
    assert got["error_ref"] == _ref_logged(got, caplog)
    # the proposal, edit and approval handlers
    for meth in ("propose_design", "edit_design", "approve_design"):
        monkeypatch.setattr(impl, meth, _boom)
    for method, url, body in (
            ("post", "/materialise/designs", {"request": REQUESTS[name]}),
            ("put", f"/materialise/designs/{doc['id']}", {"rev": doc["rev"], "label": None}),
            ("post", f"/materialise/designs/{doc['id']}/approve", {"digest": doc["digest"]})):
        r = getattr(c, method)(url, json=body, headers=AUTH_HEADERS)
        assert r.status_code == 500 and r.json()["refusal"]["code"] == "DESIGN_FAILED", url
        _no_leak(r.text)
        _ref_logged(r.text, caplog)


def test_the_synchronous_materialiser_failure_is_a_fixed_sentence(world, caplog):
    _name, _e, c = world

    class Sync:
        def plans(self):
            return []

        def run(self, plan, *, by):
            raise Leaky(LEAK)

    materialise_routes.set_materialiser(c.app, Sync())
    r = c.post("/materialise", json={"shape": "tabular"}, headers=AUTH_HEADERS)
    assert r.status_code == 503 and r.json()["refusal"]["code"] == "MATERIALISATION_FAILED"
    _no_leak(r.text)
    _ref_logged(r.text, caplog)


def test_every_person_setup_step_records_the_actor_object(estate):
    """#7: a person's setup step is recorded with the common actor object of the signed-in
    principal (never a bare string, never `actor: null`)."""
    from databridge_adapter_agents.setup import setup_records

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
    records = setup_records(rt.manager.runs_root, keeper_of())
    person = [r for r in records if r.get("actor_kind") == "person"]
    assert {r["steps"][0]["tool"] for r in person} >= {"setup.created", "setup.approve_plan"}
    for r in person:
        actor = r["actor"]
        assert actor is not None and "actor_unrecorded" not in r, r
        assert actor["principal_id"] == PERSON
        assert set(actor) >= {"principal_id", "authenticated_by", "principal_type", "dev_only"}
