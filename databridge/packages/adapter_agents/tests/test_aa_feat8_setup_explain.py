"""FEAT8: the adapter agent explains a proposed mapping (advisory, grounded, pointers), and the
setup helpers never let the agent stand in for a person.

Both fixture estates (workspace/Larkspur and finance), read through the real `crawl.fetch_table`;
the mapping is the real rules' proposal for the estate. Expectations are derived from the estate's
own snapshots and the mapper's own output, never written down.

Codex R-027 AAX round 1: #2 (every sentence is a predeclared fact; the model only chooses ids),
#3 (no value of a person OR a protected subject reaches the model), #4/#5/#8 (the hand-off gate:
a successful discovery under the current plan, an explanation bound to it and to the current
proposal), #6 (the manager re-checks the plan's connection binding on every gate).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

MI_TESTS = Path(__file__).resolve().parents[2] / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

from test_aa_link7_fk_edges import estate, estate_snaps  # noqa: E402,F401

SRC = "feat8-src"


def _mapping(snaps, vocab):
    from databridge_mcp_ingest.mapping import SourceMapping, propose_rules

    return SourceMapping(source_id=SRC, tables=propose_rules(snaps, vocab))


def _captures(snaps):
    return {t: (s, hashlib.sha256(json.dumps(s, sort_keys=True, default=str).encode())
                .hexdigest()) for t, s in snaps.items()}


def _discovery(caps, *, run_id="20261002T000000Z-aaaaaa", status=None, plan="p1"):
    from databridge_mcp_ingest.runner import DISCOVERED

    return {"run_id": run_id, "status": status or DISCOVERED, "plan_digest": plan,
            "tables": {t: {"sha256": sha} for t, (_, sha) in caps.items()}}


def _row_id(row: dict, pk: list[str], ordinal: int) -> str:
    """The test's own statement of a row's id: the primary key's values joined, else its ordinal."""
    vals = [row.get(c) for c in pk]
    return ",".join(str(v) for v in vals) if pk and None not in vals else f"#{ordinal}"


class Harness:
    """Chooses from the payload it is sent (never a lookup table): per table every offered fact
    but the kind, in REVERSE order, then an id never offered, then a repeat. It cannot write a
    sentence: the answer schema has no field for one."""

    def __init__(self):
        self.sent: list[dict] = []

    def complete_structured(self, task_class, messages, response_model, **kw):
        assert task_class == "data_to_text"
        payload = json.loads(messages[-1]["content"])
        self.sent.append(payload)
        tables = []
        for t in payload["tables"]:
            ids = [f["id"] for f in t["facts"] if f["type"] != "kind"][::-1]
            tables.append({"table": t["table"], "reasons": [*ids, "f9999", *ids[:1]]})

        class R:
            parsed = response_model.model_validate({"tables": tables})
            event = {"model": "fake/explain"}
        return R()


def _check_facts(t: dict) -> None:
    """Every shown sentence IS an offered fact, and a fact cites only evidence bound to it."""
    facts = {f["id"]: f for f in t["facts"]}
    ev = {e["id"]: e for e in t["evidence"]}
    for claim in [t["summary"], *t["reasons"]]:
        f = facts[claim["fact"]]
        assert (claim["text"], claim["evidence"], claim["basis"]) == \
            (f["text"], f["evidence"], f["basis"])
    for f in t["facts"]:
        if f["basis"] == "capture":
            assert f["evidence"] and all(ev[i]["fact"] == f["id"] for i in f["evidence"])
            # the cited cells are of the columns the fact is about
            assert all(ev[i]["column"] in f["text"] for i in f["evidence"])
        else:
            assert f["evidence"] == []                # a proposal or a declaration cites nothing
    # every evidence item supports exactly one offered fact
    assert {e["fact"] for e in t["evidence"]} <= set(facts)


def test_rules_explanation_states_the_mappers_facts_with_resolving_pointers(estate):
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_mcp_ingest.mapping import is_person_kind, mapping_digest

    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    before = {t: mapping_digest(tm) for t, tm in m.tables.items()}
    caps = _captures(snaps)
    ex = explain_mapping(SRC, m, vocab, harness=None, agent_id="keeper-x", captures=caps)
    assert ex["advisory"] is True and ex["produced_by"]["mode"] == "rules"
    assert ex["produced_by"]["task_class"] == "data_to_text"
    assert {t["table"] for t in ex["tables"]} == set(m.tables)
    resolved = 0
    for t in ex["tables"]:
        tm, (snap, sha) = m.tables[t["table"]], caps[t["table"]]
        assert t["kind"] == (tm.node or {}).get("kind")
        assert t["is_person"] is (bool(t["kind"]) and is_person_kind(vocab, t["kind"]))
        assert t["keys"]["primary_key"] == list(snap.get("primary_key") or [])
        assert t["explained_by"] == "rules" and not t["sent_to_model"]
        assert t["capture_sha"] == sha
        _check_facts(t)
        # the summary is the mapper's PROPOSAL of the kind: it cites no cell (Codex f1 #2)
        assert t["summary"]["type"] == "kind" and t["summary"]["evidence"] == []
        assert t["summary"]["basis"] == "mapper"
        pk = list(snap.get("primary_key") or [])
        rows = {_row_id(r, pk, i): r for i, r in enumerate(snap.get("rows") or [])}
        for e in t["evidence"]:
            p = e["pointer"]
            assert (p["kind"], p["table"], p["capture_digest"]) == ("table_cell", t["table"], sha)
            raw = rows[p["row_id"]][p["column"]]
            if not e["masked"]:
                assert e["value"] == (None if raw is None else str(raw))
            else:
                assert e["value"] != str(raw)
            resolved += 1
    assert resolved > 0                                              # non-vacuous
    assert {t: mapping_digest(tm) for t, tm in m.tables.items()} == before   # advisory


def test_the_model_chooses_fact_ids_only_and_unknown_ids_are_dropped(estate):
    """Codex f1 #2: the model writes no words. What it returns is a choice of offered fact ids;
    an id never offered is dropped and counted; every sentence shown is the fact's own."""
    from databridge_adapter_agents.explain import MANDATORY, explain_mapping

    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    h = Harness()
    ex = explain_mapping(SRC, m, vocab, harness=h, agent_id="keeper-x", captures=_captures(snaps))
    sent = {t["table"]: t for p in h.sent for t in p["tables"]}
    assert sent and ex["produced_by"]["model"] == "fake/explain"
    assert any(t["explained_by"] == "model" for t in ex["tables"])     # non-vacuous
    for t in ex["tables"]:
        _check_facts(t)
        assert all(f["type"] != "mapper_reason" for f in t["facts"])   # f2 #4: no free prose
        if t["model_alias"] not in sent:
            continue
        offered = [f for f in t["facts"] if f["type"] != "kind"]
        assert t["explained_by"] == "model" and t["model_dropped"] == 1
        mandatory = [f["id"] for f in offered if f["type"] in MANDATORY]
        chosen = [f["id"] for f in offered if f["type"] not in MANDATORY][::-1][:4]
        assert [r["fact"] for r in t["reasons"]] == mandatory + chosen
        # the facts are still the mapper's, whatever the model chose
        assert t["kind"] == (m.tables[t["table"]].node or {}).get("kind")


def test_a_capture_fact_with_no_citable_cell_is_not_offered(estate):
    """Codex f1 #2: a fact of the captured table is offered only with a cell of its own column.
    With no row to cite (the table captured empty), no capture fact is offered at all - the
    mapper's proposal and the vocabulary's declaration still are, citing nothing."""
    import copy

    from databridge_adapter_agents.explain import explain_mapping

    vocab, snaps = estate["vocab"], copy.deepcopy(estate["snaps"])
    m = _mapping(snaps, vocab)
    table = next(t for t in sorted(m.tables) if m.tables[t].node and snaps[t].get("primary_key"))
    snaps[table]["rows"] = []
    ex = explain_mapping(SRC, m, vocab, harness=None, captures=_captures(snaps))
    [t] = [x for x in ex["tables"] if x["table"] == table]
    assert t["evidence"] == [] and t["keys"]["primary_key"]          # a key was declared
    assert [f for f in t["facts"] if f["basis"] == "capture"] == []
    assert any(f["type"] == "kind" for f in t["facts"])
    _check_facts(t)


def test_select_facts_keeps_only_offered_ids_and_always_the_protected_fact():
    from databridge_adapter_agents.explain import select_facts

    offered = [{"id": "f1", "type": "kind", "text": "K", "basis": "mapper", "evidence": []},
               {"id": "f2", "type": "people", "text": "P", "basis": "vocabulary",
                "evidence": []},
               {"id": "f3", "type": "primary_key", "text": "PK", "basis": "capture",
                "evidence": ["e1"]}]
    shown, dropped = select_facts(["f3", "f1", "nope", "f3"], offered)
    assert [s["fact"] for s in shown] == ["f2", "f3"] and dropped == 2
    assert [s["text"] for s in shown] == ["P", "PK"]
    shown, dropped = select_facts([], offered)
    assert [s["fact"] for s in shown] == ["f2"] and dropped == 0


@pytest.mark.parametrize("flags", [{"is_person": True, "is_subject": True},
                                   {"is_person": True, "is_subject": False},
                                   {"is_person": False, "is_subject": True}])
def test_a_person_or_protected_subject_table_never_sends_a_value(flags):
    """Codex f1 #3: values are suppressed when is_person OR is_subject; a plain table sends them."""
    from databridge_adapter_agents.explain import _model_payload

    facts = {"table": "t", "kind": "k", "association": False, **flags}
    ev = [{"id": "e1", "fact": "f2", "what": "primary_key", "column": "c", "value": "V-17",
           "masked": False}]
    offered = [{"id": "f2", "type": "primary_key", "text": "x", "basis": "capture",
                "evidence": ["e1"]}]
    from databridge_mcp_ingest.screen import load_screen

    cfg = load_screen()
    protected = _model_payload(facts, offered, ev, "T1", cfg)
    assert "V-17" not in json.dumps(protected)
    # f2 #4: a protected table is addressed by controlled identifiers only - no name, no text
    assert protected["table"] == "T1" and "name" not in protected
    assert all(set(f) == {"id", "type", "basis", "evidence"} for f in protected["facts"])
    assert all(set(e) == {"id", "fact", "what"} for e in protected["evidence"])
    plain = _model_payload({**facts, "is_person": False, "is_subject": False}, offered, ev,
                           "T1", cfg)
    assert plain["evidence"][0]["value"] == "V-17"                  # non-vacuous


def test_on_both_estates_protected_tables_send_no_value(estate):
    """End to end: with the vocabulary as it is, and with every kind declared a protected subject
    (none a person), no sampled value of such a table reaches the model."""
    from databridge_adapter_agents.explain import explain_mapping

    vocab, snaps = estate["vocab"], estate["snaps"]

    class AllSubjects:
        def __init__(self, v):
            self._v = v

        def __getattr__(self, name):
            return getattr(self._v, name)

        def is_subject(self, kind):
            return True

    for v in (vocab, AllSubjects(vocab)):
        h = Harness()
        ex = explain_mapping(SRC, _mapping(snaps, vocab), v, harness=h, agent_id="k",
                             captures=_captures(snaps))
        protected = {t["model_alias"]: t for t in ex["tables"]
                     if t["is_person"] or t["is_subject"]}
        sent = {t["table"]: t for p in h.sent for t in p["tables"]}
        for table in set(protected) & set(sent):
            assert all("value" not in e for e in sent[table]["evidence"])
            values = {e["value"] for e in protected[table]["evidence"] if e["value"]}
            assert not any(val in json.dumps(sent[table]) for val in values if len(val) > 3)
        if v is not vocab:
            assert set(protected) & set(sent)                       # non-vacuous


def test_a_payload_the_screen_does_not_allow_is_never_sent(estate, monkeypatch):
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_mcp_ingest import mapping as mp

    monkeypatch.setattr(mp, "screen_for_model",
                        lambda payload, **kw: (False, {"decision": "review", "fired": []}))
    h = Harness()
    vocab, snaps = estate["vocab"], estate["snaps"]
    ex = explain_mapping(SRC, _mapping(snaps, vocab), vocab, harness=h, agent_id="k",
                         captures=_captures(snaps))
    assert h.sent == []
    with_ev = [t for t in ex["tables"] if t["evidence"]]
    assert with_ev and all("screen decided 'review'" in t["not_sent_reason"] for t in with_ev)


def test_evidence_is_read_from_the_discoverys_capture_and_fails_closed(estate, tmp_path,
                                                                      monkeypatch):
    """Codex f1 #5: the explanation reads the capture digest the DISCOVERY recorded. A missing or
    altered capture yields no evidence and a capture_problem - never another capture."""
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_mcp_ingest import capture

    monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(tmp_path / "capture"))
    vocab, snaps = estate["vocab"], estate["snaps"]
    caps = {t: (s, capture.put_bytes(json.dumps(s, sort_keys=True, default=str).encode()))
            for t, s in snaps.items()}
    disc = _discovery(caps)
    victim = sorted(caps)[0]
    disc["tables"][victim]["sha256"] = "0" * 64                     # a capture that is not there
    ex = explain_mapping(SRC, _mapping(snaps, vocab), vocab, harness=None, discovery=disc)
    by = {t["table"]: t for t in ex["tables"]}
    assert ex["discovery"]["run_id"] == disc["run_id"]
    assert by[victim]["evidence"] == [] and by[victim]["capture_problem"]
    others = [t for name, t in by.items() if name != victim]
    assert others and all(t["capture_sha"] == disc["tables"][t["table"]]["sha256"]
                          for t in others)
    assert any(t["evidence"] for t in others)                        # non-vacuous


# --- setup helpers -------------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["db", "a" * 64, "x_y-z", "finance-core"])
def test_keeper_id_is_a_valid_deterministic_agent_id(sid):
    from databridge_adapter_agents.setup import draft_spec, keeper_id
    from databridge_workflow_spec import load_spec

    assert keeper_id(sid) == keeper_id(sid)
    spec = load_spec(draft_spec(sid, "workspace"))                 # the contract accepts it
    assert spec.id == keeper_id(sid) and spec.status == "draft" and spec.connections == [sid]


def test_setup_records_are_activity_not_syncs(tmp_path):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.setup import draft_spec, record_step, setup_records

    reg = AdapterAgentRegistry(root=tmp_path / "aa")
    mgr = AdapterAgentManager(service=object(), registry=reg, runs_root=tmp_path / "runs")
    spec = mgr.create(draft_spec("db", "workspace"), by="alice")
    rec = record_step(mgr.runs_root, spec, step="propose_plan", status="completed", by="alice",
                      reason="proposed")
    assert rec["kind"] == "adapter_setup" and rec["trigger"] == {"type": "setup", "by": "alice"}
    assert rec["produced_by"]["agent"] == f"adapter_agent:{spec.id}"
    started = record_step(mgr.runs_root, spec, step="build", status="started", by="alice",
                          reason="b")
    done = record_step(mgr.runs_root, spec, step="build", status="completed", by="alice",
                       reason="b")
    # in the order written, even within one second
    assert [r["run_id"] for r in setup_records(mgr.runs_root, spec.id)] == \
        [rec["run_id"], started["run_id"], done["run_id"]]
    assert mgr.run_records(spec.id) == []                           # health/sync list unchanged


def test_discovery_problems_accept_only_a_successful_discovery_under_this_plan():
    from databridge_adapter_agents.setup import discovery_problems
    from databridge_mcp_ingest.runner import DISCOVERED

    ok = {"run_id": "r", "status": DISCOVERED, "plan_digest": "p1", "tables": {}}
    assert discovery_problems(ok, plan_digest="p1") == []
    assert discovery_problems(None, plan_digest="p1")
    assert discovery_problems(ok, plan_digest="p2")                 # another plan
    assert discovery_problems(ok, plan_digest=None)
    for status in ("failed", "ok", "empty", "unverified", "incomplete", "invalid", "running"):
        assert discovery_problems({**ok, "status": status}, plan_digest="p1"), status


def test_explanation_problems_bind_the_explanation_to_discovery_and_proposal(estate):
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_adapter_agents.setup import explanation_problems

    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    caps = _captures(snaps)
    disc = _discovery(caps)
    ex = explain_mapping(SRC, m, vocab, harness=None, captures=caps, discovery=disc)
    assert explanation_problems(ex, m, disc) == []
    assert explanation_problems(None, m, disc)                      # f1 #4: none saved
    assert explanation_problems(ex, m, {**disc, "run_id": "newer"})  # f1 #5: another discovery
    t = sorted(m.tables)[0]
    other = {**disc, "tables": {**disc["tables"], t: {"sha256": "f" * 64}}}
    assert any(t in p for p in explanation_problems(ex, m, other))  # f1 #5: another capture
    tm = m.tables[t]
    edited = m.model_copy(update={"tables": {**m.tables, t: tm.model_copy(
        update={"edges": [], "node": {**(tm.node or {}), "label": "{zz}"}})}})
    assert any("changed since it was explained" in p
               for p in explanation_problems(ex, edited, disc))      # f1 #4: proposal changed


def test_the_handoff_counts_only_tables_a_person_approved(tmp_path, monkeypatch, estate):
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_adapter_agents.setup import approved_by_people, handoff_problems
    from databridge_agent_runtime import register_authenticator
    from databridge_mcp_ingest.mapping import approve_mapping, save_mapping

    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(tmp_path / "sources"))
    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    save_mapping(m)
    caps = _captures(snaps)
    disc = _discovery(caps)
    ex = explain_mapping(SRC, m, vocab, harness=None, captures=caps, discovery=disc)
    proposed = sorted(t for t, tm in m.tables.items() if tm.status == "proposed")
    assert proposed

    def gate(mapping, **over):
        kw = {"plan_status": "approved", "plan_stale": None, "plan_digest": "p1",
              "discovery": disc, "mapping": mapping, "explanation": ex, "vocab": vocab, **over}
        return handoff_problems(SRC, **kw)
    assert gate(m)
    with pytest.raises(Exception, match="machine actor"):
        approve_mapping(SRC, principal=register_authenticator("feat8").issue("adapter_agent:k"),
                        tables=proposed[:1], vocab=vocab)
    m2 = approve_mapping(SRC, principal=register_authenticator("feat8").issue("alice"),
                         tables=proposed[:1], vocab=vocab)
    ok, problems = approved_by_people(SRC, m2, vocab)
    assert ok == proposed[:1] and problems == []
    assert gate(m2) == []
    assert gate(m2, plan_status="proposed")
    assert gate(m2, plan_stale="the plan was approved for a different revision")   # f1 #6
    assert gate(m2, discovery=None)                                                 # f1 #8
    assert gate(m2, discovery={**disc, "status": "failed"})                         # f1 #8
    assert gate(m2, plan_digest="another plan")                                     # f1 #8
    assert gate(m2, explanation=None)                                               # f1 #4
    uncaptured = {**disc, "tables": {t: v for t, v in disc["tables"].items()
                                     if t != proposed[0]}}
    assert any("did not capture" in p for p in gate(m2, discovery=uncaptured))
    # a table marked approved by a machine or through a delegation is not a person's approval —
    # refused by the hand-off's OWN check, even where the engine's verification would pass
    from databridge_mcp_ingest import mapping as mp
    monkeypatch.setattr(mp, "approval_problem", lambda *a, **k: None)
    t = proposed[:1][0]
    for approval in ({"principal_id": "adapter_agent:k", "authenticated_by": "x"},
                     {"principal_id": "alice", "authenticated_by": "delegated:abc"}):
        forged = m2.model_copy(update={"tables": {**m2.tables, t: m2.tables[t].model_copy(
            update={"approval": {**m2.tables[t].approval, **approval}})}})
        ok, problems = approved_by_people(SRC, forged, vocab)
        assert t not in ok and problems
        assert gate(forged)


def test_a_row_the_screen_holds_is_never_sampled(estate, estate_snaps):
    """The finance estate plants a note for an automated reader (found by the screen, not written
    down here). Copied into the FIRST row of a table of each estate, that row is never cited."""
    import copy

    from databridge_adapter_agents.explain import explain_mapping
    from databridge_mcp_ingest.screen import load_screen, screen_text

    cfg = load_screen()
    note = next(v for s in estate_snaps["finance"].values() for r in s.get("rows") or []
                for v in r.values() if isinstance(v, str) and len(v) > 20
                and screen_text(v, cfg).decision != "allow")
    vocab, snaps = estate["vocab"], copy.deepcopy(estate["snaps"])
    m = _mapping(snaps, vocab)
    table = next(t for t in sorted(m.tables) if m.tables[t].node and snaps[t].get("rows"))
    first = snaps[table]["rows"][0]
    col = next(c for c, v in first.items() if isinstance(v, str))
    first[col] = note
    pk = list(snaps[table].get("primary_key") or [])
    held_id = _row_id(first, pk, 0)
    ex = explain_mapping(SRC, m, vocab, harness=None, captures=_captures(snaps))
    [t] = [x for x in ex["tables"] if x["table"] == table]
    assert t["evidence"]                                            # non-vacuous
    assert all(e["pointer"]["row_id"] != held_id for e in t["evidence"])
    assert note not in json.dumps(ex)


# --- the manager re-checks the plan's connection binding (Codex f1 #6) ----------------------------
class _PlanService:
    def __init__(self):
        self.status = "approved"

    def plan_status(self, source_id):
        return self.status

    def read_config(self, source_id, kind):
        return {"id": source_id, "scope": {"tables_include": ["s.t"]}} if kind == "connection"             else None


def _draft(mgr, cid="db"):
    from databridge_adapter_agents.setup import draft_spec

    d = draft_spec(cid, "workspace")
    d["plans"] = {cid: "approved"}
    return mgr.create(d, by="alice")


def test_the_manager_refuses_a_plan_its_stale_check_rejects(tmp_path):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.registry import RegistryError

    seen = []

    def stale(service, conn):
        seen.append(conn)
        return "the plan was approved for a different revision of this connection"
    mgr = AdapterAgentManager(service=_PlanService(), registry=AdapterAgentRegistry(
        root=tmp_path / "aa"), runs_root=tmp_path / "runs", plan_stale_check=stale)
    spec = _draft(mgr)
    assert any("different revision" in r for r in mgr.agent_gate(spec)) and seen == ["db"]
    with pytest.raises(RegistryError, match="different revision"):
        mgr.activate(spec.id, by="alice")
    # fail closed: a check that cannot run refuses
    mgr.plan_stale_check = lambda s, c: (_ for _ in ()).throw(OSError("unreadable"))
    assert any("could not be checked" in r for r in mgr.agent_gate(spec))
    # and with nothing stale, the gate is open (non-vacuous)
    mgr.plan_stale_check = lambda s, c: None
    assert mgr.agent_gate(spec) == []


def test_the_process_wide_stale_check_applies_to_every_manager(tmp_path, monkeypatch):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents import manager as manager_mod

    monkeypatch.setattr(manager_mod, "_PLAN_STALE_CHECK", None)
    mgr = AdapterAgentManager(service=_PlanService(), registry=AdapterAgentRegistry(
        root=tmp_path / "aa"), runs_root=tmp_path / "runs")
    spec = _draft(mgr)
    assert mgr.agent_gate(spec) == []
    manager_mod.set_plan_stale_check(lambda s, c: "stale for the connection as it is now")
    assert any("as it is now" in r for r in mgr.agent_gate(spec))


# =================================================================================================
# Codex R-027 AAX round 2
# =================================================================================================
def test_f2_4_every_string_sent_for_an_open_table_is_masked():
    """#4: an unprotected table's payload passes EVERY string field through the screen's masks,
    not only the values."""
    from databridge_adapter_agents.explain import _model_payload
    from databridge_mcp_ingest.screen import apply_masks, load_screen

    cfg = load_screen()
    secret = "jane.doe@example.com"
    masked, red = apply_masks(secret, cfg.redactions)
    assert red and masked != secret                     # the screen masks it (non-vacuous)
    facts = {"table": f"t_{secret}", "kind": "k", "association": False, "is_person": False,
             "is_subject": False}
    offered = [{"id": "f1", "type": "label", "text": f"named from {secret}", "basis": "capture",
                "evidence": ["e1"]}]
    ev = [{"id": "e1", "fact": "f1", "what": "label", "column": secret, "value": secret,
           "masked": False}]
    out = json.dumps(_model_payload(facts, offered, ev, "T1", cfg))
    assert secret not in out and "T1" in out


def test_f2_3_a_table_the_discovery_did_not_allow_is_never_sent_to_a_model(estate):
    """#3/#4: only a table the discovery's screen ALLOWED reaches the explanation model."""
    from databridge_adapter_agents.explain import explain_mapping

    vocab, snaps = estate["vocab"], estate["snaps"]
    caps = _captures(snaps)
    disc = _discovery(caps)
    held = sorted(caps)[0]
    disc["tables"][held]["decision"] = "review"
    for t in disc["tables"]:
        disc["tables"][t].setdefault("decision", "allow")
    h = Harness()
    ex = explain_mapping(SRC, _mapping(snaps, vocab), vocab, harness=h, captures=caps,
                         discovery=disc)
    by = {t["table"]: t for t in ex["tables"]}
    sent = {t["table"] for p in h.sent for t in p["tables"]}
    assert by[held]["model_alias"] not in sent and not by[held]["sent_to_model"]
    assert "'review'" in by[held]["not_sent_reason"]
    assert sent                                                      # others were (non-vacuous)


def test_f2_5_a_missing_or_corrupt_capture_fails_the_explanation(estate):
    from databridge_adapter_agents.explain import explain_mapping
    from databridge_adapter_agents.setup import explanation_problems

    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    caps = _captures(snaps)
    disc = _discovery(caps)
    ex = explain_mapping(SRC, m, vocab, harness=None, captures=caps, discovery=disc)
    assert explanation_problems(ex, m, disc) == []
    t = sorted(caps)[0]
    for broken in ({"capture_sha": None, "capture_problem": None},
                   {"capture_sha": None, "capture_problem": "the capture is missing"},
                   {"capture_problem": "altered"}):
        bad = {**ex, "tables": [{**x, **broken} if x["table"] == t else x
                                for x in ex["tables"]]}
        assert any(t in p for p in explanation_problems(bad, m, disc)), broken


def _setup_mgr(tmp_path, service=None, **kw):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.setup import draft_spec, mark_setup_agent

    mgr = AdapterAgentManager(service=service or _PlanService(), registry=AdapterAgentRegistry(
        root=tmp_path / "aa"), runs_root=tmp_path / "runs", **kw)
    d = draft_spec("db", "workspace")
    d["plans"] = {"db": "approved"}
    spec = mgr.create(d, by="alice")
    mark_setup_agent(mgr.registry.root, spec.id, "db", by="alice")
    return mgr, spec


def _issued(pid):
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("feat8-aa").issue(pid)


def test_f2_1_a_setup_agent_activates_only_under_a_persons_authorisation(tmp_path, monkeypatch):
    """#1: the manager refuses a setup agent's activation (any route) without a recorded hand-off
    authorisation, and verifies the authorisation against the setup as it is now."""
    from databridge_adapter_agents import setup as setup_mod
    from databridge_adapter_agents.registry import RegistryError

    mgr, spec = _setup_mgr(tmp_path)
    assert any("no person has authorised" in r for r in mgr.agent_gate(spec, activating=True))
    with pytest.raises(RegistryError, match="no person has authorised"):
        mgr.activate(spec.id, by=_issued("alice"))
    gate = {"problems": [], "pending": [], "plan_digest": "p", "discovery": {"run_id": "r"},
            "mapping": None, "explanation": None, "documents_decision": None}
    monkeypatch.setattr(setup_mod, "load_gate", lambda *a, **k: dict(gate))
    binding = setup_mod.gate_binding(plan_digest="p", discovery={"run_id": "r"}, mapping=None,
                                     explanation=None, documents=None, deferred=[])
    mgr.authorize_setup_handoff(spec.id, by=_issued("alice"), binding=binding)
    # the setup moved on since: refused
    gate["discovery"] = {"run_id": "newer"}
    with pytest.raises(RegistryError, match="changed since"):
        mgr.activate(spec.id, by=_issued("alice"))
    gate["discovery"] = {"run_id": "r"}
    gate["problems"] = ["the crawl plan is stale"]
    with pytest.raises(RegistryError, match="stale"):
        mgr.activate(spec.id, by=_issued("alice"))
    gate["problems"] = []
    assert mgr.activate(spec.id, by=_issued("alice")).status == "active"   # non-vacuous


def test_f2_1_an_active_setup_agent_syncs_only_with_a_seal_in_force(tmp_path, monkeypatch):
    from databridge_adapter_agents import setup as setup_mod

    mgr, spec = _setup_mgr(tmp_path)
    gate = {"problems": [], "pending": [], "plan_digest": "p", "discovery": {"run_id": "r"},
            "mapping": None, "explanation": None, "documents_decision": None}
    monkeypatch.setattr(setup_mod, "load_gate", lambda *a, **k: dict(gate))
    mgr.authorize_setup_handoff(spec.id, by=_issued("alice"), binding=setup_mod.gate_binding(
        plan_digest="p", discovery={"run_id": "r"}, mapping=None, explanation=None,
        documents=None, deferred=[]))
    spec = mgr.activate(spec.id, by=_issued("alice"))
    assert mgr.delegator(spec) == "alice" and mgr.agent_gate(spec) == []
    monkeypatch.setattr(mgr, "delegation_status", lambda s: (None, "revoked"))
    assert any("sealed delegation" in r for r in mgr.agent_gate(spec))
    summary = mgr.sync(spec.id)
    assert summary.status == "refused" and summary.connections == []


def test_f2_2_activation_is_person_only(tmp_path):
    """#2: a machine actor - by principal or by name - never activates or resumes; a setup
    agent is never activated by a typed name; a machine cannot authorise a hand-off."""
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.registry import RegistryError
    from databridge_adapter_agents.setup import draft_spec

    mgr, spec = _setup_mgr(tmp_path)
    for by in (_issued("adapter_agent:k"), "adapter_agent:k", "system", "alice"):
        with pytest.raises(RegistryError, match="machine actor|authenticated person"):
            mgr.activate(spec.id, by=by)
    with pytest.raises(RegistryError, match="machine actor"):
        mgr.authorize_setup_handoff(spec.id, by=_issued("workflow:x"), binding={})
    with pytest.raises(RegistryError, match="name, not an authenticated principal"):
        mgr.authorize_setup_handoff(spec.id, by="alice", binding={})
    # an agent NOT set up here keeps today's rules (a name activates; it delegates nothing) -
    # but never a machine
    plain = AdapterAgentManager(service=_PlanService(), registry=AdapterAgentRegistry(
        root=tmp_path / "aa2"), runs_root=tmp_path / "runs2")
    d = draft_spec("db", "workspace")
    d["plans"] = {"db": "approved"}
    s2 = plain.create(d, by="alice")
    with pytest.raises(RegistryError, match="machine actor"):
        plain.activate(s2.id, by="agent:planner")
    assert plain.activate(s2.id, by="alice").status == "active"


def test_f2_8_pending_decisions_name_links_holds_tables_and_identity(estate, tmp_path,
                                                                   monkeypatch):
    from databridge_adapter_agents.setup import pending_decisions
    from databridge_agent_runtime import register_authenticator
    from databridge_mcp_ingest.mapping import approve_mapping, save_mapping

    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(tmp_path / "ontology.json"))
    vocab, snaps = estate["vocab"], estate["snaps"]
    m = _mapping(snaps, vocab)
    caps = _captures(snaps)
    disc = _discovery(caps)
    t0, t1 = sorted(t for t, tm in m.tables.items() if tm.status == "proposed")[:2]
    disc["tables"][t1].update({"decision": "review", "item_id": "it-1"})
    disc["documents"] = {"items": [{"item_id": "d-1", "sha256": "s", "decision": "review",
                                    "uri": "docs/x.md"}]}
    tm0 = m.tables[t0]
    m = m.model_copy(update={"tables": {**m.tables, t0: tm0.model_copy(update={"links": [
        {"column": "c", "source": "other", "table": "o", "key": "k", "status": "proposed"}]})}})
    save_mapping(m)
    m = approve_mapping(SRC, principal=register_authenticator("feat8").issue("alice"),
                        tables=[t0], vocab=vocab)
    got = pending_decisions(SRC, mapping=m, discovery=disc, vocab=vocab, overrides=set())
    keys = {d["key"] for d in got}
    assert any(k.startswith(f"link:{t0}:c:") for k in keys)
    assert {"hold:it-1", "hold:d-1", f"table:{t1}"} <= keys
    assert f"table:{t0}" not in keys
    for kind in [(m.tables[t0].node or {}).get("kind")]:
        if vocab.identity_rule_for(kind) is not None:
            assert f"identity:{kind}" in keys
    # an owner override for THIS content lifts the hold
    class Lifted(set):
        def allows(self, item_id, digest):
            return item_id == "d-1" and digest == "s"
    keys2 = {d["key"] for d in pending_decisions(SRC, mapping=m, discovery=disc, vocab=vocab,
                                                 overrides=Lifted())}
    assert "hold:d-1" not in keys2 and "hold:it-1" in keys2


def test_f2_7_documents_need_a_persons_decision_on_exactly_this_discovery():
    from databridge_adapter_agents.setup import documents_digest, documents_problems

    disc = {"run_id": "r1", "documents": {"items": [
        {"item_id": "a", "sha256": "1", "decision": "allow"},
        {"item_id": "b", "sha256": "2", "decision": "review"}]}}
    dig = documents_digest(disc)
    assert dig and documents_digest({"run_id": "r", "documents": {"items": []}}) is None
    ok = {"decision": "build", "discovery_run_id": "r1", "documents_digest": dig,
          "principal_id": "alice"}
    assert documents_problems(ok, disc) == []
    assert documents_problems(None, disc)
    assert documents_problems({**ok, "discovery_run_id": "r0"}, disc)
    assert documents_problems({**ok, "documents_digest": "x"}, disc)
    assert documents_problems({**ok, "principal_id": "adapter_agent:k"}, disc)
