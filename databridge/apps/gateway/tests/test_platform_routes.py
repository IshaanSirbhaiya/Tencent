"""Knowledge-platform gateway routes (BUILD_PLAN §6 U): `/sources`, `/adapter-agents`,
`/automation`, `/platform/catalogue` — and the trigger engine hosted in the app lifespan.

Every test runs on private directories in `tmp_path` (every `WALNUT_*` location is pointed there)
and on fakes: a fake ingest facade (no MCP server is spawned), a fake ingest service for the
adapter-agent manager, an in-memory graph store. No model and no network are reached.

What is proven:

* every route these modules mount answers 401 without a credential — the list is DERIVED from
  the app's routes, and is checked to be non-empty and to include every write;
* approvals are attributed to the signed-in principal even when the body names someone else;
* a background ingest run returns its ``run_id`` at once, streams progress onto the job, and
  calls ``app.state.reload_graph()`` when it ends — on success AND on failure;
* an adapter agent's sync files an ontology proposal, and forwarding it reaches `/ontology` with
  ``origin="adapter_agent"`` — pending, not applied, even with auto-approve ON;
* a manual firing through the engine is routed by spec kind and recorded as the principal's;
* the engine's loop starts with the app lifespan and stops with it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import AUTH_HEADERS, auth_headers
from databridge_gateway.public_errors import STANDARD_FOR  # noqa: E402  (R-028 #11)

from databridge_gateway import adapter_agents_routes, automation_routes, sources_routes
from databridge_gateway import agents_routes
from databridge_gateway.platform_runtime import (IngestFacade, PlatformRuntime, build_platform,
                                                 make_engine_runner)

PLATFORM_MODULES = {m.__name__ for m in (sources_routes, adapter_agents_routes, agents_routes,
                                         automation_routes)}

PACK_YAML = """\
pack: workspace
title: Workspace (test)
version: "0.1"

node_kinds:
  - name: person
    binds_to: entity
    description: A natural person.
  - name: organisation
    binds_to: organisation
    description: A company.
  - name: document
    binds_to: document
    description: A captured file.

edge_kinds:
  - name: mentions
    binds_to: references
    src: [document]
    dst: [person, organisation]
    description: A document names something.

subject_kinds:
  - kind: person
    identifiers: [email]
    never_fuzzy_merge: true
    attestation: verified_channel
    grant_type: PersonReadGrant
"""


# ------------------------------------------------------------------------------------------
# environment: every location private to the test
# ------------------------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    from databridge_graph.packs import clear_caches

    dirs = {"WALNUT_SOURCES_DIR": "sources", "WALNUT_INGEST_DIR": "ingest",
            "WALNUT_CAPTURE_DIR": "capture", "WALNUT_AGENTS_DIR": "agents",
            "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents", "WALNUT_RUNS_DIR": "runs",
            "WALNUT_AUTOMATION_DIR": "automation", "WALNUT_EVENTS_OFFSETS_DIR": "offsets",
            "WALNUT_PASSAGES_DIR": "passages"}
    out = {}
    for name, sub in dirs.items():
        monkeypatch.setenv(name, str(tmp_path / sub))
        out[sub] = tmp_path / sub
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(tmp_path / "events" / "events.jsonl"))
    root = tmp_path / "packs"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "ontology.yaml").write_text(PACK_YAML, encoding="utf-8")
    (root / "deployment.yaml").write_text("enabled_packs:\n  - workspace\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(tmp_path / "state" / "ontology.json"))
    monkeypatch.delenv("WALNUT_ONTOLOGY_AUDIT_PATH", raising=False)
    monkeypatch.setenv("WALNUT_AUTOMATION_ENGINE", "0")
    # R-019: command sources are OFF over HTTP unless the operator allow-lists the executable.
    # These tests register `npx` sources, so the operator's list names it; the policy tests
    # below change it themselves.
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES", "npx")
    # `npx` is a package runner: these tests register npx sources, so they run as an operator
    # who has accepted that risk; the round-2 policy tests unset it themselves.
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK", "1")
    monkeypatch.delenv("WALNUT_SOURCE_SECRET_HOSTS", raising=False)
    monkeypatch.delenv("WALNUT_SOURCE_ALLOWED_HOSTS", raising=False)
    # Hermetic resolution: PATH lookup and DNS are seams, never the machine's or the network's.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runners = {"npx", "uvx"}

    def fake_which(cmd):
        base = cmd.lower()
        for suffix in (".cmd", ".exe", ".bat"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return str(fake_bin / base) if base in runners | set(out.get("extra_exes", ())) else None

    monkeypatch.setattr(sources_routes, "_which", fake_which)
    out["hosts"] = {}

    def fake_resolve(host, port):
        return list(out["hosts"].get(host, ["93.184.216.34"]))

    monkeypatch.setattr(sources_routes, "_resolve_host", fake_resolve)
    # INT5: an HTTP registration is now `origin="remote"` in the package too (FXJ3), so the
    # package's registration policy resolves the same command and host. Its seams get the SAME
    # hermetic fakes — one machine, one network, as the gateway's own check sees them.
    from databridge_mcp_ingest import connections as _mi_connections

    monkeypatch.setattr(_mi_connections, "_which", fake_which)
    monkeypatch.setattr(_mi_connections, "_resolve_host", fake_resolve)
    out["packs"] = root
    out["tmp"] = tmp_path
    clear_caches()
    yield out
    clear_caches()


# ------------------------------------------------------------------------------------------
# fakes
# ------------------------------------------------------------------------------------------
class FakePlan(BaseModel):
    source_id: str
    status: str = "proposed"
    steps: list[str] = []
    problems: list[str] = []
    tools_seen: list[str] = []
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    notes: list[str] = []


class FakeIngest(IngestFacade):
    """Connections are REAL (`databridge_mcp_ingest` file ops in tmp); plans and runs are fake —
    no MCP server is launched. A run writes the §5.2 `report.json` so the on-disk view is real."""

    def __init__(self, tools: list[str]) -> None:
        super().__init__()
        self.tools = tools
        self.plans: dict[str, FakePlan] = {}
        self.approvals: list[tuple[str, str]] = []
        self.fail: Optional[Exception] = None
        self.report_status = "ok"
        self.progress_events = [{"stage": "collect", "done": 1}, {"stage": "build", "done": 1}]

    def propose_plan(self, source_id, *, harness=None, bind_notes=None, expected_revision=None,
                     revision_of=None):
        conn = self.load_connection(source_id)
        if expected_revision is not None and revision_of(conn) != expected_revision:
            raise RuntimeError("the connection changed")
        plan = FakePlan(source_id=source_id, steps=["documents"], tools_seen=list(self.tools),
                        notes=list(bind_notes or []))
        self.plans[source_id] = plan
        return plan

    def load_plan(self, source_id):
        if source_id not in self.plans:
            raise LookupError(f"no plan for {source_id!r}")
        return self.plans[source_id]

    def approve_plan(self, source_id, *, principal, vouch_read_tools=None, bind_notes=None,
                     expected_plan_digest=None, expected_revision=None, revision_of=None):
        # The gateway must hand over the principal OBJECT; the package's own gate decides, so a
        # string (or a look-alike) is refused here exactly as `plans.approve_plan` refuses it.
        # `bind_notes` are written by the same approval, as the package does (R-027 r1 #3).
        from databridge_mcp_ingest.plans import require_authenticated_approver
        who = require_authenticated_approver(principal)
        self.approvals.append((source_id, who["principal_id"]))
        current = self.load_plan(source_id)
        plan = current.model_copy(update={"status": "approved",
                                          "approved_by": who["principal_id"],
                                          "notes": [*current.notes, *(bind_notes or [])]})
        self.plans[source_id] = plan
        return plan

    def _save_plan(self, plan):
        self.plans[plan.source_id] = plan

    def run_ingest(self, source_id, *, store, harness, progress_cb, run_id):
        run_dir = self.run_dir(source_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        for ev in self.progress_events:
            progress_cb(dict(ev))
        if self.fail is not None:
            raise self.fail
        report = {"source_id": source_id, "run_id": run_id, "status": self.report_status,
                  "counts": {"inventory": len(self.progress_events)}}
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return report


def bound_notes(source_id):
    """The binding a gateway proposal carries: the revision of the connection as it is now. A plan
    a test writes directly carries it too, or it is (rightly) stale (R-027 r2 #5)."""
    from databridge_gateway.platform_runtime import PLAN_REVISION_NOTE
    from databridge_gateway.sources_routes import connection_revision
    from databridge_mcp_ingest.connections import load_connection
    return [f"{PLAN_REVISION_NOTE}{connection_revision(load_connection(source_id))}"]


def approve(c, source_id, **body):
    """Approve ``source_id``'s plan as a person does: with the `plan_digest` of the plan the
    page shows (`GET /sources/{id}`), which every approval must name (R-027 r1 #2)."""
    shown = c.get(f"/sources/{source_id}")
    digest = shown.json().get("plan_digest") if shown.status_code == 200 else None
    return c.post(f"/sources/{source_id}/plan/approve",
                  json={"plan_digest": digest or "none-shown", **body})


class FakeService:
    """`IngestService` for the adapter-agent manager: plan approved, each run returns a report
    carrying the refusals it was configured with."""

    def __init__(self, refused: list[dict]) -> None:
        self.refused = refused
        self.runs: dict[str, list[dict]] = {}

    def plan_status(self, source_id):
        return "approved"

    def run_ingest(self, source_id, *, store, harness, progress_cb=None):
        from databridge_adapter_agents import normalize_report

        n = len(self.runs.get(source_id, [])) + 1
        raw = {"source_id": source_id, "run_id": f"r{n}", "status": "ok",
               "started": "2026-10-01T09:00:00Z", "ended": "2026-10-01T09:00:05Z",
               "counts": {"inventory": 2}, "refused": list(self.refused)}
        self.runs.setdefault(source_id, []).append(raw)
        if progress_cb:
            progress_cb({"stage": "build", "done": 1})
        return normalize_report(raw, source_id=source_id)

    def list_runs(self, source_id):
        from databridge_adapter_agents import normalize_report
        return [normalize_report(r, source_id=source_id) for r in self.runs.get(source_id, [])]


def _refusals(kind: str, docs: int, per_doc: int) -> list[dict]:
    out = []
    for d in range(docs):
        for i in range(per_doc):
            # Document identity is the hashed full path (P4); a bare "doc-0" is no longer accepted
            # as grounded evidence by adapter_agents (R-017-t04).
            out.append({"axis": "node", "kind": kind,
                        "document_id": hashlib.sha256(f"doc-{d}".encode()).hexdigest(),
                        "char_start": 10 * i, "char_end": 10 * i + 5,
                        "text": f"entity 'x{d}{i}': unknown kind '{kind}'"})
    return out


def _mount(env, *, refused: Optional[list[dict]] = None, ontology: bool = False):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_graph.store import GraphStore

    from databridge_gateway.ontology_routes import register_ontology_routes

    app = FastAPI()
    app.state.graph_store = GraphStore()
    reloads: list[int] = []

    def reload_graph():
        reloads.append(1)
        return {"nodes": len(reloads)}

    app.state.reload_graph = reload_graph
    rt = build_platform(app)
    rt.harness = None
    rt.ingest = FakeIngest(tools=["read_text_file", "list_directory", "write_file"])
    rt.manager = AdapterAgentManager(
        service=FakeService(refused or []), registry=AdapterAgentRegistry(),
        store=app.state.graph_store)
    app.state.platform = rt
    sources_routes.register_sources_routes(app)
    adapter_agents_routes.register_adapter_agents_routes(app)
    agents_routes.register_agents_routes(app)
    automation_routes.register_automation_routes(app)
    if ontology:
        register_ontology_routes(app)
    return app, rt, reloads


def _aa_spec(agent_id: str, connections: list[str], **over) -> dict:
    d = {"spec": "adapter_agent/v1", "id": agent_id, "title": "Test sources",
         "created_from": "Keep the test folder in the graph; check hourly",
         "connections": connections, "plans": {c: "approved" for c in connections},
         "sync": {"type": "schedule", "schedule": {"cron": "0 * * * *", "timezone": "UTC"}},
         "extract": {"pack": "workspace"}, "status": "active"}
    d.update(over)
    return d


# ------------------------------------------------------------------------------------------
# 1. authentication on every route
# ------------------------------------------------------------------------------------------
def test_every_platform_route_refuses_without_a_credential(env):
    from databridge_gateway import create_app

    app = create_app()
    routes = [(m, r.path) for r in app.routes
              if getattr(getattr(r, "endpoint", None), "__module__", None) in PLATFORM_MODULES
              for m in sorted(r.methods or ())]
    writes = [(m, p) for m, p in routes if m in {"POST", "PATCH", "PUT", "DELETE"}]
    assert writes, "no platform write routes were found — the derivation is vacuous"
    assert {m for m, _ in writes} >= {"POST", "PATCH", "DELETE"}
    client = TestClient(app)                          # no lifespan: auth runs before any work
    for method, path in routes:
        url = path.replace("{", "").replace("}", "")  # any concrete value for each parameter
        r = client.request(method, url, json={})
        assert r.status_code == 401, (method, path, r.status_code, r.text[:200])
        assert r.json()["refusal"]["code"] == "UNAUTHENTICATED"


# ------------------------------------------------------------------------------------------
# 2. sources
# ------------------------------------------------------------------------------------------
def test_register_plan_approve_run_and_reload(env):
    app, rt, reloads = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/sources", json={"mode": "command", "id": "test-files", "title": "Files",
                                     "command": "npx", "args": ["-y", "some-server", "/data"]})
        assert r.status_code == 200, r.text
        assert [x["id"] for x in r.json()["registered"]] == ["test-files"]
        assert (env["sources"] / "test-files.yaml").is_file()
        assert c.post("/sources", json={"mode": "command", "id": "test-files",
                                        "command": "npx"}).status_code == 409

        listed = c.get("/sources").json()["sources"]
        assert [s["id"] for s in listed] == ["test-files"]
        assert listed[0]["plan_status"] is None

        # no approved plan → the run is refused before anything starts
        r = c.post("/sources/test-files/runs")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "PLAN_NOT_APPROVED"
        assert reloads == []

        plan = c.post("/sources/test-files/plan").json()["plan"]
        assert plan["status"] == "proposed" and plan["tools_seen"] == rt.ingest.tools
        detail = c.get("/sources/test-files").json()
        assert detail["tools"] == rt.ingest.tools

        # approval is the PRINCIPAL's, whatever the body claims
        r = approve(c, "test-files", approved_by="mallory")
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["approved_by"] == "analyst-7"
        assert rt.ingest.approvals == [("test-files", "analyst-7")]

        r = c.post("/sources/test-files/runs")
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        assert run_id and r.json()["started_by"] == "analyst-7"

        # TestClient finishes background tasks before returning: the run has ended
        assert reloads == [1]
        got = c.get(f"/sources/test-files/runs/{run_id}").json()
        assert got["status"] == "ok"
        assert got["report"]["run_id"] == run_id
        assert [e["stage"] for e in got["events"]] == \
            [e["stage"] for e in rt.ingest.progress_events]
        assert got["graph_counts"] == {"nodes": len(reloads)}
        assert [x["run_id"] for x in c.get("/sources/test-files/runs").json()["runs"]] == [run_id]
        assert c.get("/sources/test-files/health").json()["overall"] == "healthy"


def test_a_failed_run_still_reloads_and_is_reported(env):
    app, rt, reloads = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        c.post("/sources", json={"mode": "command", "id": "flaky", "command": "npx"})
        c.post("/sources/flaky/plan")
        approve(c, "flaky")
        rt.ingest.fail = RuntimeError("server went away")
        run_id = c.post("/sources/flaky/runs").json()["run_id"]
        assert reloads == [1]
        got = c.get(f"/sources/flaky/runs/{run_id}").json()
        assert got["status"] == "failed"
        # R-028 #6 (ROUTES): a fixed sentence and a reference, never the exception text
        assert "server went away" not in got["error"] and "reference ERR-" in got["error"]


def test_bad_ids_are_refused_before_touching_disk(env):
    app, _rt, _ = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        assert c.get("/sources/..%2Fx/runs").status_code in (400, 404)
        assert c.get("/sources/.hidden/health").status_code == 400
        assert c.get("/sources/ok/runs/.hidden").status_code == 400


# ------------------------------------------------------------------------------------------
# 3. adapter agents + forwarding proposals
# ------------------------------------------------------------------------------------------
def test_adapter_agent_lifecycle_is_attributed_to_the_principal(env):
    app, rt, reloads = _mount(env)
    with TestClient(app, headers=auth_headers("fcc-3")) as c:
        r = c.post("/adapter-agents", json={"spec": _aa_spec("test_sources", ["test-files"])})
        assert r.status_code == 200, r.text
        assert c.post("/adapter-agents",
                      json={"spec": _aa_spec("test_sources", ["test-files"])}).status_code == 409
        assert [a["spec"]["id"] for a in c.get("/adapter-agents").json()["adapter_agents"]] == \
            ["test_sources"]

        assert c.patch("/adapter-agents/test_sources",
                       json={"freshness": {"max_age_hours": 6}}).status_code == 200
        assert c.post("/adapter-agents/test_sources/pause").json()["adapter_agent"]["status"] \
            == "paused"
        assert c.post("/adapter-agents/test_sources/resume").json()["adapter_agent"]["status"] \
            == "active"

        r = c.post("/adapter-agents/test_sources/sync")
        assert r.status_code == 202
        job = c.get(f"/adapter-agents/test_sources/jobs/{r.json()['job_id']}").json()
        assert job["status"] == "completed" and job["result"]["status"] == "completed"
        assert reloads == [1]
        runs = c.get("/adapter-agents/test_sources/runs").json()["runs"]
        assert [x["trigger"]["by"] for x in runs] == ["fcc-3"]
        assert c.get("/adapter-agents/test_sources/health").json()["agent_id"] == "test_sources"

        assert c.request("DELETE", "/adapter-agents/test_sources",
                         json={"reason": "retired", "by": "mallory"}).status_code == 200
        detail = c.get("/adapter-agents/test_sources").json()
        assert detail["deleted"] is True
        history = detail["history"]
        assert history, "the registry recorded no history"
        assert {h["by"] for h in history} == {"fcc-3"}
        assert "test_sources" not in [a["spec"]["id"] for a in
                                      c.get("/adapter-agents").json()["adapter_agents"]]


def test_forwarded_proposal_goes_to_ontology_as_adapter_agent_and_never_auto_applies(env):
    from databridge_adapter_agents import ProposalPolicy

    policy = ProposalPolicy()
    # enough refusals of one unknown kind to cross the manager's own thresholds
    refused = _refusals("vessel", docs=policy.min_documents,
                        per_doc=-(-policy.min_occurrences // policy.min_documents))
    app, rt, _ = _mount(env, refused=refused, ontology=True)
    pack_file = env["packs"] / "workspace" / "ontology.yaml"
    before = pack_file.read_bytes()
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.put("/ontology/auto_approve", json={"enabled": True, "set_by": "ops.lead"})
        assert r.status_code == 200, r.text
        c.post("/adapter-agents", json={"spec": _aa_spec("ship_sources", ["ship-files"])})
        c.post("/adapter-agents/ship_sources/sync")

        proposals = c.get("/adapter-agents/proposals").json()["proposals"]
        assert [p["name"] for p in proposals] == ["vessel"]
        pid = proposals[0]["proposal_id"]
        assert proposals[0]["applied"] is False

        r = c.post(f"/adapter-agents/proposals/{pid}/forward")
        assert r.status_code == 200, r.text
        onto = r.json()["ontology_proposal"]
        assert onto["origin"] == "adapter_agent"
        # auto-approve is ON, and it waits for a human (MCP evidence is untrusted, so the
        # guardrail escalates it too; the origin rule is proven on its own in the next test)
        assert onto["status"] in ("pending", "escalated"), onto
        assert onto["decided_by"] is None
        assert onto["proposed_by"] == "adapter_agent:ship_sources"
        assert r.json()["forwarded_by"] == "analyst-7"
        assert pack_file.read_bytes() == before           # nothing was written to the pack

        again = c.post(f"/adapter-agents/proposals/{pid}/forward")
        assert again.status_code == 409
        listed = c.get("/adapter-agents/proposals").json()["proposals"]
        assert listed[0]["forwarded"]["ontology_proposal_id"] == onto["id"]


# ------------------------------------------------------------------------------------------
# 4. automation: the engine routes a firing by spec kind; the lifespan hosts it
# ------------------------------------------------------------------------------------------
def test_manual_fire_routes_an_adapter_agent_to_its_sync_as_the_principal(env):
    app, rt, reloads = _mount(env)
    from databridge_automation import TriggerEngine
    from databridge_gateway.platform_runtime import _spec_loader

    rt.engine = TriggerEngine(make_engine_runner(app), spec_loader=_spec_loader)
    with TestClient(app, headers=auth_headers("audit-1")) as c:
        c.post("/adapter-agents", json={"spec": _aa_spec("fire_sources", ["fire-files"])})
        assert "fire_sources" in rt.engine.specs            # reloaded after the save
        status = c.get("/automation/status").json()
        assert [s["id"] for s in status["specs"]] == ["fire_sources"]
        assert status["running"] is False                   # engine off in this test

        r = c.post("/automation/fire_sources/fire", json={"wait": True, "by": "mallory"})
        assert r.status_code == 200, r.text
        firing = r.json()["firing"]
        assert firing["status"] == "succeeded" and firing["spec_kind"] == "adapter_agent"
        runs = c.get("/adapter-agents/fire_sources/runs").json()["runs"]
        assert [x["trigger"]["by"] for x in runs] == ["audit-1"]
        assert reloads == [1]
        assert c.post("/automation/nope/fire", json={"wait": True}).status_code == 404
        recent = c.get("/automation/status").json()["recent_firings"]
        assert any(f.get("firing_id") == firing["firing_id"] for f in recent)


def test_catalogue_is_what_the_compiler_sees(env):
    app, _rt, _ = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        c.post("/sources", json={"mode": "command", "id": "cat-files", "command": "npx"})
        body = c.get("/platform/catalogue").json()
    from databridge_automation import load_catalogue

    expected = load_catalogue().as_record()
    assert [x["id"] for x in body["connections"]] == [x["id"] for x in expected["connections"]]
    assert "cat-files" in [x["id"] for x in body["connections"]]
    assert body["kinds"] == expected["kinds"] and body["kinds"]
    assert body["internal_tools"] == expected["internal_tools"]


@pytest.mark.parametrize("flag,expect_running", [("1", True), (None, False)])
def test_engine_starts_and_stops_with_the_app_lifespan(env, monkeypatch, flag, expect_running):
    from databridge_gateway import create_app
    from databridge_graph.packs import clear_caches

    # the full gateway boots on the repo's own packs; the store starts empty
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    monkeypatch.delenv("WALNUT_LOAD_CLINIC_SEED", raising=False)
    clear_caches()
    if flag is None:
        monkeypatch.delenv("WALNUT_AUTOMATION_ENGINE", raising=False)   # pytest ⇒ off
    else:
        monkeypatch.setenv("WALNUT_AUTOMATION_ENGINE", flag)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        rt = c.app.state.platform
        assert isinstance(rt, PlatformRuntime)
        assert rt.engine is not None
        assert rt.engine_running is expect_running
        assert (rt.engine._loop_task is not None and not rt.engine._loop_task.done()) \
            is expect_running
        assert c.get("/automation/status").json()["running"] is expect_running
        # the manager writes to THE shared store, not a second one
        assert rt.manager.store is c.app.state.graph_store
    assert rt.engine_running is False
    assert rt.engine._loop_task is None


def test_forwarding_forces_origin_adapter_agent_even_over_an_owner_claim(env):
    """The origin rule on its own: a filed proposal the guardrail WOULD auto-approve for an owner
    (trusted, grounded evidence; a meta-kind that is not attested/claim/subject) — and whose
    stored request even claims ``origin: owner`` — is forwarded as ``adapter_agent`` and waits."""
    from databridge_core import SourcePointer
    from databridge_graph.meta import NodeMetaKind

    from databridge_gateway.ontology_routes import guardrail

    evidence = SourcePointer(source_id="ops-sheet", kind="doc_span", document_id="ops-sheet",
                             char_start=0, char_end=7, trust="trusted")
    ev = evidence.model_dump(mode="json", exclude_none=True)
    approvable = [m.value for m in NodeMetaKind
                  if guardrail(axis="node", binds_to=m.value, name="harbour", pack="workspace",
                               proposed_by="adapter_agent:ops", evidence=(evidence,)
                               ).auto_approvable]
    assert approvable, "no meta-kind is owner-approvable here — the test would prove nothing"

    app, rt, _ = _mount(env, ontology=True)
    pack_file = env["packs"] / "workspace" / "ontology.yaml"
    before = pack_file.read_bytes()
    request = {"pack": "workspace", "axis": "node", "name": "harbour",
               "binds_to": approvable[0], "src": [], "dst": [], "description": "tamper test",
               "proposed_by": "adapter_agent:ops", "declares_subject": False,
               "evidence": [ev], "origin": "owner"}
    rt.manager.proposal_log.append({"proposal_id": "aap_tamper", "status": "filed",
                                    "applied": False, "pack": "workspace", "axis": "node",
                                    "name": "harbour", "proposed_by": "adapter_agent:ops",
                                    "adapter_agent": "ops", "request": request})
    with TestClient(app, headers=AUTH_HEADERS) as c:
        assert c.put("/ontology/auto_approve",
                     json={"enabled": True, "set_by": "ops.lead"}).status_code == 200
        r = c.post("/adapter-agents/proposals/aap_tamper/forward", json={"origin": "owner"})
        assert r.status_code == 200, r.text
        onto = r.json()["ontology_proposal"]
    assert onto["origin"] == "adapter_agent"
    assert onto["auto_approvable"] is True                  # an owner's would have applied
    assert onto["status"] == "pending"
    assert "auto_approve_bypassed" in onto
    assert pack_file.read_bytes() == before


# ------------------------------------------------------------------------------------------
# 5. R-019 (Codex batch 2, n-platform-routes)
# ------------------------------------------------------------------------------------------
def _sources_on_disk(env) -> list[str]:
    d = env["sources"]
    return sorted(p.name for p in d.glob("*.yaml")) if d.is_dir() else []


def test_command_sources_are_off_over_http_unless_the_operator_allows_the_executable(
        env, monkeypatch):
    """#1: registering a command source = host code execution; authentication is not permission
    to run programs on the host. Unset allow-list -> refused; a path-qualified command does not
    ride on a bare-name entry."""
    app, _rt, _ = _mount(env)
    monkeypatch.delenv("WALNUT_COMMAND_SOURCES", raising=False)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/sources", json={"mode": "command", "id": "evil", "command": "npx",
                                     "args": ["-y", "anything"]})
        assert r.status_code == 403, r.text
        assert r.json()["refusal"]["code"] == "COMMAND_SOURCES_DISABLED"
        # the same through a full `connection` declaration
        r = c.post("/sources", json={"mode": "connection", "connection": {
            "id": "evil2", "transport": {"type": "stdio", "command": "npx"}}})
        assert r.status_code == 403, r.text
        assert _sources_on_disk(env) == []

        monkeypatch.setenv("WALNUT_COMMAND_SOURCES", "npx,uvx")
        for cmd in ("C:/Users/x/Downloads/npx.exe", "./npx", "powershell", "cmd.exe"):
            r = c.post("/sources", json={"mode": "command", "id": "evil3", "command": cmd})
            assert r.status_code == 403, (cmd, r.text)
        assert _sources_on_disk(env) == []
        for cmd in ("npx", "NPX.cmd", "uvx"):
            r = c.post("/sources", json={"mode": "command", "id": f"ok-{cmd.lower()[:3]}",
                                         "command": cmd, "replace": True})
            assert r.status_code == 200, (cmd, r.text)


def test_http_sources_may_not_send_host_secrets_to_an_unlisted_host(env, monkeypatch):
    """#1 (SSRF/secret forwarding): `headers_from` names a HOST env var; the gateway would send
    its value to the URL. Only an operator-listed host may receive one."""
    app, _rt, _ = _mount(env)
    body = {"mode": "url", "id": "remote", "url": "https://attacker.example/mcp",
            "headers_from": {"Authorization": "ANTHROPIC_API_KEY"}}
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/sources", json=body)
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "SECRET_HOST_NOT_ALLOWED"
        assert _sources_on_disk(env) == []
        # no secret forwarded -> nothing to exfiltrate, registration proceeds
        r = c.post("/sources", json={**body, "headers_from": {}})
        assert r.status_code == 200, r.text
        monkeypatch.setenv("WALNUT_SOURCE_SECRET_HOSTS", "mcp.internal.example")
        r = c.post("/sources", json={**body, "id": "internal", "replace": True,
                                     "url": "https://mcp.internal.example/mcp"})
        assert r.status_code == 200, r.text


def test_auto_approve_and_import_paths_are_refused_over_http(env, tmp_path):
    """#2: an approval is the signed-in principal's act on /plan/approve — not a registration
    flag; and a request may not choose which host file is read as declarations."""
    app, _rt, _ = _mount(env)
    other = tmp_path / "elsewhere.yaml"
    other.write_text("version: 1\nservers: {}\n", encoding="utf-8")
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/sources", json={"mode": "command", "id": "auto", "command": "npx",
                                     "auto_approve": True})
        assert r.status_code == 403 and r.json()["refusal"]["code"] == \
            "AUTO_APPROVE_NOT_OVER_HTTP"
        r = c.post("/sources", json={"mode": "connection", "connection": {
            "id": "auto2", "auto_approve": True,
            "transport": {"type": "stdio", "command": "npx"}}})
        assert r.status_code == 403
        r = c.post("/sources", json={"mode": "import", "path": str(other)})
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "IMPORT_PATH_NOT_ALLOWED"
    assert _sources_on_disk(env) == []


def test_replacing_a_connection_revokes_its_plan_approval(env):
    """#2: an approval covers the connection a person reviewed. Replacing the transport under
    the same id returns the plan to `proposed` (recorded, not deleted) and the next run is
    refused; re-registering an IDENTICAL connection revokes nothing."""
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        base = {"mode": "command", "id": "swap", "command": "npx", "args": ["-y", "reviewed"]}
        assert c.post("/sources", json=base).status_code == 200
        c.post("/sources/swap/plan")
        assert approve(c, "swap").status_code == 200

        same = c.post("/sources", json={**base, "replace": True})
        assert same.status_code == 200 and same.json()["plan_approval_revoked"] == []
        assert rt.ingest.load_plan("swap").status == "approved"

        swapped = c.post("/sources", json={**base, "replace": True,
                                           "args": ["-y", "something-else"]})
        assert swapped.status_code == 200, swapped.text
        assert swapped.json()["plan_approval_revoked"] == ["swap"]
        plan = rt.ingest.load_plan("swap")
        assert plan.status == "proposed" and plan.approved_by is None
        assert any("revoked by 'analyst-7'" in n for n in plan.notes)
        r = c.post("/sources/swap/runs")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "PLAN_NOT_APPROVED"


@pytest.mark.parametrize("status", ["failed", "partial", None])
def test_an_ingest_job_is_completed_only_for_a_successful_run_status(env, status):
    """#7: an allow-list — a report whose status is not a success is not a completed job."""
    from databridge_gateway.sources_routes import _RUN_SUCCESS, job_status_for_run

    assert status not in _RUN_SUCCESS
    app, rt, _ = _mount(env)
    rt.ingest.report_status = status
    with TestClient(app, headers=AUTH_HEADERS) as c:
        c.post("/sources", json={"mode": "command", "id": "st", "command": "npx"})
        c.post("/sources/st/plan")
        approve(c, "st")
        c.post("/sources/st/runs")
    job = rt.jobs.list("ingest", "st")[-1]
    assert job["status"] == job_status_for_run(status) != "completed"
    assert job["run_status"] == status
    assert all(job_status_for_run(s) == "completed" for s in _RUN_SUCCESS)


def test_forwarding_overwrites_a_forged_proposer_and_forwards_once(env):
    """#6: the proposer comes from the log's own `adapter_agent`, not the stored request; and
    two forwards produce one ontology proposal."""
    import concurrent.futures as cf

    app, rt, _ = _mount(env, ontology=True)
    request = {"pack": "workspace", "axis": "node", "name": "harbour", "binds_to": "entity",
               "src": [], "dst": [], "description": "forged proposer",
               "proposed_by": "ops.lead", "declares_subject": False, "evidence": []}
    rt.manager.proposal_log.append({"proposal_id": "aap_forged", "status": "filed",
                                    "applied": False, "pack": "workspace", "axis": "node",
                                    "name": "harbour", "proposed_by": "ops.lead",
                                    "adapter_agent": "ops", "request": request})
    with TestClient(app, headers=AUTH_HEADERS) as c:
        with cf.ThreadPoolExecutor(4) as pool:
            codes = list(pool.map(
                lambda _: c.post("/adapter-agents/proposals/aap_forged/forward"), range(4)))
        ok = [r for r in codes if r.status_code == 200]
        assert len(ok) == 1, [(r.status_code, r.text[:120]) for r in codes]
        assert all(r.status_code == 409 for r in codes if r is not ok[0])
        assert ok[0].json()["ontology_proposal"]["proposed_by"] == "adapter_agent:ops"
    state = json.loads((env["tmp"] / "state" / "ontology.json").read_text(encoding="utf-8"))
    assert len([p for p in state["proposals"] if p.get("name") == "harbour"]) == 1


def test_a_paused_adapter_agent_is_not_queued_for_sync(env):
    """#3 (the part that was real): the manager already refuses a paused agent at execution time
    from the CURRENT registry entry; the route now says so up front instead of a 202."""
    app, rt, reloads = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        c.post("/adapter-agents", json={"spec": _aa_spec("paused_one", ["x-files"])})
        c.post("/adapter-agents/paused_one/pause")
        r = c.post("/adapter-agents/paused_one/sync")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "ADAPTER_AGENT_PAUSED"
        assert rt.jobs.list("adapter_sync", "paused_one") == []


# ------------------------------------------------------------------------------------------
# R-019-adapter-agents-r2: a draft adapter agent can be turned live over HTTP, through the gate
# ------------------------------------------------------------------------------------------
def test_a_draft_adapter_agent_is_activated_only_through_the_managers_checks(env):
    """`POST /adapter-agents/{id}/activate` runs `manager.activate` (plan-approved + valid-policy
    checks) as the authenticated principal; a draft that fails a check stays a draft."""
    from conftest import TEST_TOKENS

    who = "analyst-7"
    assert who in TEST_TOKENS.values()
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers(who)) as c:
        c.post("/adapter-agents", json={"spec": _aa_spec("ready_one", ["x-files"],
                                                         status="draft")})
        c.post("/adapter-agents", json={"spec": _aa_spec("blocked_one", ["y-files"],
                                                         status="draft",
                                                         plans={"y-files": "proposed"})})
        assert rt.manager.registry.get("ready_one").status == "draft"      # non-vacuous
        # the refusal is the manager's own reason, derived — not a literal
        expected = rt.manager.agent_gate(rt.manager.registry.get("blocked_one"))
        assert expected
        bad = c.post("/adapter-agents/blocked_one/activate")
        assert bad.status_code == 409, bad.text
        assert bad.json()["refusal"]["code"] == "ADAPTER_AGENT_NOT_ACTIVATABLE"
        assert all(reason in bad.json()["refusal"]["message"] for reason in expected)
        assert rt.manager.registry.get("blocked_one").status == "draft"

        ok = c.post("/adapter-agents/ready_one/activate", json={"by": "mallory"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["adapter_agent"]["status"] == "active"
        last = rt.manager.registry.history("ready_one")[-1]
        assert last.get("by") == who and last.get("action") == "activate"

        again = c.post("/adapter-agents/ready_one/activate")               # only a draft
        assert again.status_code == 409
        assert _anon_status(app, "/adapter-agents/ready_one/activate") == 401


def _anon_status(app, path: str) -> int:
    with TestClient(app) as anon:
        return anon.post(path).status_code


# ------------------------------------------------------------------------------------------
# R-019 round 2 (Codex, n-platform-routes) — every expectation derived from the policy under test
# ------------------------------------------------------------------------------------------
def _cmd(c, sid, **over):
    return c.post("/sources", json={"mode": "command", "id": sid, "command": "npx",
                                    "args": ["-y", "reviewed"], **over})


def test_round2_a_plan_proposed_for_a_replaced_connection_cannot_be_approved(env):
    """#1: replacing the connection SUPERSEDES its plan (approved or merely proposed); approving
    that stale plan is refused until a plan is proposed for the connection that now exists."""
    from databridge_gateway.platform_runtime import (PLAN_REVISION_NOTE, PLAN_SUPERSEDED,
                                                     plan_binding)

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "bind").status_code == 200
        c.post("/sources/bind/plan")                                   # proposed, NOT approved
        assert plan_binding(rt.ingest.load_plan("bind"), PLAN_REVISION_NOTE) not in (
            None, PLAN_SUPERSEDED)                                     # non-vacuous: bound
        r = _cmd(c, "bind", replace=True, args=["-y", "something-else"])
        assert r.status_code == 200, r.text
        assert r.json()["plan_approval_revoked"] == []                 # nothing was approved...
        assert plan_binding(rt.ingest.load_plan("bind"), PLAN_REVISION_NOTE) == PLAN_SUPERSEDED
        bad = approve(c, "bind")                     # ...and it is still stale
        assert bad.status_code == 409 and (bad.json()["refusal"]["code"], bad.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_STALE"], "PLAN_STALE")
        assert rt.ingest.load_plan("bind").status == "proposed"
        assert rt.ingest.approvals == []
        c.post("/sources/bind/plan")                                   # proposed for B
        assert approve(c, "bind").status_code == 200
        assert c.post("/sources/bind/runs").status_code == 202


def test_round2_a_connection_is_not_replaced_while_it_runs(env):
    """#1: admission marks the source busy under its lock; a replacement arriving while the run
    is in flight is refused, so the run cannot execute the replacement under the old approval."""
    app, rt, _ = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        _cmd(c, "busy")
        before = (env["sources"] / "busy.yaml").read_text(encoding="utf-8")
        rt.jobs.start("ingest", "busy", run_id="r-inflight")          # a run in flight
        r = _cmd(c, "busy", replace=True, args=["-y", "other"])
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "RUN_IN_PROGRESS"
        assert (env["sources"] / "busy.yaml").read_text(encoding="utf-8") == before


def test_round2_a_queued_run_refuses_a_connection_changed_after_admission(env, monkeypatch):
    """#1: the job re-checks at START that the connection is the revision it was admitted for;
    a connection changed behind the route (another process) is refused, never executed."""
    from databridge_gateway.sources_routes import _ingest_job, connection_revision

    app, rt, _ = _mount(env)
    ran: list[str] = []
    real = rt.ingest.run_ingest
    monkeypatch.setattr(rt.ingest, "run_ingest",
                        lambda sid, **kw: ran.append(sid) or real(sid, **kw))
    with TestClient(app, headers=AUTH_HEADERS) as c:
        _cmd(c, "pin")
        c.post("/sources/pin/plan")
        approve(c, "pin")
    pinned = connection_revision(rt.ingest.load_connection("pin"))
    # control first: admitted for the current revision, the job runs
    job0 = rt.jobs.start("ingest", "pin", run_id="r0")
    _ingest_job(app, "pin", "r0", job0["job_id"], pinned)
    assert ran == ["pin"]
    # the connection file replaced OUTSIDE the route (e.g. the CLI) after admission
    spec = {"id": "pin", "transport": {"type": "stdio", "command": "npx", "args": ["-y", "B"]}}
    rt.ingest.register_connection(spec, overwrite=True)
    assert connection_revision(rt.ingest.load_connection("pin")) != pinned   # non-vacuous
    job = rt.jobs.start("ingest", "pin", run_id="r1")
    _ingest_job(app, "pin", "r1", job["job_id"], pinned)
    got = rt.jobs.get(job["job_id"])
    assert got["status"] == "refused" and "changed after this run was admitted" in got["error"]
    # ...and pinning the NEW revision does not help: the plan was approved for the old one
    job2 = rt.jobs.start("ingest", "pin", run_id="r2")
    _ingest_job(app, "pin", "r2", job2["job_id"],
                connection_revision(rt.ingest.load_connection("pin")))
    assert rt.jobs.get(job2["job_id"])["status"] == "refused"
    assert ran == ["pin"]


def test_round2_replacing_a_connection_revokes_its_mapping_approval(env):
    """#2: an approved table mapping covers the connection whose tables were reviewed."""
    from databridge_mcp_ingest.mapping import (SourceMapping, TableMapping, load_mapping,
                                               save_mapping)

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        _cmd(c, "mapped")
        save_mapping(SourceMapping(source_id="mapped", approved_by="analyst-7", tables={
            "orders": TableMapping(status="approved"), "notes": TableMapping(status="unmapped")}))
        approved = sorted(t for t, tm in load_mapping("mapped").tables.items()
                          if tm.status == "approved")
        assert approved                                                # non-vacuous
        same = _cmd(c, "mapped", replace=True)                         # identical: nothing
        assert same.json()["mapping_approval_revoked"] == {}
        r = _cmd(c, "mapped", replace=True, args=["-y", "different-db"])
        assert r.status_code == 200, r.text
        assert r.json()["mapping_approval_revoked"] == {"mapped": approved}
    m = load_mapping("mapped")
    assert all(m.tables[t].status == "proposed" for t in approved)
    assert m.tables["notes"].status == "unmapped"                      # untouched
    assert m.approved_by is None
    assert all(any("revoked by 'analyst-7'" in n for n in m.tables[t].rationale)
               for t in approved)


def _revocation_actors(lines: list) -> list[dict]:
    from databridge_gateway.platform_runtime import REVOKED_ACTOR_TAG
    return [json.loads(n.split(REVOKED_ACTOR_TAG, 1)[1]) for n in lines if REVOKED_ACTOR_TAG in n]


def test_tidy_a_revocation_records_the_common_actor_object(env):
    """R-028 TIDY #5: a revocation is a decision, so it records the common actor object
    (`decision_ledger.actor_of`), not a bare id - on the plan and on every revoked table."""
    from databridge_core import decision_ledger as dl
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping, load_mapping, save_mapping

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "rv").status_code == 200
        c.post("/sources/rv/plan")
        assert approve(c, "rv").status_code == 200
        save_mapping(SourceMapping(source_id="rv", approved_by="analyst-7",
                                   tables={"orders": TableMapping(status="approved")}))
        assert _cmd(c, "rv", replace=True, args=["-y", "other"]).status_code == 200
    plan_actors = _revocation_actors(rt.ingest.load_plan("rv").notes)
    table_actors = _revocation_actors(load_mapping("rv").tables["orders"].rationale)
    assert plan_actors and table_actors                                 # non-vacuous
    for a in plan_actors + table_actors:
        assert a == dl.actor_of(a) and set(a) >= set(dl.ACTOR_FIELDS)
        assert a["principal_id"] == "analyst-7"


def test_tidy_a_revocation_by_a_bare_name_is_refused_and_writes_nothing(env):
    """R-028 TIDY #5: `by` must be the actor object; a typed name is not one."""
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping, load_mapping, save_mapping

    app, rt, _ = _mount(env)
    save_mapping(SourceMapping(source_id="nm", approved_by="analyst-7",
                               tables={"orders": TableMapping(status="approved")}))
    with pytest.raises(Exception):
        rt.ingest.revoke_mapping_approval("nm", by="analyst-7", reason="r")
    assert load_mapping("nm").tables["orders"].status == "approved"
    with pytest.raises(Exception):
        rt.ingest.revoke_plan_approval("nm", by="analyst-7", reason="r")


def test_tidy_a_mapping_revocation_loads_and_saves_under_the_mapping_lock(env, monkeypatch):
    """R-028 TIDY #2: load -> modify -> save of the mapping runs inside `mapping.mapping_lock`
    (the one every mapping writer takes), so a concurrent approval cannot be overwritten."""
    import contextlib

    from databridge_core import decision_ledger as dl
    from databridge_mcp_ingest import mapping as mapping_mod
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping, save_mapping

    app, rt, _ = _mount(env)
    save_mapping(SourceMapping(source_id="lk", approved_by="analyst-7",
                               tables={"orders": TableMapping(status="approved")}))
    held: list[str] = []
    seen: list[tuple[str, bool]] = []
    real_lock, real_load, real_save = (mapping_mod.mapping_lock, rt.ingest.load_mapping,
                                       rt.ingest._save_mapping)

    @contextlib.contextmanager
    def spy_lock(source_id, *a, **k):
        with real_lock(source_id, *a, **k):
            held.append(source_id)
            try:
                yield
            finally:
                held.pop()

    monkeypatch.setattr(mapping_mod, "mapping_lock", spy_lock)
    monkeypatch.setattr(rt.ingest, "load_mapping",
                        lambda sid: (seen.append(("load", sid in held)), real_load(sid))[1])
    monkeypatch.setattr(rt.ingest, "_save_mapping",
                        lambda m: (seen.append(("save", m.source_id in held)), real_save(m))[1])
    actor = dl.actor_of({"principal_id": "analyst-7", "authenticated_by": "bearer:test",
                         "principal_type": "Principal", "dev_only": False})
    assert rt.ingest.revoke_mapping_approval("lk", by=actor, reason="r") == ["orders"]
    assert [k for k, _ in seen] == ["load", "save"]                     # non-vacuous
    assert all(locked for _, locked in seen)


def test_round2_package_runners_need_the_operators_explicit_risk_acceptance(env, monkeypatch):
    """#3: listing a package runner is listing arbitrary code — refused unless accepted; and a
    caller may not choose env / env_from / cwd / ${VAR} for a command over HTTP."""
    from databridge_gateway.sources_routes import RUNNER_RISK_ENV, _CODE_RUNNERS

    assert "npx" in _CODE_RUNNERS                                      # the case under test
    app, _rt, _ = _mount(env)
    monkeypatch.delenv(RUNNER_RISK_ENV, raising=False)
    env["extra_exes"] = ("files-server",)
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES", "npx,files-server")
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = _cmd(c, "runner")
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "COMMAND_SOURCES_DISABLED"
        assert RUNNER_RISK_ENV in r.json()["refusal"]["message"]
        ok = c.post("/sources", json={"mode": "command", "id": "fs", "command": "files-server"})
        assert ok.status_code == 200, ok.text                          # a non-runner is fine
        for extra in ({"env": {"NODE_OPTIONS": "--require /x.js"}}, {"env_from": {"K": "HOME"}},
                      {"args": ["${ANTHROPIC_API_KEY}"]}):
            r = c.post("/sources", json={"mode": "command", "id": "fs2",
                                         "command": "files-server", **extra})
            assert r.status_code == 403, (extra, r.text)
        r = c.post("/sources", json={"mode": "connection", "connection": {
            "id": "fs3", "transport": {"type": "stdio", "command": "files-server", "cwd": "/"}}})
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "COMMAND_ENV_NOT_OVER_HTTP"
    assert sorted(p.name for p in env["sources"].glob("*.yaml")) == ["fs.yaml"]


def test_round2_command_allow_listing_is_by_exact_resolved_path(env, monkeypatch, tmp_path):
    """#6: an entry for agent.exe does not authorise agent.cmd; case folds only where the OS
    does (os.path.normcase)."""
    import os

    from databridge_gateway.sources_routes import command_policy_refusal

    safe = tmp_path / "safe"
    safe.mkdir()
    exe = safe / "agent.exe"
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES", str(exe))
    assert command_policy_refusal(str(exe)) is None
    assert command_policy_refusal(str(safe / "agent.cmd")) is not None
    assert command_policy_refusal(str(safe / "agent.bat")) is not None
    assert command_policy_refusal(str(safe / "agent")) is not None
    upper = str(safe / "AGENT.EXE")
    expect_same = os.path.normcase(upper) == os.path.normcase(str(exe))
    assert (command_policy_refusal(upper) is None) is expect_same


def test_round2_http_sources_may_not_point_the_gateway_at_its_own_network(env, monkeypatch):
    """#5 (SSRF): every address the host resolves to must be global unless the operator lists
    the host; ${VAR} in a URL is refused (it would be expanded into the request)."""
    from databridge_gateway.sources_routes import ALLOWED_HOSTS_ENV, _non_global

    env["hosts"].update({"intranet.example": ["10.1.2.3"],
                         "meta.example": ["169.254.169.254"],
                         "mixed.example": ["93.184.216.34", "127.0.0.1"],
                         "mapped.example": ["::ffff:127.0.0.1"],
                         "127.0.0.1": ["127.0.0.1"]})
    assert not _non_global("93.184.216.34")                            # the default is public
    app, _rt, _ = _mount(env)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        for i, url in enumerate(["http://intranet.example/mcp", "http://meta.example/latest",
                                 "https://mixed.example/mcp", "http://mapped.example/",
                                 "http://127.0.0.1:8080/mcp", "file:///etc/passwd",
                                 "https://x.example/?k=${ANTHROPIC_API_KEY}"]):
            r = c.post("/sources", json={"mode": "url", "id": f"u{i}", "url": url})
            assert r.status_code == 403, (url, r.text)
            assert r.json()["refusal"]["code"] == "SOURCE_DESTINATION_NOT_ALLOWED", url
        assert c.post("/sources", json={"mode": "url", "id": "pub",
                                        "url": "https://public.example/mcp"}).status_code == 200
        monkeypatch.setenv(ALLOWED_HOSTS_ENV, "intranet.example")
        assert c.post("/sources", json={"mode": "url", "id": "intra",
                                        "url": "http://intranet.example/mcp"}).status_code == 200
    assert sorted(p.name for p in env["sources"].glob("*.yaml")) == ["intra.yaml", "pub.yaml"]


def test_round2_a_draft_adapter_agent_is_not_runnable_by_any_route(env):
    """#4: `/sync` refuses a draft up front, and `/automation/{id}/fire` answers 409 with the
    engine's recorded skip — neither runs it."""
    from databridge_automation import TriggerEngine, admission_refusal
    from databridge_gateway.platform_runtime import _spec_loader

    app, rt, _ = _mount(env)
    rt.engine = TriggerEngine(make_engine_runner(app), spec_loader=_spec_loader)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        c.post("/adapter-agents", json={"spec": _aa_spec("drafty", ["d-files"], status="draft")})
        assert rt.manager.registry.get("drafty").status == "draft"     # non-vacuous
        r = c.post("/adapter-agents/drafty/sync")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "ADAPTER_AGENT_NOT_ACTIVE"
        assert rt.jobs.list("adapter_sync", "drafty") == []
        expected = admission_refusal(rt.engine.specs["drafty"])
        assert expected                                                # derived, non-empty
        f = c.post("/automation/drafty/fire", json={"wait": False})
        assert f.status_code == 409 and f.json()["refusal"]["code"] == "SPEC_NOT_ADMITTED"
        assert expected in f.json()["refusal"]["message"]
        assert f.json()["firing"]["status"] == "skipped"
        assert c.get("/adapter-agents/drafty/runs").json()["runs"] == []


def _forged_row(rt, pid):
    request = {"pack": "workspace", "axis": "node", "name": f"kind_{pid}", "binds_to": "entity",
               "src": [], "dst": [], "description": "durable forward", "proposed_by": "ops",
               "declares_subject": False, "evidence": []}
    rt.manager.proposal_log.append({"proposal_id": pid, "status": "filed", "applied": False,
                                    "pack": "workspace", "axis": "node", "name": f"kind_{pid}",
                                    "proposed_by": "adapter_agent:ops", "adapter_agent": "ops",
                                    "request": request})
    return request


def _ontology_named(env, name):
    path = env["tmp"] / "state" / "ontology.json"
    if not path.is_file():
        return []
    state = json.loads(path.read_text(encoding="utf-8"))
    return [p for p in state["proposals"] if p.get("name") == name]


def test_round2_a_forward_that_crashed_after_filing_is_reconciled_not_duplicated(env,
                                                                                 monkeypatch):
    """#7: the ontology proposal is created, then the process dies before the `forwarded`
    record is written. The retry finds the proposal by its idempotency marker and completes the
    log; it files nothing new."""
    from databridge_gateway.adapter_agents_routes import forward_marker

    app, rt, _ = _mount(env, ontology=True)
    _forged_row(rt, "aap_crash")
    log_obj = rt.manager.proposal_log
    real_append = type(log_obj).append

    def dying_append(rec):
        if rec.get("event") == "forwarded":
            raise RuntimeError("process died")
        return real_append(log_obj, rec)

    monkeypatch.setattr(log_obj, "append", dying_append)
    with TestClient(app, headers=AUTH_HEADERS, raise_server_exceptions=False) as c:
        assert c.post("/adapter-agents/proposals/aap_crash/forward").status_code == 500
        filed = _ontology_named(env, "kind_aap_crash")
        assert len(filed) == 1 and forward_marker("aap_crash") in filed[0]["description"]
        monkeypatch.setattr(log_obj, "append", lambda rec: real_append(log_obj, rec))
        r = c.post("/adapter-agents/proposals/aap_crash/forward")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "ALREADY_FORWARDED"
        again = c.post("/adapter-agents/proposals/aap_crash/forward")
        assert again.status_code == 409
    assert len(_ontology_named(env, "kind_aap_crash")) == 1
    fw = [x for x in rt.manager.proposal_log.all() if x.get("event") == "forwarded"]
    assert [(x["proposal_id"], x.get("reconciled")) for x in fw] == [("aap_crash", True)]
    assert fw[0]["ontology_proposal_id"] == filed[0]["id"]


def test_round2_a_claimed_forward_blocks_a_second_worker(env):
    """#7: the claim is a file created exclusively — another worker (another process, so no
    shared in-memory lock) that finds it does not file a second proposal."""
    from databridge_gateway.adapter_agents_routes import _claim_path

    app, rt, _ = _mount(env, ontology=True)
    _forged_row(rt, "aap_race")
    claim = _claim_path(rt.manager, "aap_race")
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text('{"by": "worker-2"}', encoding="utf-8")         # worker 2 is mid-forward
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/adapter-agents/proposals/aap_race/forward")
    assert r.status_code == 409 and r.json()["refusal"]["code"] == "FORWARD_IN_PROGRESS"
    assert _ontology_named(env, "kind_aap_race") == []


def test_round2_an_empty_successful_ingest_counts_as_synced(env):
    """#8: health uses the same success allow-list as the job status."""
    from databridge_gateway.sources_routes import _RUN_SUCCESS

    assert "empty" in _RUN_SUCCESS                                     # the case under test
    app, rt, _ = _mount(env)
    rt.ingest.report_status = "empty"
    with TestClient(app, headers=AUTH_HEADERS) as c:
        _cmd(c, "quiet")
        c.post("/sources/quiet/plan")
        approve(c, "quiet")
        c.post("/sources/quiet/runs")
        h = c.get("/sources/quiet/health").json()
    assert h["overall"] == "healthy" and h["last_success"]["status"] == "empty"
    assert "no successful run yet" not in h["issues"]


# ------------------------------------------------------------------------------------------
# EF3-c wirings (R-020-EF3-ingest.md): write-tool advert on agent_write registration (F-08) and
# per-tool read vouching on plan approval (Codex b)
# ------------------------------------------------------------------------------------------
class _AdvertIngest(FakeIngest):
    def __init__(self, *a, fail=None, **kw):
        super().__init__(*a, **kw)
        self.adverts: list[str] = []
        self.advert_fail = fail

    def write_tool_advert(self, source_id):
        if self.advert_fail is not None:
            raise self.advert_fail
        self.adverts.append(source_id)
        return self.run_dir(source_id, "x").parent / f"{source_id}.tools.yaml"


def test_ef3_an_agent_write_registration_writes_its_live_tool_advert(env):
    """F-08: a write connection is never crawled, so its live tools reach the catalogue only
    through the advert written when it is registered. An ingest connection gets none here (its
    plan proposal writes it)."""
    app, rt, _ = _mount(env)
    rt.ingest = _AdvertIngest(tools=[])
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/sources", json={"mode": "connection", "connection": {
            "id": "crm-write", "purpose": "agent_write",
            "transport": {"type": "stdio", "command": "npx"}}})
        assert r.status_code == 200, r.text
        assert r.json()["tool_advert"]["crm-write"]["written"] is True
        assert _cmd(c, "crm-read").status_code == 200
    assert rt.ingest.adverts == ["crm-write"]


def test_ef3_a_failed_advert_is_reported_not_fatal(env):
    app, rt, _ = _mount(env)
    rt.ingest = _AdvertIngest(tools=[], fail=RuntimeError("server did not start"))
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/sources", json={"mode": "connection", "connection": {
            "id": "crm-write2", "purpose": "agent_write",
            "transport": {"type": "stdio", "command": "npx"}}})
    assert r.status_code == 200, r.text
    got = r.json()["tool_advert"]["crm-write2"]
    # R-028 #6 (ROUTES): reported with a reference, never the exception text
    assert got["written"] is False and "server did not start" not in got["error"]
    assert "reference ERR-" in got["error"]


class _RealApproveIngest(FakeIngest):
    """Plans are the PACKAGE's (file-backed, real `approve_plan` with its fail-closed tool rule);
    connections are real; no server is launched."""
    load_plan = IngestFacade.load_plan
    approve_plan = IngestFacade.approve_plan
    _save_plan = IngestFacade._save_plan


def _plan_with_tool(source_id, tool, cls, monkeypatch=None):
    """A proposed plan whose `tools_digest` is the digest of the live tool list the approval will
    see. No server is launched: `plans._live_tools` is patched to return that list (FXI3)."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import CrawlPlan, ListSpec, ReadSpec, StepSpec, save_plan

    tools = [ToolInfo(name=tool, description="", input_schema={"type": "object"})]
    if monkeypatch is not None:
        live = dict(getattr(plans._live_tools, "_int3_tools", {}))
        live[source_id] = tools

        def _fake_live(conn, client, _live=live):
            return list(_live[conn.id])
        _fake_live._int3_tools = live
        monkeypatch.setattr(plans, "_live_tools", _fake_live)
    save_plan(CrawlPlan(source_id=source_id, status="proposed",
                        steps=[StepSpec(id="walk", strategy="documents",
                                        list=ListSpec(tool=tool), read=ReadSpec(text_tool=tool))],
                        tool_classes={tool: cls}, tools_digest=plans.tools_digest(tools),
                        notes=bound_notes(source_id)))


def test_ef3_plan_approval_passes_the_principals_read_vouches_through(env, monkeypatch):
    """An unclassified tool passes only when the approving principal vouches for it BY NAME; a
    write can never be vouched. The vouch is recorded against the principal, not a body name."""
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        _cmd(c, "kg")
        _plan_with_tool("kg", "open_nodes", "unclassified:unknown (no annotation)", monkeypatch)
        r = approve(c, "kg")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "PLAN_NOT_APPROVABLE"
        assert "vouch_read_tools" in r.json()["refusal"]["message"]
        r = approve(c, "kg", vouch_read_tools=["open_nodes"], approved_by="mallory")
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        assert plan["status"] == "approved" and plan["approved_by"] == "analyst-7"
        assert plan["tool_classes"]["open_nodes"] == "read:vouched_by:analyst-7"

        _cmd(c, "wr")
        _plan_with_tool("wr", "write_file", "write:name", monkeypatch)
        r = approve(c, "wr", vouch_read_tools=["write_file"])
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "PLAN_NOT_APPROVABLE"
        assert rt.ingest.load_plan("wr").status == "proposed"


# ------------------------------------------------------------------------------------------
# INT3: the gateway hands mcp_ingest the principal OBJECT on every approval
# ------------------------------------------------------------------------------------------
def test_int3_the_facade_never_approves_with_a_bare_id_string(env):
    """mcp_ingest refuses a string approver; the facade forwards whatever it is given, so a
    string reaching it must be refused by the package — not silently accepted."""
    from databridge_mcp_ingest.mapping import MappingApprovalRefused
    from databridge_mcp_ingest.plans import ApprovalRefused

    from databridge_gateway.platform_runtime import IngestFacade

    facade = IngestFacade()
    with pytest.raises(ApprovalRefused):
        facade.approve_plan("nothing", principal="analyst-7")
    with pytest.raises(MappingApprovalRefused):
        facade.approve_mapping("nothing", principal="analyst-7")


def test_int3_mapping_approval_is_recorded_against_the_authenticated_principal(env):
    """The mapping route passes the principal object: the package's approval record names the
    signed-in principal and its authentication mechanism, whatever the body claims."""
    from databridge_mcp_ingest.mapping import (SourceMapping, TableMapping, approval_log_path,
                                               load_mapping, save_mapping)

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        _cmd(c, "crm")
        save_mapping(SourceMapping(source_id="crm", tables={"orgs": TableMapping(
            status="proposed", node={"kind": "organisation", "label": "{name}",
                                             "sources": ["name"]},
            columns=["name"])}))                     # R-022 f2 #9: the schema it names
        r = c.post("/sources/crm/mapping/approve", json={"approved_by": "mallory"})
        assert r.status_code == 200, r.text
    tm = load_mapping("crm").tables["orgs"]
    assert tm.status == "approved"
    assert tm.approval["principal_id"] == "analyst-7"
    assert tm.approval["authenticated_by"]                          # an authentication, not a name
    lines = approval_log_path("crm").read_text(encoding="utf-8").splitlines()
    assert lines and all(json.loads(x)["principal_id"] == "analyst-7" for x in lines)


def test_int3_the_override_route_approves_and_revokes_for_the_principal_only(env):
    from databridge_mcp_ingest.screen import content_digest, load_overrides

    app, rt, _ = _mount(env)
    digest = content_digest(b"the reviewed bytes")
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        _cmd(c, "docs")
        body = {"item_id": "item-1", "digest": digest, "reason": "owner reviewed it",
                "principal_id": "mallory"}
        r = c.post("/sources/docs/overrides", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["override"]["principal_id"] == "analyst-7"
        got = load_overrides("docs")
        assert got.allows("item-1", digest) and not got.allows("item-1", "0" * 64)
        # a malformed digest is refused by the package, never widened by the route
        bad = c.post("/sources/docs/overrides", json={**body, "digest": "abc"})
        assert bad.status_code == 409 and bad.json()["refusal"]["code"] == "OVERRIDE_REFUSED"
        r = c.post("/sources/docs/overrides", json={**body, "action": "revoke"})
        assert r.status_code == 200, r.text
        assert not load_overrides("docs").allows("item-1", digest)
    with TestClient(app) as anon:
        assert anon.post("/sources/docs/overrides", json=body).status_code == 401
