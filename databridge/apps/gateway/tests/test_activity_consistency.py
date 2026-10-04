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

import collections
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
    SEALED_CHAINED,
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
    """The principal the gateway's authentication layer issued for a request (what every gateway
    route hands the shared approval gate), and the actor dict that gate records for it (exactly
    what the producers store)."""
    from databridge_mcp_ingest.plans import require_authenticated_approver

    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.platform_runtime import register_gateway_authenticators

    register_gateway_authenticators()
    principal = AuthenticatedPrincipal(principal_id=pid, dev_mode=dev)
    return principal, require_authenticated_approver(principal)


def _runtime_approver(pid: str, *, dev: bool) -> object:
    """The agent runtime's `Approver` for the same signed-in person — what the gateway hands the
    runtime when that person decides an agent's action (`platform_runtime.approver_for`)."""
    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.platform_runtime import approver_for

    return approver_for(AuthenticatedPrincipal(principal_id=pid, dev_mode=dev))


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
    written: dict[str, list[dict]] = collections.defaultdict(list)

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
    # R-029 TIDY2: as the gateway's build route does, the build names the full actor object
    store.record_build(view["id"], by=OWNER, build_id="b-1", rev=1, digest=view["digest"],
                       actor=who)
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
        "status": "completed", "models_used": [],
        "actions": [{"name": "log_one_follow_up_task", "automation_mode": "human_required",
                     "status": "executed", "by": OWNER}],
        "answer": {"markdown": "1", "produced_by": None}})
    once_path = run_path("run-once-1", agent="once_ran", runs_dir=runs)
    write_run(rec, once_path)
    # E2E run 5 W-09: OWNER approves the task's action through the agent runtime, which records
    # how the decider authenticated with ITS mechanism name (`runtime._decided_by`)
    from databridge_agent_runtime import ActionLog
    from databridge_agent_runtime.runs import actions_path
    from databridge_agent_runtime.runtime import _decided_by

    rt_approver = _runtime_approver(OWNER, dev=False)
    alog = ActionLog(actions_path(once_path))
    for ev in ({"event": "proposed", "at": "2026-10-02T09:06:01+00:00",
                "tool": "source.write:db.execute_sql", "automation_mode": "human_required"},
               {"event": "approved", "by": OWNER, **_decided_by(rt_approver),
                "idempotency_key": "run-once-1/a1", "at": "2026-10-02T09:06:02+00:00"},
               {"event": "executed", "by": OWNER, **_decided_by(rt_approver),
                "at": "2026-10-02T09:06:03+00:00", "result_pointers": []}):
        alog.append({"action_id": "a1", "name": "log_one_follow_up_task", **ev})
    written["approval"].append({"who": OWNER})
    written["one_time_task"] += [{"task": "once_ran"}, {"task": "once_failed"}]

    # -- E2E run 5 F-12: OWNER lifts a screen hold (the sealed override log) -------------------
    from databridge_mcp_ingest.screen import approve_override, content_digest

    held = content_digest("the held item's captured bytes")
    approve_override(sid, "item-held-1", digest=held, principal=approver, reason=SECRET,
                     root=src_dir)
    written["screen_override"].append({"who": who, "decision": "lifted", "digest": held})

    # -- E2E run 5 F-12: OWNER approves a declared identity rule (the ontology state + the
    #    gateway's audit of how the decider signed in), written by the producers' own writers ---
    from databridge_graph import ontology_state
    from databridge_graph.packs import load_pack

    from databridge_gateway import identity_rules_routes

    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "ontology" / "state.json"))
    (tmp_path / "ontology").mkdir()
    pack = load_pack(name)
    rule_kind = sorted(k for k in pack.node_kinds if k not in set(pack.subject_kinds))[0]
    declared = type("Declared", (), {"pack": name, "rule_types": ("exact_name",)})()
    vocab = type("Vocab", (), {"packs": (name,),
                               "identity_rule_for": staticmethod(
                                   lambda k: declared if k == rule_kind else None)})()
    rule_rec = ontology_state.record_identity_rule_decision(
        pack=name, kind=rule_kind, rule="exact_name", principal=who["principal_id"],
        vocab=vocab, at="2026-10-02T09:07:00+00:00")
    identity_rules_routes._append_audit({**rule_rec, "authenticated_by": who["authenticated_by"],
                                         "dev_only": bool(who["dev_only"])})
    written["identity_rule_decision"].append({"who": who, "kind": rule_kind})

    app = FastAPI()
    app.state.activity_sources = ActivitySources(
        runs, tmp_path / "firings.jsonl", aa_dir, sources_dir=src_dir, materialise_root=mat,
        one_time_root=once, outbox_db=outbox_db, setup_decisions=True,
        ontology_state=ontology_state.state_path(),
        identity_rule_audit=identity_rules_routes.audit_path())
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
    # (E2E run 5 W-09: the producer's principal CLASS is served as the one type vocabulary, and a
    # mechanism under the canonical name the gateway gives it — derived by the same rule)
    from databridge_gateway.activity_actor import canonical_mechanism, canonical_type

    for kind, rec in stored.items():
        [row] = got[kind]
        for k in keys:
            want = rec[k] if k in rec else absent()
            if k == "principal_type" and k in rec:
                want = canonical_type(rec[k], rec.get("principal_id"))
            if k == "authenticated_by" and k in rec:
                want = canonical_mechanism(rec[k])
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
                         "setup_documents", "setup_handoff", "adapter_setup",
                         "screen_override"}, set(kept)
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


# ------------------------------------------------------------------------------------------------
# E2E run 5 (wiki/40-reviews/E2E-2026-10-02-run5.md)
# ------------------------------------------------------------------------------------------------
def _rows_with_actors(items: list[dict]):
    """Every actor object a feed serves: each row's ``actor`` and, on a run, ``started_by``."""
    for i in items:
        yield i["kind"], "actor", i["actor"]
        if isinstance(i.get("started_by"), dict):
            yield i["kind"], "started_by", i["started_by"]


def test_r5_w09_every_family_serves_the_identical_actor_schema(estate, monkeypatch):
    """W-09: the agent runtime's approval rows said ``{person, gateway:auth}`` while every other
    family said ``{AuthenticatedPrincipal, gateway}`` for the SAME signed-in person. Every family
    now serves one schema: the same keys, ``principal_type`` from one closed vocabulary (a person,
    or one of the machine types), and one name per authentication mechanism."""
    from databridge_gateway.activity_actor import MACHINE_TYPES, PERSON

    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    items = _feed(estate["app"], OPERATOR)["items"]
    keys = {"principal_id", "authenticated_by", "principal_type", "dev_only"}
    families: set[str] = set()
    person_shapes: dict[tuple, set] = collections.defaultdict(set)
    person_families: dict[tuple, set] = collections.defaultdict(set)
    for kind, field, a in _rows_with_actors(items):
        families.add(kind)
        assert keys <= set(a) <= keys | {"delegation"}, (kind, field, a)
        for k in keys:
            v = a[k]
            assert v is not None and (is_envelope(v) or isinstance(v, (str, bool))), (kind, k, a)
        if isinstance(a["dev_only"], bool):
            assert isinstance(a["authenticated_by"], str), (kind, a)
        ptype = a["principal_type"]
        if is_envelope(ptype):
            continue
        assert ptype in (PERSON, *MACHINE_TYPES), (kind, field, a)
        if ptype in MACHINE_TYPES:
            # authentication and the dev token do not apply to a machine: one envelope, always
            assert a["authenticated_by"] == absent(MACHINE_ACTOR), (kind, a)
            assert a["dev_only"] == absent(MACHINE_ACTOR), (kind, a)
        elif isinstance(a["dev_only"], bool):
            who = (a["principal_id"], a["dev_only"])
            person_shapes[who].add((ptype, a["authenticated_by"]))
            person_families[who].add(kind)
    # non-vacuous: every family the fixture wrote was walked, the runtime's approval among them,
    # and one person's real sign-in was recorded by several producers
    assert set(WRITTEN_KINDS) | {"approval", "adapter_setup"} <= families, families
    real = (OWNER, False)
    assert {"approval", "mapping_approval", "screen_override"} <= person_families[real], \
        person_families[real]
    for who, shapes in person_shapes.items():
        assert len(shapes) == 1, (who, shapes, person_families[who])


def test_r5_w09_mechanism_and_type_names_are_read_from_the_gateway_not_written_down():
    """The canonical names are the gateway's own: the agent runtime's mechanism name for the
    signed-in path (`platform_runtime.GATEWAY_MECHANISM`) is the same mechanism as the auth
    layer's (`auth.GATEWAY_MECHANISM`); a recorded principal class is a person unless it is a
    machine type (``AdapterAgent`` is the ``adapter_agent`` machine)."""
    from databridge_adapter_agents.setup import agent_actor

    from databridge_gateway import auth, platform_runtime
    from databridge_gateway.activity_actor import PERSON, make_actor

    rt = actor_from_record({"by": OWNER, "authenticated_by": platform_runtime.GATEWAY_MECHANISM,
                            "dev_only": False}, pid_key="by", default_type="person")
    gw = make_actor(OWNER, auth.AuthenticatedPrincipal.__name__,
                    authenticated_by=auth.GATEWAY_MECHANISM, dev_only=False)
    assert rt == gw and gw["principal_type"] == PERSON
    assert gw["authenticated_by"] == auth.GATEWAY_MECHANISM
    dev = make_actor(OWNER, "Approver", authenticated_by=platform_runtime.GATEWAY_DEV_MECHANISM,
                     dev_only=True)
    assert dev["authenticated_by"] == auth.DEV_MECHANISM
    machine = actor_from_record(agent_actor("keeper-x"))
    assert machine["principal_type"] == "adapter_agent"
    assert machine["authenticated_by"] == absent(MACHINE_ACTOR)
    # a delegated decision keeps its own mechanism and its delegation
    deleg = make_actor(OWNER, "DelegatedApproval", authenticated_by="delegated:abc",
                       dev_only=False, delegation={"policy_digest": "abc"})
    assert deleg["principal_type"] == PERSON and deleg["authenticated_by"] == "delegated:abc"
    assert deleg["delegation"] == {"policy_digest": "abc"}


def test_r5_f12_a_hold_lift_and_an_identity_rule_decision_reach_activity(estate, monkeypatch):
    """F-12: lifting a screen hold and approving an identity rule both WIDEN what the graph holds;
    neither reached Activity. Each is now a row of its own kind with the common actor object,
    what was verified about it, owner-or-operator visibility, and its free text withheld."""
    got = _by_kind(_feed(estate["app"], OWNER)["items"])
    w = estate["written"]
    [lift] = got["screen_override"]
    assert lift["decision"] == "lifted" and lift["source_id"] == estate["sid"]
    assert lift["item_id"] == "item-held-1" and lift["digest"] == w["screen_override"][0]["digest"]
    assert lift["reason"] == withheld(FREE_TEXT)
    for k in ("principal_id", "authenticated_by", "dev_only"):
        assert lift["actor"][k] == w["screen_override"][0]["who"][k], (k, lift["actor"])
    assert lift["integrity"] == SEALED_CHAINED
    [rule] = got["identity_rule_decision"]
    assert rule["decision"] == "approved" and rule["rule"] == "exact_name"
    assert rule["node_kind"] == w["identity_rule_decision"][0]["kind"]
    for k in ("principal_id", "authenticated_by", "dev_only"):
        assert rule["actor"][k] == w["identity_rule_decision"][0]["who"][k], (k, rule["actor"])
    assert rule["integrity"] == UNVERIFIED
    assert SECRET not in json.dumps(got)
    # the kind filter selects exactly that family
    for kind in ("screen_override", "identity_rule_decision"):
        assert {i["kind"] for i in _feed(estate["app"], OWNER, kinds=kind)["items"]} == {kind}
    # the keeper's creator sees the hold lift on its source; nobody else sees either; an
    # operator sees both
    kept = _by_kind(_feed(estate["app"], KEEPER)["items"])
    assert "screen_override" in kept and "identity_rule_decision" not in kept
    assert _feed(estate["app"], OTHER)["items"] == []
    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    ops = _by_kind(_feed(estate["app"], OPERATOR)["items"])
    assert len(ops["screen_override"]) == len(ops["identity_rule_decision"]) == 1


def test_r5_f12_a_tampered_override_log_is_an_integrity_row_not_a_decision(estate):
    from databridge_mcp_ingest.screen import override_log_path

    path = override_log_path(estate["sid"], estate["src"])
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    rec["principal_id"] = OTHER                                   # an edited lift
    path.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    got = _by_kind(_feed(estate["app"], KEEPER)["items"])
    assert "screen_override" not in got
    [bad] = got["decision_log"]
    assert bad["refusal"]["code"] == LEDGER_INTEGRITY_FAILED
    assert bad["family"] == "screen_override"
    assert _feed(estate["app"], OTHER)["items"] == []


def test_r5_f12_an_unreadable_identity_state_is_an_integrity_row_for_operators_only(
        estate, monkeypatch):
    state = estate["app"].state.activity_sources.ontology_state
    state.write_text("{not json", encoding="utf-8")
    mine = _by_kind(_feed(estate["app"], OWNER)["items"])
    assert "identity_rule_decision" not in mine and "decision_log" not in mine
    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    ops = _by_kind(_feed(estate["app"], OPERATOR)["items"])
    assert "identity_rule_decision" not in ops
    assert [r["family"] for r in ops["decision_log"]] == ["identity_rule_decision"]


def test_r5_f12_reading_the_hold_lifts_never_creates_the_approval_key(estate, monkeypatch,
                                                                       tmp_path):
    """Read-only never writes: with no key in the environment and no key file, the lifts cannot
    be verified, so none is shown — and reading creates no key file."""
    from databridge_mcp_ingest import screen

    key_file = tmp_path / "no-such-dir" / "approval.key"
    monkeypatch.delenv(screen.APPROVAL_KEY_ENV)
    monkeypatch.setenv(screen.APPROVAL_KEY_FILE_ENV, str(key_file))
    body = _feed(estate["app"], OWNER, kinds="screen_override")
    assert body["items"] == []
    # non-vacuous: the family was read, and said it could not be verified
    assert "screen_override" in (body.get("counts_incomplete") or []), body
    assert not key_file.exists() and not key_file.parent.exists()
