"""Codex round 2 on the workflow contract (wiki/40-reviews/R-021): each REAL finding, red before the
fix, green after. Expectations are derived from the code the contract must agree with (the trigger
engine's cron, the connection manifest), never written down."""
from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from databridge_workflow_spec import ActionSpec, SpecError, load_spec

from test_workflow_spec import ADAPTER, READ_ONLY, READ_WRITE

REPO = Path(__file__).resolve().parents[3]


def _mut(base, **changes):
    d = copy.deepcopy(base)
    d.update(changes)
    return d


# -- 1 (Critical): outputs.save_to is a governed write, never a raw path ---------------------------
@pytest.mark.parametrize("save_to", ["../../some-file", "/Out/x.md", "C:\\x.md", "reports/x.md"])
def test_save_to_is_never_a_raw_path(save_to):
    """`mode: read_write, actions: [], outputs.save_to: ../../some-file` used to validate: a write
    with no declared action and no automation_mode."""
    with pytest.raises(SpecError, match="save_to"):
        load_spec(_mut(READ_WRITE, actions=[], outputs={"save_to": save_to}))
    with pytest.raises(SpecError, match="save_to"):
        load_spec(_mut(READ_WRITE, outputs={"save_to": save_to}))


def test_save_to_names_a_declared_action_and_inherits_its_approval_mode():
    name = READ_WRITE["actions"][0]["name"]
    spec = load_spec(_mut(READ_WRITE, outputs={"format": "cited_markdown", "save_to": name}))
    act = spec.save_action()
    assert act is not None and act.name == name
    assert act.automation_mode == READ_WRITE["actions"][0]["automation_mode"]
    assert load_spec(READ_WRITE).save_action() is None
    with pytest.raises(SpecError, match="save_to"):
        load_spec(_mut(READ_WRITE, outputs={"save_to": "not_declared_here"}))


# -- 2 (Critical): immutability survives base-class calls; derivation values are JSON -------------
def test_base_class_mutators_cannot_reach_a_validated_spec():
    ro, ad = load_spec(READ_ONLY), load_spec(ADAPTER)
    ev = load_spec(_mut(READ_ONLY, trigger={"type": "event", "event": {
        "source": "larkspur-files", "on": ["item_added"], "filter": {"uri_glob": "**/x/**"}}}))
    write = ActionSpec(name="w", tool="source.write:larkspur-files:write_file",
                       automation_mode="human_required")
    attempts = [lambda: list.append(ro.actions, write),          # codex's exact bypass
                lambda: list.extend(ro.tools, ["source.query:x"]),
                lambda: list.__setitem__(ro.sources, 0, "elsewhere"),
                lambda: dict.__setitem__(ad.plans, "larkspur-files", "approved"),
                lambda: dict.update(ad.plans, {"x": "approved"}),
                lambda: dict.__setitem__(ev.trigger.event.filter, "uri_glob", "**"),
                lambda: list.append(ev.trigger.event.on, "item_removed"),
                lambda: setattr(ad.plans, "_m", {})]
    for attempt in attempts:
        with pytest.raises((TypeError, AttributeError)):
            attempt()
    assert ro.actions == [] and ad.plans == ADAPTER["plans"]      # still compare with plain ones


@pytest.mark.parametrize("value", [{1, 2}, bytearray(b"x"), object(), b"bytes"])
def test_a_derivation_value_is_a_closed_json_value(value):
    row = {"field": "title", "produced_by": "user", "evidence": "q1", "value": value}
    with pytest.raises(SpecError):
        load_spec(_mut(READ_ONLY, derivations=[row]))


def test_nested_derivation_values_are_frozen_all_the_way_down():
    row = {"field": "x", "produced_by": "user", "evidence": "q1",
           "value": {"a": [1, {"b": [2]}]}}
    d = load_spec(_mut(READ_ONLY, derivations=[row])).derivations[0]
    with pytest.raises((TypeError, AttributeError)):
        list.append(d.value["a"][1]["b"], 3)
    with pytest.raises((TypeError, AttributeError)):
        dict.__setitem__(d.value, "c", 1)
    assert d.model_dump(mode="json")["value"] == row["value"]


# -- 3 (High): identifiers carry no newline, control character or path syntax ---------------------
@pytest.mark.parametrize("raw,needle", [
    (_mut(READ_ONLY, agent="account_briefing\n"), "agent"),
    (_mut(READ_ONLY, tools=["source.read:larkspur-files\n"]), "tool"),
    (_mut(READ_ONLY, tools=["graph.search\n"]), "tool"),
    (_mut(READ_ONLY, sources=["../../writer"], tools=[]), "sources"),
    (_mut(READ_ONLY, sources=["db;DROP TABLE x"], tools=[]), "sources"),
    (_mut(READ_ONLY, sources=["larkspur-files\n"], tools=[]), "sources"),
    (_mut(READ_WRITE, actions=[{"name": "w", "tool": "source.write:larkspur-files:../../execute_sql",
                                "automation_mode": "human_required"}]), "tool"),
    (_mut(READ_WRITE, actions=[{"name": "w", "tool": "source.write:larkspur-files:a/b",
                                "automation_mode": "human_required"}]), "tool"),
    (_mut(READ_WRITE, actions=[{"name": "w", "tool": "source.write:larkspur-files:a\\b",
                                "automation_mode": "human_required"}]), "tool"),
    (_mut(READ_WRITE, actions=[{"name": "w\n", "tool": "source.write:larkspur-files:write_file",
                                "automation_mode": "human_required"}]), "lower_snake_case"),
    (_mut(ADAPTER, connections=["../../writer"], plans={}), "connections"),
    (_mut(ADAPTER, connections=["db;DROP TABLE x"], plans={}), "connections"),
    (_mut(ADAPTER, id="larkspur_workspace_sources\n"), "adapter agent id"),
])
def test_identifiers_refuse_path_and_control_smuggling(raw, needle):
    with pytest.raises(SpecError, match=needle):
        load_spec(raw)


def test_mcp_tool_names_keep_the_mcp_alphabet():
    """Dots and hyphens are MCP tool-name characters; `..` and separators are not."""
    from databridge_workflow_spec import parse_tool
    assert parse_tool("source.read:larkspur-files:files.read-text_v2")["tool"] == \
        "files.read-text_v2"
    for bad in ("source.read:larkspur-files:..", "source.read:larkspur-files:a..b",
                "source.read:larkspur-files:a\tb", "source.read:larkspur-files:"):
        with pytest.raises(SpecError):
            parse_tool(bad)


# -- 4 (High): provenance fails closed; unknown stays unknown --------------------------------------
@pytest.mark.parametrize("by", ["typo", "", "guess", "rules-ish"])
def test_an_unrecognised_by_is_unknown_never_rules(by):
    row = {"field": "title", "by": by, "evidence": "x"}
    d = load_spec(_mut(READ_ONLY, derivations=[row])).derivations[0]
    assert d.produced_by == "unknown" and d.basis == by


@pytest.mark.parametrize("missing", ["model", "task_class", "prompt_version"])
def test_a_model_derivation_names_its_model_task_class_and_prompt(missing):
    row = {"field": "title", "produced_by": "model", "model": "fake/planner",
           "task_class": "compile", "prompt_version": "compiler/1"}
    assert load_spec(_mut(READ_ONLY, derivations=[row]))
    row[missing] = None
    with pytest.raises(SpecError, match=missing):
        load_spec(_mut(READ_ONLY, derivations=[row]))


@pytest.mark.parametrize("span", [[-1, 3], [0, 10_000], [2, 1], [0], [0.5, 2]])
def test_a_span_points_inside_the_users_words(span):
    row = {"field": "title", "produced_by": "rules", "span": span}
    with pytest.raises(SpecError):
        load_spec(_mut(READ_ONLY, derivations=[row]))
    n = len(READ_ONLY["created_from"])
    assert load_spec(_mut(READ_ONLY, derivations=[{**row, "span": [0, n]}]))


def test_a_rules_or_user_derivation_carries_its_basis():
    with pytest.raises(SpecError, match="basis"):
        load_spec(_mut(READ_ONLY, derivations=[{"field": "title", "produced_by": "rules"}]))
    assert load_spec(_mut(READ_ONLY, derivations=[{"field": "title", "produced_by": "unknown"}]))


def test_an_underived_field_reads_as_unknown_provenance():
    spec = load_spec(_mut(READ_ONLY, derivations=[
        {"field": "title", "produced_by": "user", "evidence": "q1 = Account briefing"}]))
    assert spec.provenance_of("title").produced_by == "user"
    assert spec.provenance_of("instructions").produced_by == "unknown"
    assert load_spec(ADAPTER).provenance_of("sync.type").produced_by == "unknown"


# -- 5 (High): a screen block the runner ignores is refused, not silently accepted ------------------
def test_an_adapter_screen_block_is_a_pack_reference_never_rules():
    pack = ADAPTER["extract"]["pack"]
    with pytest.raises(SpecError, match="screen"):
        load_spec(_mut(ADAPTER, screen={"exclude": "salary"}))
    with pytest.raises(SpecError, match="screen"):
        load_spec(_mut(ADAPTER, screen={"pack": pack, "exclude": "salary"}))
    with pytest.raises(SpecError, match="screen.pack"):
        load_spec(_mut(ADAPTER, screen={"pack": pack + "_other"}))
    assert load_spec(_mut(ADAPTER, screen={})).screen == {}
    assert load_spec(_mut(ADAPTER, screen={"pack": pack})).screen == {"pack": pack}


# -- 6 (High): the plans map is a mirror, never an approval ----------------------------------------
def test_the_plans_field_is_declared_a_non_authoritative_mirror():
    from databridge_workflow_spec import AdapterAgentSpec
    info = AdapterAgentSpec.model_fields["plans"]
    assert (info.json_schema_extra or {}).get("authoritative") is False
    assert "never" in (info.description or "") and "approval" in (info.description or "")


def test_a_yaml_approved_plan_does_not_make_a_connection_runnable(tmp_path):
    """The consumer that gates runs must need the on-disk, principal-attributed approval too."""
    for p in ("adapter_agents", "mcp_ingest"):
        path = str(REPO / "packages" / p)
        if path not in sys.path:
            sys.path.append(path)
    manager = pytest.importorskip("databridge_adapter_agents.manager")
    spec = load_spec(ADAPTER)
    assert set(spec.plans.values()) == {"approved"}

    class _NoOnDiskApproval:
        def plan_status(self, connection):
            return None

    m = manager.AdapterAgentManager.__new__(manager.AdapterAgentManager)
    m.service = _NoOnDiskApproval()
    # A real manager always has a registry; with none, the R-027 setup gate fails closed and adds
    # its own reason. Give this bare manager an empty registry so only the plan gate is measured.
    from types import SimpleNamespace
    m.registry = SimpleNamespace(root=tmp_path)
    reasons = m.agent_gate(spec)
    assert len(reasons) == len(spec.connections) and all("approve" in r for r in reasons)


# -- 7 (Medium): one canonical digest, insensitive to mapping order --------------------------------
def test_spec_digest_ignores_mapping_order_but_not_meaning():
    from databridge_workflow_spec import canonical_json, spec_digest
    ev = {"type": "event", "event": {"source": "larkspur-files", "on": ["item_added"],
                                      "filter": {"uri_glob": "**/x/**", "kind": "doc"}}}
    ev_rev = copy.deepcopy(ev)
    ev_rev["event"]["filter"] = dict(reversed(list(ev["event"]["filter"].items())))
    a, b = load_spec(_mut(READ_ONLY, trigger=ev)), load_spec(_mut(READ_ONLY, trigger=ev_rev))
    assert list(a.trigger.event.filter) != list(b.trigger.event.filter)
    assert canonical_json(a) == canonical_json(b) and spec_digest(a) == spec_digest(b)
    assert spec_digest(a).startswith("sha256:")
    c = load_spec(_mut(READ_ONLY, title="Another title"))
    assert spec_digest(c) != spec_digest(load_spec(READ_ONLY))
    assert spec_digest(load_spec(ADAPTER)) == spec_digest(load_spec(
        _mut(ADAPTER, plans=dict(reversed(list(ADAPTER["plans"].items()))))))


# -- 8 (Medium): a cron that can never fire is refused — derived from the trigger engine ------------
def _engine():
    p = str(REPO / "packages" / "automation")
    if p not in sys.path:
        sys.path.append(p)
    return pytest.importorskip("databridge_automation.cron")


_CORPUS = ["0 0 31 2 *", "0 0 30 2 *", "0 0 31 4,6,9,11 *", "0 0 29 2 *", "0 0 31 * *",
           "0 0 31 2 mon", "0 0 30,31 feb *", "0 0 31 2,3 *", "0 9 * * 1-5", "0 0 31 2 */1",
           "0 0 31 2 */2", "0 0 1-31/31 2 *"]


@pytest.mark.parametrize("expr", _CORPUS)
def test_spec_accepts_a_cron_exactly_when_the_engine_can_fire_it(expr):
    cron = _engine()
    sched = cron.CronSchedule.parse(expr, "UTC")
    engine_fires = sched.next_fire(datetime(2026, 1, 1, tzinfo=timezone.utc)) is not None
    raw = _mut(READ_WRITE, trigger={"type": "schedule", "schedule": {"cron": expr}})
    if engine_fires:
        assert load_spec(raw)
    else:
        with pytest.raises(SpecError, match="never"):
            load_spec(raw)


def test_the_cron_corpus_has_both_outcomes():
    cron = _engine()
    fires = {cron.CronSchedule.parse(e, "UTC").next_fire(
        datetime(2026, 1, 1, tzinfo=timezone.utc)) is not None for e in _CORPUS}
    assert fires == {True, False}


# -- F-28: one connection id, taken from the manifest ----------------------------------------------
def test_every_fixture_connection_is_declared_in_the_connection_manifest():
    manifest = yaml.safe_load((REPO / "infra" / "mcp_servers.yaml")
                              .read_text(encoding="utf-8"))
    declared = {s["id"] for s in manifest["servers"]}
    used = set(READ_ONLY["sources"]) | set(READ_WRITE["sources"]) | set(ADAPTER["connections"])
    assert used and used <= declared, sorted(used - declared)
