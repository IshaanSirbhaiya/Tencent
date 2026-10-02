"""R-028 CONS (ACTV): Activity is ONE consistent audit timeline across every decision producer.

Codex R-028 consistency review:

* **#8** — explicit Activity kinds for every producer family, each projected through the common
  actor object: Set-up steps (``adapter_setup``, never ``adapter_sync``), source plan approvals,
  mapping and link approvals, dataset design approvals and builds, setup document decisions and
  hand-offs, outbox decisions and one-time tasks;
* **#7** — actors come from the structured object ``{principal_id, authenticated_by,
  principal_type, dev_only, delegation?}``; a dev-token decision is ``dev_only: true`` with an
  undecorated id;
* **#12** — withheld / absent values are the envelope ``{status, reason}`` IN PLACE;
* **#10** — owner OR operator (`agent_ownership.is_operator`), identical 404 bodies.

Every fixture is written by the PRODUCER'S OWN writer (`plans._append_approval`,
`mapping._append_approval` / `reject_link` / `save_mapping`, `setup.record_documents_decision` /
`record_authorization` / `record_step`, `DesignStore` + `delegated.approve`, `OutboxStore`, the
one-time route's `_write_task`, `write_run`), with an approver the gateway's authentication layer
issued. Every expectation is derived from what was written, on both estates.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_DATABRIDGE = Path(__file__).resolve().parents[3]
if str(_DATABRIDGE / "packages" / "materialise") not in sys.path:    # as the design-route tests
    sys.path.insert(0, str(_DATABRIDGE / "packages" / "materialise"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import auth_headers

from databridge_gateway import agent_ownership
from databridge_gateway.activity_actor import (
    ABSENT,
    FREE_TEXT,
    LEGACY_DEV_SUFFIX,
    MACHINE_ACTOR,
    absent,
    actor_from_record,
    is_envelope,
    withheld,
)
from databridge_gateway.activity_decisions import (
    APPEND_ONLY,
    LEDGER_INTEGRITY_FAILED,
    SEALED,
    UNVERIFIED,
    _producer_on_ledger,
)
from databridge_gateway.activity_routes import (
    ACTIVITY_KINDS,
    DECISION_KINDS,
    ActivitySources,
    register_activity_routes,
)

ROOT = Path(__file__).resolve().parents[3]
OWNER = "analyst-7"          # decides everything below
KEEPER = "staff"              # created the source's keeper agent: sees its source's decisions
OTHER = "fcc-3"              # owns nothing here
OPERATOR = "audit-1"
SECRET = "zz-free-text-4417 C:/secret/path"
ESTATES = ("workspace", "finance")
#: Kinds this fixture writes a record of that OWNER (the decider) sees. A Set-up step lives with
#: the keeper agent's records, so it is its creator's (KEEPER's) — tested through KEEPER.
WRITTEN_KINDS = tuple(k for k in DECISION_KINDS if k != "decision_log") + ("one_time_task",)


def _who(pid: str, *, dev: bool) -> tuple[object, dict]:
    """An approver the gateway's authentication layer issued, and the actor dict the shared
    approval gate records for it (exactly what every producer stores)."""
    from databridge_mcp_ingest.plans import require_authenticated_approver

    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.platform_runtime import approver_for, register_gateway_authenticators

    register_gateway_authenticators()
    approver = approver_for(AuthenticatedPrincipal(principal_id=pid, dev_mode=dev))
    return approver, require_authenticated_approver(approver)


def _pointer_json() -> str:
    from databridge_core.provenance import SourcePointer

    return SourcePointer(source_id="chit", kind="doc_span", document_id="chit", char_start=0,
                         char_end=4).model_dump_json(exclude_none=True)


@pytest.fixture(params=ESTATES)
def estate(request, tmp_path, monkeypatch):
    """One source per estate, every decision family written for it by OWNER (dev token on the
    plan approval and the outbox approval, a real sign-in elsewhere)."""
    from databridge_adapter_agents.registry import AdapterAgentRegistry
    from databridge_adapter_agents.setup import (
        draft_spec,
        keeper_id,
        record_authorization,
        record_documents_decision,
        record_step,
    )
    from databridge_agent_runtime import RunRecord, run_path
    from databridge_agent_runtime.runs import write_run
    from databridge_materialise import delegated
    from databridge_mcp_ingest import mapping as mp
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.screen import APPROVAL_KEY_ENV
    from databridge_outbox.store import OutboxStore
    from databridge_workflow_spec import AdapterAgentSpec

    from databridge_gateway import one_time_routes
    from databridge_gateway.outbox_routes import DraftOwners

    name = request.param
    assert (ROOT / "packs" / name).is_dir(), name          # a real estate, not a label
    monkeypatch.setenv("WALNUT_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv(APPROVAL_KEY_ENV, "activity-consistency-key-0123456789abcdef")
    monkeypatch.setenv(delegated.ANCHOR_ENV, str(tmp_path / "anchors" / "designs.jsonl"))
    monkeypatch.delenv(agent_ownership.OPERATORS_ENV, raising=False)
    src_dir, aa_dir = tmp_path / "sources", tmp_path / "aa"
    runs, mat, once = tmp_path / "runs", tmp_path / "mat", tmp_path / "once"
    outbox_db = tmp_path / "outbox" / "outbox.sqlite"
    for d in (src_dir, aa_dir, runs, mat, once, outbox_db.parent):
        d.mkdir(parents=True, exist_ok=True)
    sid = f"{name}_ledger"
    written: dict[str, list[dict]] = {k: [] for k in ACTIVITY_KINDS}

    # -- KEEPER created the source's keeper agent (Set up's `keeper-<source>`) ---------------
    reg = AdapterAgentRegistry(aa_dir)
    keeper = keeper_id(sid)
    spec = AdapterAgentSpec.model_validate({**draft_spec(sid, name), "status": "draft"})
    reg.create(spec, by=KEEPER)

    # -- plan approval (dev token) -------------------------------------------------------------
    _a, who_dev = _who(OWNER, dev=True)
    plan_line = plans._append_approval(sid, {"event": "plan_approved", "source_id": sid,
                                             **who_dev, "at": "2026-10-02T09:00:00+00:00",
                                             "plan_digest": "p" * 64, "tools_digest": "t",
                                             "vouched_read_tools": ["read_rows"]}, src_dir)
    written["plan_approval"].append({"who": who_dev, "hash": plan_line["hash"]})

    # -- mapping approval + link decisions (a real sign-in) ------------------------------------
    approver, who = _who(OWNER, dev=False)
    mp._append_approval(sid, {"action": "approve", "source_id": sid, "table": "invoices",
                              **who, "at": "2026-10-02T09:01:00+00:00", "digest": "m" * 64},
                        src_dir)
    written["mapping_approval"].append({"who": who, "table": "invoices"})
    link = {"column": "customer_ref", "source": sid, "table": "customers", "key": "id",
            "target_kind": "account", "edge": {"kind": "billed_to", "src": "invoice",
                                               "dst": "account"}}
    # a link decision is a decision in the mapping's ledger (R-028): the approval through the
    # producer's own logger, the rejection through `reject_link` itself
    m = mp.SourceMapping(source_id=sid, tables={"invoices": mp.TableMapping(
        status="proposed", links=[{**link, "column": "owner_ref", "status": "proposed"}])})
    mp.save_mapping(m, src_dir)
    mp._log_link_decision(sid, "invoices", link, "approve", who, "2026-10-02T09:02:00+00:00",
                          src_dir)
    mp.reject_link(sid, "invoices", "owner_ref", principal=approver, reason=SECRET, root=src_dir)
    written["link_decision"] += [{"who": who, "decision": "approved"},
                                 {"who": who, "decision": "rejected"}]

    # -- setup: a step OWNER decided, the documents decision and the hand-off ------------------
    step = record_step(runs, spec, step="approve_mapping", status="completed", by=OWNER,
                       reason="approved the mapping", actor="person")
    written["adapter_setup"].append({"run_id": step["run_id"]})
    record_documents_decision(aa_dir, sid, who=who, discovery={"run_id": "disc-1"})
    written["setup_documents"].append({"who": who})
    record_authorization(aa_dir, sid, keeper, who=who,
                         binding={"plan_digest": "p" * 64, "mapping": {"invoices": "m"},
                                  "deferred": ["x"]})
    written["setup_handoff"].append({"who": who})

    # -- a dataset design OWNER proposed, approved and built (the sealed, anchored log) --------
    store = delegated.DesignStore(mat)
    design = delegated.TableDesign(request="invoices by customer", grain=delegated.SourceRef(
        adapter_agent=keeper, connection=sid, entity="invoices"), grain_key="id")
    view = store.create(design, owner=OWNER, produced_by={"agent": "rules"})
    delegated.approve(store, view["id"], principal=approver, digest=view["digest"], owner=OWNER)
    store.record_build(view["id"], by=OWNER, build_id="b-1", rev=1, digest=view["digest"])
    written["design_approval"].append({"who": who, "design": view["id"]})
    written["design_build"].append({"design": view["id"]})

    # -- the outbox: OWNER filed and approved one draft (dev token), rejected another ----------
    ob = OutboxStore(str(outbox_db))
    owners = DraftOwners(str(outbox_db.with_name(outbox_db.stem + ".owners.sqlite")))
    for draft_id in ("d-approve", "d-reject"):
        with ob._lock:
            ob._insert({"draft_id": draft_id, "status": "drafted", "channel": "email",
                        "recipient_address": "x@example.org",
                        "recipient_origin": "typed_by_human", "recipient_chosen_by": OWNER,
                        "recipient_pointer_json": _pointer_json(), "subject": SECRET,
                        "body": SECRET, "intake_id": None, "playbook_id": "pb-1",
                        "because_json": "[]", "created_at": "2026-10-02T09:05:00+00:00",
                        "recorded_at": "2026-10-02T09:05:00+00:00",
                        "recorded_by": "monitor:pb-1"})
            ob.conn.commit()
        owners.record(draft_id, OWNER)
    actor_dev = {**who_dev}
    try:
        ob.approve("d-approve", approved_by=OWNER, actor=actor_dev)
    except TypeError:                         # a store without the structured actor (pre-R-028)
        ob.approve("d-approve", approved_by=f"{OWNER}{LEGACY_DEV_SUFFIX}")
    try:
        ob.reject("d-reject", rejected_by=OWNER, reason=SECRET, actor=who)
    except TypeError:
        ob.reject("d-reject", rejected_by=OWNER, reason=SECRET)
    written["outbox_decision"] += [{"who": who_dev, "decision": "approved"},
                                   {"who": who, "decision": "rejected"}]

    # -- one-time tasks: one that ran (its run record), one that failed before any run ---------
    monkeypatch.setenv(one_time_routes.ONE_TIME_DIR_ENV, str(once))
    for tid, run_id in (("once_ran", "run-once-1"), ("once_failed", None)):
        (once / tid).mkdir()
        one_time_routes._write_task(once / tid, {
            "task_id": tid, "kind": "one_time_task", "owner": OWNER, "created_from": "count",
            "created_at": "2026-10-02T09:06:00+00:00", "run_id": run_id,
            "status": "completed" if run_id else "failed",
            "error_ref": None if run_id else "once-err-abc", "attempts": 1})
    rec = RunRecord.model_validate({
        "run_id": "run-once-1", "spec_digest": "sha256:t",
        "trigger": {"type": "one_time", "by": OWNER, "question": None, "task_id": "once_ran"},
        "started": "2026-10-02T09:06:00+00:00", "ended": "2026-10-02T09:06:05+00:00",
        "status": "completed", "models_used": [], "actions": [],
        "answer": {"markdown": "1", "produced_by": None}})
    write_run(rec, run_path("run-once-1", agent="once_ran", runs_dir=runs))
    written["one_time_task"] += [{"task": "once_ran"}, {"task": "once_failed"}]

    app = FastAPI()
    app.state.activity_sources = ActivitySources(
        runs, tmp_path / "firings.jsonl", aa_dir, sources_dir=src_dir, materialise_root=mat,
        one_time_root=once, outbox_db=outbox_db, setup_decisions=True)
    register_activity_routes(app)
    return {"app": app, "sid": sid, "written": written, "src": src_dir, "mat": mat,
            "design": view["id"], "outbox": outbox_db, "who": who, "who_dev": who_dev}


def _feed(app, who: str, **params) -> dict:
    with TestClient(app, headers=auth_headers(who)) as c:
        r = c.get("/activity", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _by_kind(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in items:
        out.setdefault(i["kind"], []).append(i)
    return out


# ------------------------------------------------------------------------------------------------
# #8: every producer family is its own kind, with the common actor object
# ------------------------------------------------------------------------------------------------
def test_every_decision_family_is_its_own_kind_in_the_feed(estate):
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    w = estate["written"]
    for kind in WRITTEN_KINDS:
        assert w[kind], f"non-vacuous: the fixture wrote {kind}"
        assert len(got.get(kind, [])) == len(w[kind]), (kind, got.get(kind))
    # nothing of a one-time task is an agent run; nothing of setup is a sync (seen by KEEPER)
    assert "agent_run" not in got
    kept = _by_kind(_feed(estate["app"], KEEPER)["items"])
    assert "adapter_sync" not in kept
    [setup] = kept["adapter_setup"]
    assert setup["run_id"] == w["adapter_setup"][0]["run_id"] and setup["decided_by_person"]
    # a step a person decided names that person; how they signed in was not recorded by the
    # step's producer, so it is absent — never presented as a sign-in
    assert setup["actor"]["principal_id"] == OWNER
    assert setup["actor"]["authenticated_by"] == absent()
    tasks = {i["task_id"]: i for i in got["one_time_task"]}
    assert set(tasks) == {t["task"] for t in w["one_time_task"]}
    assert tasks["once_failed"]["status"] == "failed" and tasks["once_failed"]["loud"]
    assert tasks["once_failed"]["error_ref"] == "once-err-abc"
    # the kind filter selects exactly one family
    for kind in WRITTEN_KINDS:
        only = _feed(estate["app"], OWNER, kinds=kind)["items"]
        assert {i["kind"] for i in only} == {kind}, kind


def test_every_row_names_its_actor_with_the_common_object(estate):
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    w = estate["written"]
    keys = {"principal_id", "authenticated_by", "principal_type", "dev_only"}
    for kind in WRITTEN_KINDS:
        for row in got[kind]:
            assert keys <= set(row["actor"]), (kind, row["actor"])
            for k in keys:                     # a value, or the envelope — never a bare null
                assert row["actor"][k] is not None, (kind, k)
    # every row carries EXACTLY the actor fields its producer stored; a field the producer did
    # not store is the absent envelope (never a guess, never a bare null)
    sid = estate["sid"]
    aa = estate["app"].state.activity_sources.adapter_agents_dir
    stored = {k: w[k][0]["who"] for k in ("plan_approval", "mapping_approval",
                                           "design_approval")}
    for kind, fname in (("setup_documents", "decisions.jsonl"),
                        ("setup_handoff", "handoffs.jsonl")):
        lines = (aa / "_setup" / sid / fname).read_text(encoding="utf-8").splitlines()
        stored[kind] = json.loads([ln for ln in lines if ln.strip()][-1])
    for kind, rec in stored.items():
        [row] = got[kind]
        for k in keys:
            want = rec[k] if k in rec else absent()
            assert row["actor"][k] == want, (kind, k, row["actor"])
    # link and outbox decisions: matched per decision
    for kind in ("link_decision", "outbox_decision"):
        for want in w[kind]:
            [row] = [r for r in got[kind] if r["decision"] == want["decision"]]
            assert row["actor"]["principal_id"] == want["who"]["principal_id"]
            assert row["actor"]["dev_only"] == want["who"]["dev_only"], (kind, row["actor"])
    # the agent's own setup step / build are machines or the recorded person — never decorated
    for row in [r for rows in got.values() for r in rows]:
        pid = row["actor"]["principal_id"]
        assert not (isinstance(pid, str) and LEGACY_DEV_SUFFIX in pid), row


def test_a_dev_token_decision_is_dev_only_true_and_never_a_decorated_id(estate):
    """#7: the plan and the outbox approval were made with the dev token; a real sign-in made the
    rest. The row says so in `dev_only`, and the id is the plain principal."""
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    [plan] = got["plan_approval"]
    assert plan["actor"]["dev_only"] is True and plan["actor"]["principal_id"] == OWNER
    [ob] = [r for r in got["outbox_decision"] if r["decision"] == "approved"]
    assert ob["actor"]["dev_only"] is True and ob["actor"]["principal_id"] == OWNER
    [mapping] = got["mapping_approval"]
    assert mapping["actor"]["dev_only"] is False


def test_a_legacy_decorated_outbox_name_is_read_back_into_the_structured_actor():
    """The outbox wrote ``"<id> (dev token)"`` before R-028; Activity never shows that as an id."""
    got = actor_from_record({"approved_by": f"{OWNER}{LEGACY_DEV_SUFFIX}"}, pid_key="approved_by")
    assert got["principal_id"] == OWNER and got["dev_only"] is True
    assert got["authenticated_by"] == absent()
    plain = actor_from_record({"approved_by": OWNER}, pid_key="approved_by")
    assert plain["principal_id"] == OWNER and plain["dev_only"] == absent()
    machine = actor_from_record({"by": "adapter_agent:keeper-x"}, pid_key="by")
    assert machine["principal_type"] == "adapter_agent"
    assert machine["authenticated_by"] == absent(MACHINE_ACTOR)
    # every decoration the core `Actor` refuses is read back, never shown as an id
    from databridge_core.decision_ledger import _DECORATED, is_machine_principal
    for decorated in (f"{OWNER} (Dev-Only)", f"{OWNER}(dev token)", f"{OWNER} (dev_login)"):
        assert _DECORATED.search(decorated)                         # the core's own rule
        got = actor_from_record({"by": decorated}, pid_key="by")
        assert got["principal_id"] == OWNER and got["dev_only"] is True, decorated
    # a machine principal by the core's rule is never shown as a person
    assert is_machine_principal("workflow:w1")
    assert actor_from_record({"by": "workflow:w1"}, pid_key="by")["principal_type"] == "agent"


def test_each_row_says_what_was_verified_about_it(estate):
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    # setup logs are the ledger's once their producer writes through it; plain JSONL until then
    setup = SEALED if _producer_on_ledger("databridge_adapter_agents.setup") else UNVERIFIED
    want = {"plan_approval": SEALED, "mapping_approval": SEALED, "link_decision": SEALED,
            "setup_documents": setup, "setup_handoff": setup, "design_approval": SEALED,
            "design_build": SEALED, "outbox_decision": APPEND_ONLY}
    for kind, integrity in want.items():
        assert {r["integrity"] for r in got[kind]} == {integrity}, kind


# ------------------------------------------------------------------------------------------------
# #12: withheld and absent values are the envelope, in place
# ------------------------------------------------------------------------------------------------
def test_free_text_is_withheld_in_place_and_never_served(estate):
    body = _feed(estate["app"], OWNER)
    assert SECRET not in json.dumps(body)
    got = _by_kind(body["items"])
    [rejected_draft] = [r for r in got["outbox_decision"] if r["decision"] == "rejected"]
    assert rejected_draft["reason"] == withheld(FREE_TEXT)
    # an approval with no reason has none: null is "there is nothing", not "hidden"
    [approved_draft] = [r for r in got["outbox_decision"] if r["decision"] == "approved"]
    assert approved_draft["reason"] is None


def test_the_setup_step_record_withholds_unlisted_fields_in_place(estate):
    """The run record of a setup step goes through the allow-list: a field it does not name is
    the withheld envelope (and listed), not dropped."""
    w = estate["written"]["adapter_setup"][0]
    runs = estate["app"].state.activity_sources.runs_dir
    [path] = list(runs.rglob(f"{w['run_id']}.json"))
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["mystery_field"] = SECRET
    path.write_text(json.dumps(rec), encoding="utf-8")
    with TestClient(estate["app"], headers=auth_headers(KEEPER)) as c:
        r = c.get(f"/activity/runs/{w['run_id']}",
                  params={"kind": "adapter_agent", "owner": path.parent.name})
    assert r.status_code == 200, r.text
    record = r.json()["record"]
    assert SECRET not in r.text
    assert is_envelope(record["mystery_field"]) and "mystery_field" in record["masked_fields"]
    assert r.json()["item"]["kind"] == "adapter_setup"


# ------------------------------------------------------------------------------------------------
# #10: owner OR operator; identical 404 bodies
# ------------------------------------------------------------------------------------------------
def test_owner_or_operator_sees_the_decisions_and_nobody_else(estate, monkeypatch):
    mine = {i["id"] for i in _feed(estate["app"], OWNER)["items"]}
    assert mine and not any(i.startswith("adapter_setup:") for i in mine)
    assert _feed(estate["app"], OTHER)["items"] == []
    # the keeper's creator owns the source: sees its source-scoped decisions (not the outbox,
    # the design or the one-time tasks, which belong to who filed / proposed / ran them)
    kept = _by_kind(_feed(estate["app"], KEEPER)["items"])
    assert set(kept) == {"plan_approval", "mapping_approval", "link_decision",
                         "setup_documents", "setup_handoff", "adapter_setup"}, set(kept)
    # an operator (the platform rule: `agent_ownership.is_operator`) sees everything
    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    everything = mine | {i["id"] for i in _feed(estate["app"], KEEPER)["items"]}
    assert {i["id"] for i in _feed(estate["app"], OPERATOR)["items"]} == everything
    assert _feed(estate["app"], OTHER)["items"] == []


def test_a_decision_with_no_recorded_owner_is_operator_only(estate, monkeypatch):
    """An outbox draft whose owner row is missing: nobody becomes its owner by deciding it."""
    side = estate["outbox"].with_name(estate["outbox"].stem + ".owners.sqlite")
    conn = sqlite3.connect(side)
    conn.execute("DELETE FROM draft_owner")
    conn.commit()
    conn.close()
    assert "outbox_decision" not in _by_kind(_feed(estate["app"], OWNER)["items"])
    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    assert len(_by_kind(_feed(estate["app"], OPERATOR)["items"])["outbox_decision"]) == 2


def test_another_principals_run_and_a_missing_one_get_the_same_404(estate):
    w = estate["written"]["adapter_setup"][0]
    with TestClient(estate["app"]) as c:
        theirs = c.get(f"/activity/runs/{w['run_id']}", headers=auth_headers(OTHER))
        missing = c.get("/activity/runs/setup-20990101T000000-000000-none",
                        headers=auth_headers(OTHER))
        mine = c.get(f"/activity/runs/{w['run_id']}", headers=auth_headers(KEEPER))
    assert mine.status_code == 200
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content


# ------------------------------------------------------------------------------------------------
# integrity: a log that does not verify is ONE loud row, and nothing in it is a decision
# ------------------------------------------------------------------------------------------------
def test_a_tampered_plan_log_is_an_integrity_row_not_a_decision(estate):
    from databridge_mcp_ingest.plans import plan_approvals_path

    path = plan_approvals_path(estate["sid"], estate["src"])
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    line["principal_id"] = OTHER                                   # an edited approval
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    got = _by_kind(_feed(estate["app"], KEEPER)["items"])
    assert "plan_approval" not in got
    [bad] = got["decision_log"]
    assert bad["refusal"]["code"] == LEDGER_INTEGRITY_FAILED and bad["loud"]
    assert bad["family"] == "plan_approval" and bad["source_id"] == estate["sid"]
    # shown with its family's filter too, and to nobody who could not see that family
    assert [i["kind"] for i in _feed(estate["app"], KEEPER, kinds="plan_approval")["items"]] \
        == ["decision_log"]
    assert _feed(estate["app"], OTHER)["items"] == []


def test_a_tampered_design_log_is_an_integrity_row_not_a_decision(estate):
    log = estate["mat"] / "designs" / estate["design"] / "events.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["principal_id"] = OTHER
    lines[1] = json.dumps(rec, sort_keys=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    assert "design_approval" not in got and "design_build" not in got
    [bad] = got["decision_log"]
    assert bad["refusal"]["code"] == LEDGER_INTEGRITY_FAILED and bad["design_id"] == \
        estate["design"]


def test_tidy_activity_reads_designs_through_the_public_verified_reader(estate, monkeypatch):
    """R-028 TIDY #8: Activity reads a design log through `DesignStore.verified_events` (the
    public, verifying reader), never the private `_chain`."""
    from databridge_materialise import delegated

    calls: list[str] = []
    real = delegated.DesignStore.verified_events

    def spy(self, design_id):
        calls.append(design_id)
        return real(self, design_id)

    monkeypatch.setattr(delegated.DesignStore, "verified_events", spy)
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    assert got.get("design_approval")                                   # non-vacuous
    assert calls == [estate["design"]]


def test_unknown_kind_is_refused_and_the_vocabulary_is_served(estate):
    with TestClient(estate["app"], headers=auth_headers(OWNER)) as c:
        r = c.get("/activity", params={"kinds": "adapter_sync,nonsense"})
        ok = c.get("/activity").json()
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "UNKNOWN_KIND"
    assert ok["kinds"] == list(ACTIVITY_KINDS)
    assert set(DECISION_KINDS) <= set(ok["kinds"])


def test_absent_is_the_envelope_and_not_a_bare_null():
    assert absent() == {"status": ABSENT, "reason": "not_recorded_by_producer"}
    assert is_envelope(withheld(FREE_TEXT)) and not is_envelope({"status": "approved"})
