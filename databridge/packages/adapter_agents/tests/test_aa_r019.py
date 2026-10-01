"""R-019 — Codex round 2 of the adapter-agents track, plus the E2E findings routed here.

Unit tests first (no server); then a few over the real stdio fixture server (`aa_drift_kit`).
Every expectation is derived from what the code or the source holds, never typed in.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from databridge_adapter_agents.adapt import (MANDATORY, AdaptAuditError, AdaptationRefused,
                                             AdaptStore, Policy, principal_of)
from databridge_mcp_ingest.drift import DRIFT_KINDS, Drift

from aa_drift_kit import OWNER, Principal, agent_spec, db_exec, drift_env, write_db


def _spec(adapt=None):
    from databridge_workflow_spec import AdapterAgentSpec

    return AdapterAgentSpec.model_validate(agent_spec("c", adapt=adapt))


def _d(kind, **kw):
    return Drift(kind=kind, unit="table:t", detail=kind, **kw)


# --- #1: the mandatory kinds are asked under EVERY policy, `off` included ------------------------
def test_mandatory_drift_is_asked_even_when_the_policy_is_off():
    from databridge_workflow_spec import ALWAYS_ASKED

    assert MANDATORY == ALWAYS_ASKED                     # the engine's floor IS the spec's
    off = Policy.of(_spec({"policy": "off"}))
    assert off.asks(_d("new_write_tool"))[0]
    assert off.asks(_d("added_column", subject_touching=True, subject_reason="x"))[0]
    # ... and `off` still only reports everything else (non-vacuous: there ARE other kinds)
    others = [k for k in DRIFT_KINDS if k not in MANDATORY]
    assert others and not any(off.asks(_d(k))[0] for k in others)


# --- #2: only a principal the authentication layer minted can decide -----------------------------
def test_a_dict_or_a_look_alike_object_is_not_a_principal():
    @dataclasses.dataclass(frozen=True)
    class LookAlike:                                     # carries the same fields, not registered
        principal_id: str
        authenticated_by: str = "test_boundary"

    for forged in ({"principal_id": "alice", "authenticated_by": "sso"}, LookAlike("alice")):
        with pytest.raises(AdaptationRefused, match="not a principal the authentication"):
            principal_of(forged, "approve")
    # a SUBCLASS of a trusted type is not the trusted type
    Sub = type("Sub", (Principal,), {})
    with pytest.raises(AdaptationRefused):
        principal_of(Sub("alice"), "approve")
    assert principal_of(OWNER, "approve")["principal_id"] == OWNER.principal_id


def test_a_trusted_principal_with_a_blank_mechanism_is_refused():
    with pytest.raises(AdaptationRefused, match="nothing authenticated"):
        principal_of(Principal("alice", authenticated_by="  "), "approve")


# --- #5: versions are append-only under crashes; a damaged index blocks ---------------------------
def test_a_version_file_left_unindexed_is_never_overwritten(tmp_path):
    store = AdaptStore(lambda: tmp_path)
    first = store.add_version("c", "mapping", {"v": 1}, reason="r", by="b", adaptation_id=None)
    # a writer that stopped between writing the file and indexing it
    orphan = Path(first["path"]).with_name("v0002.yaml")
    orphan.write_text("v: crashed\n", encoding="utf-8")
    nxt = store.add_version("c", "mapping", {"v": 3}, reason="r", by="b", adaptation_id=None)
    assert orphan.read_text(encoding="utf-8") == "v: crashed\n"          # never replaced
    idx = store.versions("c", "mapping")
    assert [r["version"] for r in idx] == [1, 2, 3] and idx[1]["recovered"] is True
    assert nxt["version"] == 3 and nxt["supersedes"] == 2


def test_a_damaged_version_index_is_a_blocking_audit_error(tmp_path):
    store = AdaptStore(lambda: tmp_path)
    store.add_version("c", "plan", {"v": 1}, reason="r", by="b", adaptation_id=None)
    with open(store.versions_index, "ab") as fh:
        fh.write(b"{not json\n")
    with pytest.raises(AdaptAuditError):
        store.add_version("c", "plan", {"v": 2}, reason="r", by="b", adaptation_id=None)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "adaptations.jsonl").write_text('{"adaptation_id": "x"}\n{oops\n',
                                                      encoding="utf-8")
    with pytest.raises(AdaptAuditError):
        store.records("a")


# --- #10 + E2E F-14: exclusions come from the run's ledgers, screen decisions included ------------
def _ledger(path: Path, recs: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def test_ledgers_are_authoritative_and_screen_decisions_are_counted_and_listed(tmp_path):
    from databridge_adapter_agents.ingest_service import SCREEN_LEDGERS, normalize_report

    run = tmp_path / "20261001T000000Z-r"
    run.mkdir()
    at = "2026-10-01T00:00:00Z"
    excluded = [{"item_id": "i1", "uri": "u1", "rel": "u1", "rule": "file_type:.bin",
                 "reverse": "add the type", "at": at}]
    drift = [{"item_id": "t1", "uri": "public.t", "rel": "public.t",
              "rule": "DRIFT_PENDING_APPROVAL", "reverse": "approve", "at": at}]
    gov = [{"item_id": f"g{n}", "uri": f"d{n}", "sha256": "0" * 64, "rules_source": "screen.yaml",
            "at": at, "decision": dec, "fired": [{"rule": r} for r in rules],
            "rationale": f"why {n}", "reverse": f"reverse {n}", "score": None}
           for n, (dec, rules) in enumerate([("block", ["private_key_block"]),
                                              ("block", ["pricing_heading", "currency_table"]),
                                              ("review", ["contract_value_wording"]),
                                              ("allow", [])])]
    _ledger(run / "excluded.jsonl", excluded)
    _ledger(run / "excluded.drift.jsonl", drift)             # written AFTER report.json
    _ledger(run / "governance.jsonl", gov)
    report = {"run_id": run.name, "status": "ok",
              "exclusions_by_rule": {"excluded": {"file_type:.bin": 1}}}
    r = normalize_report(report, source_id="s", run_dir=run)
    assert r.exclusions_by_rule["excluded"] == {"file_type:.bin": 1, "DRIFT_PENDING_APPROVAL": 1}
    kept_out = [g for g in gov if g["decision"] in SCREEN_LEDGERS]
    assert kept_out                                                     # non-vacuous
    for dec, ledger in SCREEN_LEDGERS.items():
        want = {"+".join(sorted(x["rule"] for x in g["fired"])): 1 for g in gov
                if g["decision"] == dec}
        assert r.exclusions_by_rule[ledger] == want
    assert [(x["item_id"], x["rule"], x["reverse"]) for x in r.exclusion_reasons] == \
        [(g["item_id"], "+".join(sorted(f["rule"] for f in g["fired"])), g["reverse"])
         for g in kept_out]
    # a report that disagrees with the ledgers is said, and the ledger count is used
    bad = dict(report, exclusions_by_rule={"excluded": {"file_type:.bin": 7}})
    r2 = normalize_report(bad, source_id="s", run_dir=run)
    assert r2.exclusions_by_rule["excluded"]["file_type:.bin"] == 1
    assert any("disagree" not in p["error"] and "ledgers are used" in p["error"]
               for p in r2.artefact_problems)


def test_health_lists_the_screen_exclusions(env, spec_factory):
    from databridge_adapter_agents.ingest_service import SCREEN_LEDGERS

    env.service.configure("files-a", items={"a.md": "alpha"})
    spec = env.manager.create(spec_factory(connections=("files-a",)))
    s = env.manager.sync(spec.id)
    run = env.tmp / "ingest" / "files-a" / s.connections[0].ingest_run_id
    _ledger(run / "governance.jsonl", [{
        "item_id": "g0", "uri": "d0", "sha256": "0" * 64, "rules_source": "screen.yaml",
        "at": "2026-10-01T00:00:00Z", "decision": "block", "fired": [{"rule": "private_key_block"}],
        "rationale": "decisive rule matched", "reverse": "remove the key", "score": None}])
    [c] = env.manager.health(spec.id)["connections"]
    assert c["exclusions_by_rule"][SCREEN_LEDGERS["block"]] == {"private_key_block": 1}
    assert [(x["rule"], x["reverse"]) for x in c["exclusion_reasons"]] == \
        [("private_key_block", "remove the key")]


# --- E2E F-11: extract.passages reaches run_ingest -----------------------------------------------
def test_extract_passages_reaches_run_ingest(env, spec_factory):
    seen = []
    base = env.service.run_ingest

    def run_ingest(source_id, *, store, harness, progress_cb=None, passages=None):
        seen.append(passages)
        return base(source_id, store=store, harness=harness, progress_cb=progress_cb)

    env.service.run_ingest = run_ingest
    env.service.configure("files-a", items={"a.md": "alpha"})
    for want in (True, False):
        sid = f"agent_{str(want).lower()}"
        spec = env.manager.create(spec_factory(agent_id=sid, connections=("files-a",),
                                               extract={"pack": "workspace",
                                                        "passages": want}))
        env.manager.sync(spec.id)
        assert seen[-1] is want


def test_the_mcp_service_passes_passages_to_run_ingest(monkeypatch):
    from databridge_adapter_agents import McpIngestService

    got = {}

    def fake_run(source_id, store=None, harness=None, progress_cb=None, *, passages=None):
        got.update(source_id=source_id, passages=passages)
        return {}

    svc = McpIngestService()
    monkeypatch.setattr(svc, "_run_fn", lambda: fake_run)
    svc.run_ingest("s", store=None, harness=None, passages=True)
    assert got == {"source_id": "s", "passages": True}
    with pytest.raises(TypeError):
        svc.run_ingest("s", store=None, harness=None, bogus=1)


# --- UI-builder deferred #1: a draft is activated with its checks, never resumed, never synced ----
def test_a_draft_is_never_resumed_and_never_synced(env, spec_factory):
    from databridge_adapter_agents import RegistryError

    env.service.configure("files-a", items={"a.md": "alpha"})
    spec = env.manager.create(spec_factory(connections=("files-a",), status="draft"))
    with pytest.raises(RegistryError, match="draft"):
        env.manager.resume(spec.id)
    with pytest.raises(RegistryError, match="activate"):
        env.manager.update(spec.id, {"status": "active"})
    env.manager.pause(spec.id)                            # draft -> paused -> resume: still no
    with pytest.raises(RegistryError, match="before it was paused"):
        env.manager.resume(spec.id)
    env.registry.update(spec.id, {"status": "draft"})
    for trigger in ({"type": "manual"}, {"type": "schedule"}):
        s = env.manager.sync(spec.id, trigger=trigger)
        assert s.status == "refused" and "draft" in s.reason
    assert env.service.calls == []
    # activation checks the plans; an unapproved plan refuses it
    env.service.configure("files-a", plan="proposed")
    with pytest.raises(RegistryError, match="cannot be activated"):
        env.manager.activate(spec.id)
    env.service.configure("files-a", plan="approved")
    assert env.manager.activate(spec.id).status == "active"
    assert env.manager.sync(spec.id).status == "completed"
    env.manager.pause(spec.id)
    assert env.manager.resume(spec.id).status == "active"      # a paused ACTIVE agent resumes


def test_a_paused_agent_whose_plan_went_back_to_proposed_does_not_resume(env, spec_factory):
    """Codex round 3 (builder UI, Critical): resume and sync are gated on EVERY referenced crawl
    plan being approved, enforced here — a UI gate is bypassable."""
    from databridge_adapter_agents import RegistryError

    env.service.configure("files-a", items={"a.md": "alpha"})
    spec = env.manager.create(spec_factory(connections=("files-a",)))
    env.manager.pause(spec.id)
    env.service.configure("files-a", plan="proposed")
    with pytest.raises(RegistryError, match="cannot be resumed"):
        env.manager.resume(spec.id)
    assert env.registry.get(spec.id).status == "paused"
    # an ACTIVE agent whose plan is unapproved does not sync, manual or scheduled
    env.service.configure("files-a", plan="approved")
    env.manager.resume(spec.id)
    env.service.configure("files-a", plan="proposed")
    for trigger in ({"type": "manual"}, {"type": "schedule"}):
        s = env.manager.sync(spec.id, trigger=trigger)
        assert s.status == "refused" and "'proposed'" in s.reason
    assert env.service.calls == []


# ================================================================================================
# over the real stdio fixture server
# ================================================================================================
CONN = "crm"
ORGS = "public.organisations"


@pytest.fixture
def denv(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "crm.sqlite"
    write_db(path)
    return path


def _scripted(env, **overrides):
    """The real McpIngestService with some methods replaced (a probe that fails, a runner that
    does not honour holds, a report without its run directory...)."""
    from databridge_adapter_agents import McpIngestService

    Scripted = type("Scripted", (McpIngestService,), dict(overrides))
    svc = Scripted()
    env.use_service(svc)
    return svc


def _runs(env) -> list[str]:
    d = env.tmp / "ingest" / CONN
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def test_screening_that_cannot_complete_fails_closed(denv, db):
    """#4 / #8: a probe that fails, a probe that saw only part of the shape, or a vocabulary that
    cannot be loaded while there is drift — the connection is NOT ingested, and it is said."""
    from databridge_mcp_ingest import drift as mdrift

    state = {"mode": None}

    def probe_shape(self, source_id):
        if state["mode"] == "raise":
            raise ConnectionError("server went away")
        shape = mdrift.probe_source(source_id, root=self.sources_root)
        if state["mode"] == "partial":
            shape = shape.model_copy(update={"tables": None,
                                             "problems": ["tables (s): TimeoutError: slow"]})
        return shape

    from databridge_adapter_agents import McpIngestService

    def vocabulary(self):
        if state["mode"] == "vocab":
            raise RuntimeError("pack failed to load")
        return McpIngestService.vocabulary(self)

    _scripted(denv, probe_shape=probe_shape, vocabulary=vocabulary)
    spec = denv.setup(CONN, db=db)
    assert denv.sync(spec.id).status == "completed"
    db_exec(db, "ALTER TABLE organisations ADD COLUMN region TEXT;")
    for mode in ("raise", "partial", "vocab"):
        state["mode"] = mode
        before = _runs(denv)
        s = denv.sync(spec.id)
        c = s.connections[0]
        assert c.status == "skipped" and "could not be screened" in c.reason, (mode, c.reason)
        assert c.adaptation["screening_failed"], mode
        assert _runs(denv) == before, f"{mode}: an unscreened source was ingested"
    fails = denv.manager.adaptation.store.screening_failures(spec.id)
    assert len(fails) == 3 and all(f["connection"] == CONN for f in fails)
    h = denv.manager.health(spec.id)
    assert h["adaptation"]["screening_failed"].get(CONN)
    assert any("not ingested at the last check" in i for i in h["issues"])
    state["mode"] = None
    assert denv.sync(spec.id).connections[0].status == "completed"


def test_a_write_tool_the_approved_plan_never_listed_is_asked_at_the_first_sync(denv, db):
    """#7: the first observation is not a free pass for a write tool that appeared after the
    crawl plan was approved."""
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.drift import probe_source, tool_shape
    from databridge_mcp_ingest.plans import load_plan

    def probe_shape(self, source_id):
        shape = probe_source(source_id, root=self.sources_root)
        tools = dict(shape.tools)
        for name in ("purge_everything", "delete_listed"):
            tools[name] = tool_shape(ToolInfo(name, input_schema={"properties": {}}))
        return shape.model_copy(update={"tools": tools})

    from databridge_mcp_ingest.plans import save_plan

    _scripted(denv, probe_shape=probe_shape)
    spec = denv.setup(CONN, db=db)
    # the approved plan LISTED one of the two write tools (a person saw it at approval)
    plan = load_plan(CONN)
    save_plan(plan.model_copy(update={"tools_seen": sorted([*plan.tools_seen,
                                                            "delete_listed"])}))
    assert "purge_everything" not in load_plan(CONN).tools_seen
    assert all(t.writes for n, t in probe_shape(denv.service, CONN).tools.items()
               if n in ("purge_everything", "delete_listed"))
    s = denv.sync(spec.id)
    assert s.connections[0].adaptation["baseline_established"] is True
    pending = denv.manager.pending_adaptations(spec.id)
    # only the write tool the plan never listed is asked; the listed one is not re-asked
    assert [(p["unit"], p["drifts"][0]["kind"]) for p in pending] == \
        [("tool:purge_everything", "new_write_tool")]


def test_policy_off_still_asks_about_a_subject_column(denv, db):
    """#1 end to end: under `off` a subject-touching column is pending (held), never applied."""
    from databridge_mcp_ingest.drift import subject_identifiers

    ident = sorted(subject_identifiers(denv.vocab))[0]
    spec = denv.setup(CONN, db=db, adapt={"policy": "off"})
    assert denv.sync(spec.id).status == "completed"
    db_exec(db, f"ALTER TABLE projects ADD COLUMN {ident} TEXT;")
    a = denv.sync(spec.id).connections[0].adaptation
    assert a.get("pending") and not a.get("applied")
    [p] = denv.manager.pending_adaptations(spec.id)
    assert p["drifts"][0]["subject_touching"]
    assert p["hold"]["level"] != "none"
    assert denv.manager.adaptation.store.versions(CONN, "mapping") == [] or \
        p["hold"]["via"] == "mapping_status"


def test_a_run_whose_governance_records_were_not_written_is_not_completed(denv, db, monkeypatch):
    """#9 (second half): writing the run's adaptation / drift-exclusion records failed — the run is
    not a success (a hold would have no exclusion record), and the reason says so."""
    from databridge_adapter_agents.adapt import AdaptationEngine

    spec = denv.setup(CONN, db=db)
    assert denv.sync(spec.id).status == "completed"

    def broken(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(AdaptationEngine, "_write_run_records", broken)
    c = denv.sync(spec.id).connections[0]
    assert c.status == "unverified", c.reason
    assert "governance records were not written" in c.reason and "disk full" in c.reason


def test_approve_mapping_by_name_does_not_approve_an_adaptation(denv, db):
    """#3 (the mapping-status fallback, a runner that does not honour holds) and #9 (a report
    without its run directory): a mapping file edited to `approved` is not an authenticated
    decision — the hold is re-installed; and the drift ledger is still written, into the run
    directory the history holds."""
    import databridge_mcp_ingest as mi
    from databridge_adapter_agents import McpIngestService

    def run_ingest(self, source_id, **kw):
        out = McpIngestService.run_ingest(self, source_id, **kw)
        d = out.as_dict() if hasattr(out, "as_dict") else dict(out)
        d.pop("run_dir", None)                    # a report that does not name its directory
        return d

    _scripted(denv, holds_honoured=lambda self: False, run_ingest=run_ingest)
    spec = denv.setup(CONN, db=db)
    assert denv.sync(spec.id).status == "completed"
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    s = denv.sync(spec.id)
    [p] = denv.manager.pending_adaptations(spec.id)
    assert p["hold"]["via"] == "mapping_status"
    ledger = denv.jsonl(denv.run_dir(s, CONN) / "excluded.drift.jsonl")
    assert [x["uri"] for x in ledger] == [ORGS]                            # #9
    assert s.connections[0].status == "completed"
    # someone approves the held table through mcp_ingest with a typed name: since R-022 f1 #1
    # mcp_ingest refuses that itself, so the remaining bypass is a hand-edited `status`
    with pytest.raises(ValueError, match="not an authenticated principal"):
        mi.approve_mapping(CONN, approved_by="alice")
    from databridge_mcp_ingest.mapping import load_mapping, save_mapping
    m = load_mapping(CONN)
    save_mapping(m.model_copy(update={"tables": {**m.tables, ORGS: m.tables[ORGS].model_copy(
        update={"status": "approved"})}}))
    assert denv.mapping(CONN)["tables"][ORGS]["status"] == "approved"
    denv.sync(spec.id)
    [still] = denv.manager.pending_adaptations(spec.id)
    assert still["adaptation_id"] == p["adaptation_id"]
    assert denv.mapping(CONN)["tables"][ORGS]["status"] == "proposed"      # held again
    assert any("not an authenticated decision" in (h.get("note") or "")
               for h in still["history"])
