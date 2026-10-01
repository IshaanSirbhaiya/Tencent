"""AA — adapter agents adapt to DOCUMENT-source and TOOL changes at runtime.

The real stdio fixture server over a temp folder. A new file type dropped into the folder is added
to the declared scope (as a new connection version) and captured on the next sync; a new write
tool, and a changed signature on a tool the plan uses, are always asked. Tool-level changes are
injected by a service whose probe reports them (a real server cannot grow a tool mid-test); the
ingest itself stays real.
"""
from __future__ import annotations

import pytest

from aa_drift_kit import OWNER, drift_env

CONN = "folder"


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "brief.md").write_text("# Brief\n\nRoute planning for the depots.\n",
                                            encoding="utf-8")
    (root / "docs" / "notes.txt").write_text("Depot notes for the quarter.\n", encoding="utf-8")
    return root


def _setup(env, corpus, **kw):
    spec = env.setup(CONN, root=corpus, file_types=[".md", ".txt"], **kw)
    first = env.sync(spec.id)
    assert first.status == "completed", first.reason
    return spec


def _adapt(summary) -> dict:
    return summary.connections[0].adaptation


def _captured(env, summary) -> list[dict]:
    return env.jsonl(env.run_dir(summary, CONN) / "captured.jsonl")


def _scope_types(env) -> list[str]:
    from databridge_mcp_ingest.connections import load_connection

    return list(load_connection(CONN).scope.file_types)


def test_new_file_type_widening_scope_is_asked_then_captured_next_sync(env, corpus):
    """E2E F-24: a new type OUTSIDE the declared scope is asked (a person widens the scope), then
    captured on the next sync. It is never added on its own under auto_additive."""
    spec = _setup(env, corpus)
    types_before = _scope_types(env)
    (corpus / "docs" / "ledger.csv").write_text("depot,volume\nNorth,12\n", encoding="utf-8")
    s = env.sync(spec.id)
    excluded = env.jsonl(env.run_dir(s, CONN) / "excluded.jsonl")
    assert [x["rule"] for x in excluded if x["uri"].endswith("ledger.csv")] == ["file_type:.csv"]
    a = _adapt(s)
    assert "new_file_type" in a["drift_kinds"] and a.get("pending") and not a.get("applied")
    assert _scope_types(env) == types_before
    [p] = [x for x in env.manager.pending_adaptations(spec.id)
           if x["proposal"]["artifact"] == "connection"]
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert _scope_types(env) == types_before + [".csv"]
    vs = env.manager.adaptation.store.versions(CONN, "connection")
    assert len(vs) == 2 and vs[0]["version"] == 1 and vs[1]["supersedes"] == 1
    nxt = env.sync(spec.id)
    assert [c["uri"] for c in _captured(env, nxt) if c["uri"].endswith("ledger.csv")]
    assert not [x for x in env.jsonl(env.run_dir(nxt, CONN) / "excluded.jsonl")
                if x["uri"].endswith("ledger.csv")]


def test_a_file_type_with_no_extractor_is_noted_not_added(env, corpus):
    spec = _setup(env, corpus)
    types_before = _scope_types(env)
    (corpus / "docs" / "legacy.rtf").write_text("{\\rtf1 old}", encoding="utf-8")
    a = _adapt(env.sync(spec.id))
    assert "new_file_type" in a["drift_kinds"] and a.get("noted") and not a.get("applied")
    assert _scope_types(env) == types_before
    rec = env.manager.adaptations(spec.id)[-1]
    assert "no extractor" in rec["action"]


def test_policy_off_reports_a_new_file_type_only(env, corpus):
    spec = _setup(env, corpus, adapt={"policy": "off"})
    types_before = _scope_types(env)
    (corpus / "docs" / "ledger.csv").write_text("depot,volume\nNorth,12\n", encoding="utf-8")
    a = _adapt(env.sync(spec.id))
    assert a.get("reported") and not a.get("applied")
    assert _scope_types(env) == types_before
    assert env.manager.adaptation.store.versions(CONN, "connection") == []


def test_ask_all_files_the_scope_change_for_approval(env, corpus):
    spec = _setup(env, corpus, adapt={"policy": "ask_all"})
    (corpus / "docs" / "ledger.csv").write_text("depot,volume\nNorth,12\n", encoding="utf-8")
    s = env.sync(spec.id)
    assert s.connections[0].status == "completed"            # nothing to hold: it keeps syncing
    [p] = env.manager.pending_adaptations(spec.id)
    assert p["proposal"]["artifact"] == "connection" and p["hold"]["level"] == "none"
    assert ".csv" not in _scope_types(env)
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert ".csv" in _scope_types(env)
    nxt = env.sync(spec.id)
    assert [c for c in _captured(env, nxt) if c["uri"].endswith("ledger.csv")]


# --- tools (injected through the probe) -------------------------------------------------------------
def _scripted(env):
    from databridge_adapter_agents import McpIngestService

    class Scripted(McpIngestService):
        mutate = None

        def probe_shape(self, source_id):
            shape = super().probe_shape(source_id)
            return self.mutate(shape) if self.mutate else shape

    svc = Scripted()
    env.use_service(svc)
    return svc


def _with_tool(shape, name, **kw):
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.drift import tool_shape

    tools = dict(shape.tools)
    tools[name] = tool_shape(ToolInfo(name, **kw))
    return shape.model_copy(update={"tools": tools})


def test_a_new_write_tool_is_always_asked_even_under_auto_additive(env, corpus):
    svc = _scripted(env)
    spec = _setup(env, corpus, adapt={"policy": "auto_additive"})
    svc.mutate = lambda s: _with_tool(
        _with_tool(s, "purge_index", input_schema={"properties": {}}),
        "describe_entry", input_schema={"properties": {"path": {"type": "string"}}},
        read_only=True)
    s = env.sync(spec.id)
    assert s.connections[0].status == "completed"            # the crawl never calls it
    recs = {r["unit"]: r for r in env.manager.adaptations(spec.id)}
    write = recs["tool:purge_index"]
    assert write["status"] == "pending" and write["drifts"][0]["kind"] == "new_write_tool"
    assert write["hold"]["level"] == "none"
    assert recs["tool:describe_entry"]["status"] == "noted"  # a new read-only tool: noted
    env.manager.approve_adaptation(spec.id, write["adaptation_id"], by=OWNER)
    assert _adapt(env.sync(spec.id))["drifts"] == 0


def test_a_changed_tool_the_plan_uses_holds_the_connection_and_is_asked(env, corpus):
    from databridge_mcp_ingest.plans import load_plan

    svc = _scripted(env)
    spec = _setup(env, corpus)
    used = sorted({t for s in load_plan(CONN).steps for t in s.tools()})[0]

    def changed(shape):
        tools = dict(shape.tools)
        t = tools[used]
        tools[used] = t.model_copy(update={"params": {**t.params, "cursor": "string"},
                                           "required": sorted({*t.required, "cursor"}),
                                           "signature": "changed"})
        return shape.model_copy(update={"tools": tools})

    svc.mutate = changed
    s = env.sync(spec.id)
    c = s.connections[0]
    [p] = env.manager.pending_adaptations(spec.id)
    assert p["drifts"][0]["kind"] == "tool_signature_changed"
    assert p["proposal"]["artifact"] == "plan"
    h = env.manager.health(spec.id)
    if env.holds_file():
        # the runner holds exactly the steps that call the changed tool (P's hook)
        from databridge_mcp_ingest.records import load_holds

        assert p["hold"]["level"] == "steps" and p["hold"]["via"] == "holds_file"
        assert dict(load_holds(CONN).steps) == {st: p["adaptation_id"]
                                                for st in p["hold"]["steps"]}
        assert c.status != "completed", "a run with a held step is not a complete run"
    else:
        assert c.status == "skipped" and "held" in c.reason
        assert c.exclusions_by_rule["excluded"]["DRIFT_PENDING_APPROVAL"] == 1
        assert p["hold"]["level"] == "connection" and p["hold"]["requested"] == "steps"
        assert not h["connections"][0]["runnable"] and \
            h["adaptation"]["held_connections"] == {CONN: [p["adaptation_id"]]}
    plans_before = len(env.manager.adaptation.store.versions(CONN, "plan"))
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert load_plan(CONN).approved_by.startswith(OWNER.principal_id)
    assert len(env.manager.adaptation.store.versions(CONN, "plan")) == plans_before + 2
    nxt = env.sync(spec.id)
    assert nxt.connections[0].status == "completed" and _adapt(nxt)["drifts"] == 0


def test_a_replanned_step_approved_with_a_principal_decides_the_hold(env, corpus, monkeypatch):
    """W1: `approve_plan(principal=...)` of a plan that re-plans every held step decides the
    adaptation (authenticated), releases the hold and advances the baseline; a typed name only
    never does."""
    from databridge_adapter_agents import install_plan_approval_hook
    from databridge_mcp_ingest import plans

    monkeypatch.setattr(plans, "_PLAN_APPROVAL_LISTENERS", [])
    svc = _scripted(env)
    spec = _setup(env, corpus)
    used = sorted({t for s in plans.load_plan(CONN).steps for t in s.tools()})[0]

    def changed(shape):
        tools = dict(shape.tools)
        t = tools[used]
        tools[used] = t.model_copy(update={"params": {**t.params, "cursor": "string"},
                                           "required": sorted({*t.required, "cursor"}),
                                           "signature": "changed"})
        return shape.model_copy(update={"tools": tools})

    svc.mutate = changed
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    assert install_plan_approval_hook(env.manager)
    current = plans.load_plan(CONN)
    replanned = current.model_copy(update={
        "status": "proposed",
        "steps": [s.model_copy(update={"id": s.id + "-v2"}) for s in current.steps]})
    plans.save_plan(replanned)
    # FXI3: a typed name is REFUSED by mcp_ingest (ApprovalRefused) and decides nothing
    with pytest.raises(plans.ApprovalRefused):
        plans.approve_plan(CONN, approved_by="owner")
    assert [x["adaptation_id"] for x in env.manager.pending_adaptations(spec.id)] ==         [p["adaptation_id"]]
    plans.save_plan(replanned)
    with pytest.raises(plans.ApprovalRefused):                      # a dict is a claim, too
        plans.approve_plan(CONN, principal={"principal_id": "x"})
    assert [x["adaptation_id"] for x in env.manager.pending_adaptations(spec.id)] ==         [p["adaptation_id"]]
    plans.save_plan(replanned)
    plans.approve_plan(CONN, principal=OWNER)
    assert env.manager.pending_adaptations(spec.id) == []
    [rec] = [r for r in env.manager.adaptations(spec.id)
             if r["adaptation_id"] == p["adaptation_id"]]
    assert rec["status"] == "approved"
    assert rec["decided_by"]["principal_id"] == OWNER.principal_id
    nxt = env.sync(spec.id)
    assert nxt.connections[0].status == "completed" and _adapt(nxt)["drifts"] == 0


def test_list_changed_rechecks_that_connection(env, corpus):
    spec = _setup(env, corpus)
    [out] = env.manager.on_list_changed(CONN)
    assert out["trigger"]["type"] == "list_changed" and out["ran"] == [CONN]
    notes = env.manager.adaptation.store.notifications(spec.id)
    assert [n["action"] for n in notes] == ["sync"]


def test_list_changed_is_ignored_when_the_spec_does_not_watch_it(env, corpus):
    spec = _setup(env, corpus, adapt={"watch": {"on_sync": True, "on_list_changed": False}})
    [out] = env.manager.on_list_changed(CONN)
    assert out["status"] == "ignored"
    assert [n["action"] for n in env.manager.adaptation.store.notifications(spec.id)] == \
        ["ignored"]
