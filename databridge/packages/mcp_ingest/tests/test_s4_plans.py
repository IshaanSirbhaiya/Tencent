"""S4 — plans for servers NO profile knows: resources-first without a model, model second (a
FakeBackend, never a live model), and hardened validation incl. a live sample call.

The "server" here is an in-memory fake with the tool list and output shapes of
`@modelcontextprotocol/server-memory` (read live on 2026-10-01: read_graph -> {"entities": [...],
"relations": [...]}, open_nodes(names: string[]), write tools without annotations). Expectations
are derived from the fake's own data.
"""
from __future__ import annotations

import json

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest.client import ContentPart, ResourceInfo, ToolInfo, ToolResult
from databridge_mcp_ingest.plans import (DraftStep, PlanDraft, PlanError, StepSpec, is_write_tool,
                                         load_profiles, match_profiles, plan_from_tools,
                                         validate_steps)

ENTITIES = [{"name": "Aldermoor Logistics", "entityType": "organisation",
             "observations": ["Pilots the route planner with Quillon Freight"]},
            {"name": "Priya Venkataraman", "entityType": "person",
             "observations": ["Leads planning at Aldermoor Logistics"]},
            {"name": "Quillon Freight", "entityType": "organisation", "observations": []},
            {"name": "Tomasz Brennick", "entityType": "person", "observations": ["Sponsor"]}]

_ARR = {"type": "array", "items": {"type": "string"}}
MEMORY_TOOLS = [
    ToolInfo("create_entities", "", {"type": "object", "properties": {"entities": _ARR},
                                     "required": ["entities"]}),
    ToolInfo("add_observations", "", {"type": "object", "properties": {"observations": _ARR},
                                      "required": ["observations"]}),
    ToolInfo("delete_entities", "", {"type": "object", "properties": {"entityNames": _ARR},
                                     "required": ["entityNames"]}),
    ToolInfo("read_graph", "Read the entire knowledge graph", {"type": "object",
                                                                "properties": {}}),
    ToolInfo("search_nodes", "", {"type": "object", "properties": {"query": {"type": "string"}},
                                  "required": ["query"]}),
    ToolInfo("open_nodes", "Open specific nodes by name", {
        "type": "object", "properties": {"names": _ARR}, "required": ["names"]}),
]


class FakeMemory:
    """In-memory stand-in with server-memory's tools and output shapes."""

    supports_resources = True

    def __init__(self, entities=ENTITIES, *, extra_listing: dict | None = None,
                 tools=MEMORY_TOOLS, resources=()) -> None:
        self.entities = list(entities)
        self.extra = extra_listing or {}
        self.tools = list(tools)
        self.resources = list(resources)
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return list(self.tools)

    def list_resources(self):
        return list(self.resources)

    def read_resource(self, uri):
        self.calls.append(("resources/read", {"uri": uri}))
        return (ContentPart(kind="text", text=f"# {uri}\nsynthetic body", mime="text/markdown",
                            uri=uri),)

    def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "read_graph":
            body = {"entities": self.entities, "relations": [], **self.extra}
        elif name == "open_nodes":
            if not isinstance(args.get("names"), list):
                return ToolResult(parts=(ContentPart(kind="text", text="names must be an array"),),
                                  is_error=True)
            body = {"entities": [e for e in self.entities if e["name"] in args["names"]],
                    "relations": []}
        else:
            return ToolResult(parts=(ContentPart(kind="text", text="unknown tool"),),
                              is_error=True)
        return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(body, indent=2)),))

    def close(self):
        pass


def _harness(draft: PlanDraft, seen: list | None = None):
    from databridge_harness import FakeBackend, Harness

    def handler(*, model, messages, guided_json):
        if seen is not None:
            seen.append(messages)
        return draft.model_dump_json()

    backend = FakeBackend(handler)
    return Harness(backend, profile="cloud"), backend


def _draft(**over) -> PlanDraft:
    step = dict(strategy="documents", list_tool="read_graph", list_parse_mode="json",
                items_path="entities", name_field="name", type_field="entityType",
                read_tool="open_nodes", read_args={"names": ["{name}"]},
                item_mime="application/json", rationale="read the graph, open each node")
    step.update(over)
    return PlanDraft(steps=[DraftStep(**step)])


def _register(cid="mem", **scope):
    mi.register_connection({"id": cid, "transport": {"type": "stdio", "command": "npx",
                                                     "args": ["-y", "memory"]},
                            "scope": scope, "auto_approve": True})


# --- write tools ---------------------------------------------------------------------------------
def test_write_tools_are_recognised_without_annotations():
    writes = {t.name for t in MEMORY_TOOLS if is_write_tool(t)}
    reads = {t.name for t in MEMORY_TOOLS} - writes
    assert writes == {"create_entities", "add_observations", "delete_entities"}
    assert reads == {"read_graph", "search_nodes", "open_nodes"}
    # an annotation that admits writing wins over an innocent name
    assert is_write_tool(ToolInfo("fetch", read_only=False))
    assert is_write_tool(ToolInfo("fetch", destructive=True))


# --- model second -------------------------------------------------------------------------------
def test_model_plan_for_an_unknown_server_is_validated_and_probed(walnut_env):
    _register()
    seen: list = []
    harness, backend = _harness(_draft(), seen)
    fake = FakeMemory()
    plan = mi.propose_plan("mem", harness=harness, client=fake)
    assert backend.call_count == 1 and plan.origin == "model"
    assert not plan.problems, plan.problems
    assert plan.status == "approved"                                  # auto_approve, no problems
    # the model was never shown a write tool
    catalogue = json.loads(seen[0][-1]["content"])["tools"]
    assert {t["name"] for t in catalogue} == {t.name for t in MEMORY_TOOLS if not is_write_tool(t)}
    # the probe listed and read through the session, read-only
    (srec,) = plan.probe["steps"]
    assert srec["items"] == min(3, len(ENTITIES))
    # FXI3 (Codex f1 #2): `open_nodes` is classified by nothing (no read verb; annotations are
    # claims), so the probe never CALLS it before a person vouches for it — the listing is probed,
    # the sample read is not, and the plan says so. The approver's vouch is then recorded.
    unclassified = sorted(t for t, c in plan.tool_classes.items()
                          if c.startswith("read:vouched_by:"))
    assert unclassified, plan.tool_classes
    assert "skipped" in srec["read"]
    assert any("no sample READ" in n for n in plan.notes)
    called = [n for n, _ in fake.calls]
    assert called == ["read_graph"] and not set(called) & set(unclassified)
    assert not any(is_write_tool(t) for t in MEMORY_TOOLS if t.name in called)


@pytest.mark.parametrize("over,expect", [
    ({"read_args": {"names": "{name}"}}, "must be an array"),
    ({"read_args": {"names": ["{key}"]}}, "never provides"),
    ({"read_args": {"names": ["{name}"], "depth": 2}}, "has no argument"),
    ({"read_args": {}}, "requires ['names']"),
    ({"read_tool": "add_observations", "read_args": {"observations": ["{name}"]}},
     "can change the source"),
    ({"read_tool": "open_everything"}, "not offered"),
])
def test_static_validation_catches_unrunnable_drafts(walnut_env, over, expect):
    _register()
    harness, _ = _harness(_draft(**over))
    fake = FakeMemory()
    plan = mi.propose_plan("mem", harness=harness, client=fake)
    assert any(expect in p for p in plan.problems), plan.problems
    assert plan.status == "proposed" and fake.calls == []            # never probed
    with pytest.raises(PlanError):
        mi.approve_plan("mem", approved_by="Test Owner")


def test_the_sample_call_catches_a_parser_that_finds_nothing(walnut_env):
    _register()
    harness, _ = _harness(_draft(items_path="nodes"))                # wrong path
    plan = mi.propose_plan("mem", harness=harness, client=FakeMemory())
    assert any("parser found no items" in p for p in plan.problems), plan.problems


def test_an_empty_source_is_a_note_not_a_problem(walnut_env):
    _register()
    harness, _ = _harness(_draft())
    plan = mi.propose_plan("mem", harness=harness, client=FakeMemory(entities=[]))
    # read_graph still returns {"entities": [], ...}: the items_path reached an EMPTY list
    assert plan.probe["steps"][0]["items"] == 0
    assert not plan.problems and any("no items" in n for n in plan.notes)


def test_pagination_is_detected_by_schema_and_by_sample(walnut_env):
    paged_tool = ToolInfo("read_graph", "", {"type": "object",
                                             "properties": {"cursor": {"type": "string"}}})
    tools = [paged_tool if t.name == "read_graph" else t for t in MEMORY_TOOLS]
    _register()
    harness, _ = _harness(_draft())
    plan = mi.propose_plan("mem", harness=harness, client=FakeMemory(tools=tools))
    assert "read_graph.cursor" in plan.pagination
    assert any("FIRST page only" in n for n in plan.notes)
    harness, _ = _harness(_draft())
    paged = FakeMemory(extra_listing={"nextCursor": "page-2"})
    plan = mi.propose_plan("mem", harness=harness, client=paged)
    assert any("next page" in p for p in plan.problems), plan.problems
    assert "read_graph:next-page" in plan.pagination


def test_the_probe_never_reads_a_blocked_item(walnut_env):
    _register(blocked=["*"])
    harness, _ = _harness(_draft())
    fake = FakeMemory()
    plan = mi.propose_plan("mem", harness=harness, client=fake)
    assert [n for n, _ in fake.calls] == ["read_graph"]
    assert any("out of the declared scope" in n for n in plan.notes)


# --- resources first, no model --------------------------------------------------------------------
RESOURCES = [ResourceInfo(uri=f"demo://resource/static/document/{n}", name=n, mime="text/markdown")
             for n in ("architecture.md", "features.md", "startup.md")]


def test_a_server_that_lists_resources_gets_a_generic_plan_without_a_model(walnut_env):
    _register("docs")
    harness, backend = _harness(_draft())
    fake = FakeMemory(tools=[ToolInfo("echo", "", {"type": "object"})], resources=RESOURCES)
    plan = mi.propose_plan("docs", harness=harness, client=fake)
    assert backend.call_count == 0                                    # rules first (P16)
    assert plan.origin == "rules" and plan.profiles == ["generic-resources"]
    assert not plan.problems and plan.status == "approved"
    assert plan.probe["steps"][0]["read"]["uri"] == RESOURCES[0].uri
    assert [s.strategy for s in plan.steps] == ["resources"]


def test_the_generic_profile_never_shadows_a_specific_one():
    profiles = load_profiles()
    generic = [p for p in profiles if p.fallback]
    assert generic, "no fallback profile is bundled"
    specific = [p for p in profiles if not p.fallback and p.match.get("all_tools")]
    assert specific
    for p in specific:
        tools = [ToolInfo(n) for n in p.match["all_tools"]] + \
                [ToolInfo(n) for n in (p.match.get("any_tools") or [])[:1]]
        got = match_profiles(tools, True, profiles)
        assert got and not any(g.fallback for g in got), (p.id, [g.id for g in got])


def test_profile_deny_tools_are_folded_into_the_plan_and_refused():
    profiles = [p for p in load_profiles() if p.deny_tools]
    assert profiles, "no profile declares deny_tools"
    prof = profiles[0]
    tools = [ToolInfo(n) for n in prof.match.get("all_tools") or []] + \
            [ToolInfo(n) for n in prof.deny_tools]
    conn = mi.SourceConnection.model_validate({"id": "x", "transport": {"type": "stdio",
                                                                        "command": "x"}})
    plan = plan_from_tools(conn, tools, has_resources=False, profiles=[prof])
    assert set(prof.deny_tools) <= set(plan.deny_tools)
    step = StepSpec.model_validate({"id": prof.id, **prof.step})
    bad = step.model_copy(update={"read": step.read.model_copy(
        update={"text_tool": prof.deny_tools[0], "text_args": {}})}) if step.read else None
    if bad is not None:
        problems = validate_steps([bad], tools, has_resources=False,
                                  deny_tools=prof.deny_tools)
        assert any("can change the source" in p for p in problems)


# --- the run: every entity is accounted for ----------------------------------------------------
def test_a_model_planned_run_accounts_for_every_listed_item(walnut_env, common_vocab,
                                                            fake_harness):
    from pathlib import Path

    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.ledger import read_jsonl

    _register()
    harness, _ = _harness(_draft())
    fake = FakeMemory()
    plan = mi.propose_plan("mem", harness=harness, client=fake)
    assert plan.status == "approved"
    db = walnut_env["WALNUT_GRAPH_DB_PATH"]
    db.parent.mkdir(parents=True, exist_ok=True)
    report = mi.run_ingest("mem", store=GraphStore(str(db)), harness=fake_harness, client=fake)
    run_dir = Path(report.run_dir)
    inventory = read_jsonl(run_dir / "inventory.jsonl")
    assert sorted(r["name"] for r in inventory) == sorted(e["name"] for e in ENTITIES)
    captured = read_jsonl(run_dir / "captured.jsonl")
    excluded = read_jsonl(run_dir / "excluded.jsonl")
    assert len(captured) + len(excluded) == len(ENTITIES)
    # items without an extension are captured once crawl honours list.item_mime; until then
    # each is RECORDED as no_extractor with its reversal — never silently dropped
    assert all(r["rule"].startswith("no_extractor") and r["reverse"] for r in excluded)
    assert all(not is_write_tool(ToolInfo(n)) for n, _ in fake.calls)
