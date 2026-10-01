"""S4 integration — REAL MCP servers that no Walnut profile knows, direct AND through MCPJungle.

    @modelcontextprotocol/server-everything   lists resources -> generic resources plan, NO model
    @modelcontextprotocol/server-memory       tools only      -> model-shaped plan (a FakeBackend
                                              draft — never a live model), validated + probed live

Each server is ingested into a temp store once direct (stdio via npx on this machine) and once
through the MCPJungle container (registered by this test under an `s4t_` name and deregistered
afterwards), and the two runs must mint the same item ids and capture digests.

Needs: `npx` on PATH (network on first run to fetch the packages); for the MCPJungle half, the
shared stack up (`MCPJUNGLE_URL`, default http://127.0.0.1:8080) and `docker` on PATH (the memory
file is copied into the container). Each half skips, with the reason, when its dependency is
missing. Run: `python -m pytest packages/mcp_ingest -m integration -k s4`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import client as cl
from databridge_mcp_ingest import jungle as jg
from databridge_mcp_ingest.connections import stdio_connection, with_route
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.plans import DraftStep, PlanDraft

pytestmark = pytest.mark.integration

NPX = shutil.which("npx")
# PINNED: an unpinned `npx -y pkg` reuses whatever version this machine has cached while the
# container fetches the latest — observed 2026-10-01: server-memory listed a resource through
# MCPJungle (2026.8.31) and none direct (an older cached copy). Same version on both routes, or
# the two routes are not comparable.
EVERYTHING = "@modelcontextprotocol/server-everything@2026.8.31"
#: server-memory 2026.1.26 has TOOLS ONLY (model-shaped plan); 2026.8.31 also lists the
#: `memory://knowledge-graph` resource (generic resources plan, no model). Both are exercised.
MEMORY_VERSIONS = ["2026.1.26", "2026.8.31"]
CONTAINER = "walnut_mcpjungle"
CONTAINER_MEMORY_FILE = "/tmp/s4t_memory.jsonl"

#: Synthetic knowledge graph for server-memory (all names invented).
MEMORY_LINES = [
    {"type": "entity", "name": "Aldermoor Logistics", "entityType": "organisation",
     "observations": ["Pilots the route planner with Quillon Freight"]},
    {"type": "entity", "name": "Priya Venkataraman", "entityType": "person",
     "observations": ["Leads planning at Aldermoor Logistics"]},
    {"type": "entity", "name": "Quillon Freight", "entityType": "organisation",
     "observations": ["Asked for weekly depot summaries"]},
    {"type": "relation", "from": "Priya Venkataraman", "to": "Aldermoor Logistics",
     "relationType": "works_at"},
]
ENTITY_NAMES = sorted(x["name"] for x in MEMORY_LINES if x["type"] == "entity")

MEMORY_DRAFT = PlanDraft(steps=[DraftStep(
    strategy="documents", list_tool="read_graph", list_parse_mode="json", items_path="entities",
    name_field="name", type_field="entityType", read_tool="open_nodes",
    read_args={"names": ["{name}"]}, item_mime="application/json",
    rationale="read_graph lists every entity; open_nodes reads one by name")])


def _need_npx():
    if not NPX:
        pytest.skip("npx is not on PATH")


def _jungle_registry():
    reg = jg.registry()
    health = reg.health()
    if not health.get("ok"):
        pytest.skip(f"MCPJungle is not up: {health.get('error')}")
    return reg


def _plan_harness():
    from databridge_harness import FakeBackend, Harness

    return Harness(FakeBackend(MEMORY_DRAFT.model_dump_json()), profile="cloud")


def _point_env(monkeypatch, base: Path):
    from databridge_graph.store import GraphStore

    for k, sub in {"WALNUT_SOURCES_DIR": "sources", "WALNUT_INGEST_DIR": "ingest",
                   "WALNUT_CAPTURE_DIR": "capture"}.items():
        monkeypatch.setenv(k, str(base / sub))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(base / "events" / "events.jsonl"))
    db = base / "graph" / "g.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(db))
    return GraphStore(str(db))


def _ingest(monkeypatch, base: Path, conn, *, harness_for_plan=None, extract_harness=None):
    store = _point_env(monkeypatch, base)
    mi.register_connection(conn)
    plan = mi.propose_plan(conn.id, harness=harness_for_plan)
    assert not plan.problems, plan.problems
    assert plan.status == "approved"
    report = mi.run_ingest(conn.id, store=store, harness=extract_harness)
    run_dir = Path(report.run_dir)
    return plan, report, {n: read_jsonl(run_dir / f"{n}.jsonl")
                          for n in ("inventory", "captured", "excluded", "failed")}


# --- server-everything: resources-first, no model -----------------------------------------------
def _everything_conn(cid="s4t_everything"):
    return stdio_connection(cid, NPX, ["-y", EVERYTHING], auto_approve=True)


def test_everything_server_direct_then_through_mcpjungle(tmp_path, monkeypatch, common_vocab,
                                                         fake_harness):
    _need_npx()
    conn = _everything_conn()
    with cl.open_client(conn) as c:                       # the expectation, derived live
        listed = sorted(r.uri for r in c.list_resources())
    assert listed, "server-everything listed no resources"

    plan, report, led = _ingest(monkeypatch, tmp_path / "direct", conn,
                                extract_harness=fake_harness)
    assert plan.origin == "rules" and plan.profiles == ["generic-resources"]
    assert report.status == "ok", report.message
    assert sorted(r["uri"] for r in led["captured"]) == listed
    assert not led["failed"]

    reg = _jungle_registry()
    name = conn.id
    try:
        reg.register(jg.body_from_connection(stdio_connection(name, "npx", ["-y", EVERYTHING])),
                     on_conflict="replace")
        via = with_route(conn, "mcpjungle")
        plan_j, report_j, led_j = _ingest(monkeypatch, tmp_path / "jungle", via,
                                          extract_harness=fake_harness)
        assert plan_j.profiles == plan.profiles and report_j.status == "ok", report_j.message
        key = lambda rows: sorted((r["item_id"], r["sha256"]) for r in rows)  # noqa: E731
        assert key(led_j["captured"]) == key(led["captured"])
    finally:
        reg.deregister(name)
        cl.close_pool()


# --- server-memory: tools only, model-shaped plan, validated live --------------------------------
@pytest.mark.parametrize("version", MEMORY_VERSIONS)
def test_memory_server_direct_then_through_mcpjungle(tmp_path, monkeypatch, common_vocab,
                                                     fake_harness, version):
    _need_npx()
    pkg = f"@modelcontextprotocol/server-memory@{version}"
    mem = tmp_path / "memory.jsonl"
    mem.write_text("\n".join(json.dumps(x) for x in MEMORY_LINES) + "\n", encoding="utf-8")
    conn = stdio_connection("s4t_memory", NPX, ["-y", pkg],
                            env={"MEMORY_FILE_PATH": str(mem)}, auto_approve=True)
    with cl.open_client(conn) as c:                       # the expectation, derived live
        resources = c.list_resources()
    plan, report, led = _ingest(monkeypatch, tmp_path / "direct", conn,
                                harness_for_plan=_plan_harness(), extract_harness=fake_harness)
    # The sample read is made only through a tool CLASSIFIED as a read (FXI3, 2026-10-01: an
    # unannotated tool is not a read until a person vouches for it by name). Derived from the
    # plan's own classes for the step's read tools, so either outcome is asserted, never skipped.
    step = plan.steps[0]
    read_tools = [t for t in ((step.read.text_tool, step.read.binary_tool) if step.read else ())
                  if t]
    sample = plan.probe["steps"][0]["read"]
    # A class `read:vouched_by:<person>` was given at APPROVAL; the probe ran at proposal, when
    # the tool was still unclassified.
    classes = [str(plan.tool_classes.get(t, "")) for t in read_tools]
    assert sample                                                      # non-vacuous
    # (no read TOOL: a resources step reads through resources/read, which needs no class)
    if all(c.startswith("read") and not c.startswith("read:vouched_by:") for c in classes):
        assert sample["bytes"] > 0                                    # a real sample read
    else:
        assert "not classified as reads" in sample["skipped"], sample
    assert report.status in ("ok", "empty"), report.message
    assert not led["failed"]
    if resources:                                        # rules first: no model was needed
        from databridge_mcp_ingest import capture

        assert plan.origin == "rules" and plan.profiles == ["generic-resources"]
        assert sorted(r["uri"] for r in led["captured"]) == sorted(r.uri for r in resources)
        text = capture.get_bytes(led["captured"][0]["sha256"]).decode("utf-8")
        assert all(n in text for n in ENTITY_NAMES)       # the whole graph, as one JSON item
    else:                                                # tools only: model-shaped, validated
        assert plan.origin == "model"
        assert sorted(r["name"] for r in led["inventory"]) == ENTITY_NAMES
        assert len(led["captured"]) + len(led["excluded"]) == len(ENTITY_NAMES)
    assert mem.read_text(encoding="utf-8").count("\n") == len(MEMORY_LINES)  # nothing written

    reg = _jungle_registry()
    if not shutil.which("docker"):
        pytest.skip("docker is not on PATH (needed to place the memory file in the container)")
    subprocess.run(["docker", "cp", str(mem), f"{CONTAINER}:{CONTAINER_MEMORY_FILE}"],
                   check=True, capture_output=True)
    name = conn.id
    try:
        body = jg.body_from_connection(stdio_connection(
            name, "npx", ["-y", pkg], env={"MEMORY_FILE_PATH": CONTAINER_MEMORY_FILE}))
        reg.register(body, on_conflict="replace")
        via = with_route(conn, "mcpjungle")
        plan_j, report_j, led_j = _ingest(monkeypatch, tmp_path / "jungle", via,
                                          harness_for_plan=_plan_harness(),
                                          extract_harness=fake_harness)
        assert plan_j.origin == plan.origin and plan_j.profiles == plan.profiles
        assert report_j.status == report.status
        ids = lambda rows: sorted(r["item_id"] for r in rows)         # noqa: E731
        assert ids(led_j["inventory"]) == ids(led["inventory"])
    finally:
        reg.deregister(name)
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", CONTAINER_MEMORY_FILE],
                       capture_output=True)
        cl.close_pool()
