"""WorkflowSpec (behaviour) ↔ agentdef declaration (governance): the one mapping, written once.

## Tools → capabilities

agentdef names a capability ``<source_id>.<capability>``, both lowercase snake_case, and screens it
against what an ADAPTER ADVERTISES. A workflow names tools in the §5.6 grammar. The mapping:

| workflow tool                         | capability                     | advertised by                 |
|---------------------------------------|--------------------------------|-------------------------------|
| ``graph.<op>``                        | ``graph.<op>``                 | this runtime's graph tools    |
| ``retrieve.<op>``                     | ``retrieve.<op>``              | this runtime's retriever      |
| ``source.query:<conn>``               | ``<conn_>.sql``                | the connection (it has SQL)   |
| ``source.read:<conn>:<tool>``         | ``<conn_>.tool_<tool_>``       | the connection's read tools   |
| ``source.read:<conn>``                | ``<conn_>.read_any``           | the connection (any read tool)|
| ``facts.query:<conn>``                | ``facts.<conn_>``              | this runtime, when the conn's |
|                                       |                                | read-only facts store exists  |
| ``delegate.ask:<adapter agent>``      | ``delegate.<aa_>``             | this runtime, when a federated|
|                                       |                                | service can answer for that   |
|                                       |                                | adapter agent (FED)           |
| ``source.write:<conn>:<tool>``        | — (never a capability)         | an ACTION, gated by its mode  |

``<conn_>`` / ``<tool_>`` are the ids folded to snake_case (``crm-db`` → ``crm_db``,
``readFile`` → ``readfile``). The ``tool_`` prefix keeps an MCP tool literally called ``sql`` or
``read_any`` from colliding with the two connection-wide capabilities; two ids that FOLD to the same
name are refused rather than merged, and the ids ``graph`` / ``retrieve`` are reserved.

The catalogue the compiler screens against is built from what the runtime and the source gateway
ADVERTISE (`build_catalogue`) — never from the workflow's own tool list, because "a capability
checked against names the declaring author also wrote is not checked at all" (agentdef/catalogue).

## Actions

Each workflow action becomes one declaration action, automation mode and reversal copied
verbatim. ``read_only`` ⇒ no actions (the spec already refuses them; the declaration mirrors it).
``touches_subject`` has no field in the WorkflowSpec and no safe default (agentdef finding 2), so:
a ``human_required`` action may default to ``true`` — the conservative answer, and it changes
nothing because the gate is already the strictest one — while a ``human_notified`` or
``autonomous`` action MUST be answered explicitly by the caller, or generation refuses.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

import yaml

from databridge_agentdef import AdapterAdvert, AdapterCatalogue, AgentDeclaration, parse_declaration
from databridge_agentdef.declaration import normalised_text, text_digest
from databridge_workflow_spec import SpecError, WorkflowSpec, dump_spec, load_spec, parse_tool

from . import paths

#: Source ids the runtime itself advertises. A connection may not take either name.
GRAPH_SOURCE = "graph"
RETRIEVE_SOURCE = "retrieve"
#: `facts.query:<conn>` — exact SQL over a connection's read-only facts store (S2, R-017).
FACTS_SOURCE = "facts"
#: `delegate.ask:<adapter agent>` — a read-only delegated sub-query (FED, databridge_federated).
DELEGATE_SOURCE = "delegate"
RESERVED_SOURCE_IDS = frozenset({GRAPH_SOURCE, RETRIEVE_SOURCE, FACTS_SOURCE, DELEGATE_SOURCE})

#: What the runtime's own tool families offer. Stated by the code that implements them.
GRAPH_OPS = frozenset({"search", "neighbors", "path", "node"})
RETRIEVE_OPS = frozenset({"search"})

SQL_CAPABILITY = "sql"
READ_ANY_CAPABILITY = "read_any"
TOOL_PREFIX = "tool_"

#: Where the runtime's own adverts say they come from (a grant's `resolved_through`).
RUNTIME_DECLARED_IN = "packages/agent_runtime/databridge_agent_runtime/tools.py"

_FOLD = re.compile(r"[^a-z0-9]+")


class MappingError(ValueError):
    """A workflow cannot be mapped onto a declaration. Plain words, for whoever wrote it."""


def fold(name: str) -> str:
    """``crm-db`` → ``crm_db``; ``readFile`` → ``readfile``. Must start with a letter."""
    out = _FOLD.sub("_", str(name).lower()).strip("_")
    if not out or not out[0].isalpha():
        out = f"x_{out}" if out else "x"
    return out


def source_id_for(connection: str) -> str:
    sid = fold(connection)
    if sid in RESERVED_SOURCE_IDS:
        raise MappingError(f"connection id {connection!r} folds to {sid!r}, which names one of the "
                           f"runtime's own tool families ({sorted(RESERVED_SOURCE_IDS)}); rename "
                           f"the connection")
    return sid


def tool_capability(tool_name: str) -> str:
    return TOOL_PREFIX + fold(tool_name)


def capability_for_tool(tool: str) -> Optional[str]:
    """The ``<source_id>.<capability>`` a workflow tool needs, or ``None`` for a write (an action)."""
    p = parse_tool(tool)
    if p["family"] in (GRAPH_SOURCE, RETRIEVE_SOURCE):
        return f"{p['family']}.{p['op']}"
    if p["family"] == FACTS_SOURCE:
        return f"{FACTS_SOURCE}.{fold(p['connection'] or '')}"
    if p["family"] == DELEGATE_SOURCE:
        return f"{DELEGATE_SOURCE}.{fold(p['connection'] or '')}"
    sid = source_id_for(p["connection"] or "")
    if p["verb"] == "write":
        return None
    if p["verb"] == "query":
        return f"{sid}.{SQL_CAPABILITY}"
    if p["tool"]:
        return f"{sid}.{tool_capability(p['tool'])}"
    return f"{sid}.{READ_ANY_CAPABILITY}"


# ------------------------------------------------------------------------------------------------
# what a connection advertises (the gateway says; the workflow never does)
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolAdvert:
    name: str
    writes: bool
    description: str = ""
    #: The tool's MCP ``inputSchema`` when the server published one. An action's arguments are
    #: checked against it when the action is PROPOSED (E2E F-02), so a call the tool cannot accept
    #: never reaches an approver.
    input_schema: Optional[dict] = field(default=None, compare=False, hash=False)
    #: Why it is read or write (`classify.BASES`); empty when built by hand.
    basis: str = field(default="", compare=False)


@dataclass(frozen=True)
class ConnectionAdvert:
    """One connection's tools as the SOURCE GATEWAY reports them (live `tools/list` + crawl plan)."""

    connection: str
    tools: tuple[ToolAdvert, ...] = ()
    sql: bool = False
    declared_in: str = ""
    reachable: bool = True
    problem: str = ""
    #: The MCP tool that executes SQL (from the crawl plan), when known. A ``source.read`` of THIS
    #: tool would send a statement past the read-only check, so the tool layer refuses it.
    sql_tool: Optional[str] = None

    def tool(self, name: str) -> Optional[ToolAdvert]:
        return next((t for t in self.tools if t.name == name), None)


def advert_for_connection(adv: ConnectionAdvert, *, pack: str) -> tuple[Optional[AdapterAdvert],
                                                                        list[str]]:
    """An agentdef `AdapterAdvert` for one connection, plus the problems found building it."""
    problems: list[str] = []
    if not adv.reachable:
        return None, [f"connection {adv.connection!r} could not be described: "
                      f"{adv.problem or 'unreachable'}"]
    try:
        sid = source_id_for(adv.connection)
    except MappingError as e:
        return None, [str(e)]
    reads: dict[str, str] = {}
    writes: dict[str, str] = {}
    for t in adv.tools:
        cap = tool_capability(t.name)
        bucket = writes if t.writes else reads
        other = reads if t.writes else writes
        if cap in bucket or cap in other:
            prior = bucket.get(cap) or other.get(cap)
            problems.append(f"connection {adv.connection!r}: tools {prior!r} and {t.name!r} both "
                            f"fold to capability {cap!r}; neither is grantable until one is renamed")
            bucket.pop(cap, None)
            other.pop(cap, None)
            continue
        bucket[cap] = t.name
    read_caps = set(reads)
    if reads:
        read_caps.add(READ_ANY_CAPABILITY)
    if adv.sql:
        read_caps.add(SQL_CAPABILITY)
    if not read_caps and not writes:
        return None, problems + [f"connection {adv.connection!r} advertises no tools"]
    return AdapterAdvert(
        source_id=sid, pack=pack, reads=frozenset(read_caps), writes=frozenset(writes),
        max_trust="untrusted",           # third-party MCP content is untrusted (connections.py)
        egress_class="INTERNAL", declared_in=adv.declared_in or f"var/sources/{adv.connection}.yaml",
        kind="mcp"), problems


def build_catalogue(*, pack: str, connections: Sequence[ConnectionAdvert],
                    graph: bool, retrieval: bool,
                    facts: Sequence[str] = (),
                    delegates: Sequence[str] = ()) -> tuple[AdapterCatalogue, list[str]]:
    """The catalogue `compile_agent` screens a declaration against, and the problems met.

    ``graph`` / ``retrieval`` say whether this runtime actually has a store / retriever to offer —
    a tool family with nothing behind it is not advertised.
    """
    adverts: dict[str, AdapterAdvert] = {}
    problems: list[str] = []
    if graph:
        adverts[GRAPH_SOURCE] = AdapterAdvert(
            source_id=GRAPH_SOURCE, pack=pack, reads=GRAPH_OPS, writes=frozenset(),
            max_trust="untrusted", egress_class="INTERNAL", declared_in=RUNTIME_DECLARED_IN,
            kind="graph")
    if retrieval:
        adverts[RETRIEVE_SOURCE] = AdapterAdvert(
            source_id=RETRIEVE_SOURCE, pack=pack, reads=RETRIEVE_OPS, writes=frozenset(),
            max_trust="untrusted", egress_class="INTERNAL", declared_in=RUNTIME_DECLARED_IN,
            kind="retrieval")
    if facts:                               # connections whose facts store exists right now
        adverts[FACTS_SOURCE] = AdapterAdvert(
            source_id=FACTS_SOURCE, pack=pack, reads=frozenset(fold(c) for c in facts),
            writes=frozenset(), max_trust="untrusted", egress_class="INTERNAL",
            declared_in=RUNTIME_DECLARED_IN, kind="facts")
    if delegates:                           # adapter agents a federated service answers for
        adverts[DELEGATE_SOURCE] = AdapterAdvert(
            source_id=DELEGATE_SOURCE, pack=pack, reads=frozenset(fold(d) for d in delegates),
            writes=frozenset(), max_trust="untrusted", egress_class="INTERNAL",
            declared_in=RUNTIME_DECLARED_IN, kind="delegate")
    for adv in connections:
        a, probs = advert_for_connection(adv, pack=pack)
        problems.extend(probs)
        if a is None:
            continue
        if a.source_id in adverts:
            problems.append(f"connection {adv.connection!r} folds to source id {a.source_id!r}, "
                            f"already taken; neither is advertised")
            adverts.pop(a.source_id, None)
            continue
        adverts[a.source_id] = a
    return AdapterCatalogue(adverts, (pack,)), problems


# ------------------------------------------------------------------------------------------------
# WorkflowSpec → declaration YAML
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Governance:
    """What a WorkflowSpec does not say and a declaration must: the reviewed, governance-only facts.

    None of these are guessed. ``purpose`` must be declared in ``packs/<pack>/purposes.yaml``;
    ``golden_set`` is a repo-relative JSONL path the compiler digests; the two daily ceilings have
    no defaults because an unbounded axis is the one that fails.
    """

    purpose: str
    golden_set: str
    escalations_per_day: int
    cost_ceiling_usd_per_day: float
    trust_ceiling: str = "untrusted"
    version: int = 1
    touches_subject: Mapping[str, bool] = field(default_factory=dict)
    description: str = ""


def declaration_from_workflow(spec: WorkflowSpec, pack: str, governance: Governance) -> str:
    """The agentdef declaration YAML for ``spec``. `compile_agent` decides whether it may run."""
    if fold(spec.agent) != spec.agent or "-" in spec.agent:
        raise MappingError(
            f"agent id {spec.agent!r} is not lowercase snake_case; an agentdef agent name becomes "
            f"part of a grant id and must be (e.g. {fold(spec.agent)!r}). §5.6 requires the two "
            f"ids to be equal, so rename the workflow")
    caps: list[str] = []
    for t in spec.tools:
        cap = capability_for_tool(t)
        if cap is not None and cap not in caps:
            caps.append(cap)
    actions: list[dict] = []
    if spec.mode == "read_write":
        for a in spec.actions:
            if a.name in governance.touches_subject:
                touches = bool(governance.touches_subject[a.name])
            elif a.automation_mode == "human_required":
                touches = True
            else:
                raise MappingError(
                    f"action {a.name!r} is {a.automation_mode}, so whether it decides, records or "
                    f"narrows WHICH SUBJECT something concerns must be answered explicitly "
                    f"(Governance.touches_subject[{a.name!r}] = true|false). There is no safe "
                    f"default on this axis (CLAUDE.md hard constraint 1)")
            body: dict[str, Any] = {
                "description": (a.description or f"{a.tool}").strip(),
                "touches_subject": touches,
                "automation": {"mode": a.automation_mode},
            }
            if a.reversal:
                body["reversal"] = a.reversal          # verbatim
            actions.append({a.name: body})
    decl: dict[str, Any] = {
        "agent": spec.agent,
        "version": governance.version,
        "pack": pack,
        "title": spec.title,
        "description": governance.description or f"Compiled from workflow {spec.agent!r}: "
                                                  f"{spec.created_from}",
        "purpose": governance.purpose,
        "capabilities": caps,
        "trust_ceiling": governance.trust_ceiling,
        "citation": "required_per_field",
        "actions": actions,
        "budget": {"tokens_per_run": spec.limits.tokens_per_run,
                   "escalations_per_day": governance.escalations_per_day,
                   "cost_ceiling_usd_per_day": governance.cost_ceiling_usd_per_day},
        "evals": {"golden_set": governance.golden_set, "gates": {"grounding": 1.0}},
    }
    header = (f"# agentdef declaration generated from workflow {spec.agent!r} "
              f"(databridge_agent_runtime.declaration_from_workflow).\n"
              f"# Review it as you would any pack artefact; compile_agent decides whether it runs.\n")
    return header + yaml.safe_dump(decl, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------------------------------------------
# loading an agent (both halves) and checking they describe the same agent
# ------------------------------------------------------------------------------------------------
def parse_declaration_text(text: str, *, declared_in: str) -> AgentDeclaration:
    """A declaration from YAML text, refusing duplicate keys exactly as `load_declaration` does.

    `load_declaration(path)` requires ``packs/<pack>/agents/<agent>.yaml``; §5.6 stores a user's
    agent as ``var/agents/<agent>.agent.yaml``, so the file rules differ and are checked by
    `load_agent` instead.
    """
    from databridge_agentdef._yaml_strict import strict_safe_load   # noqa: PLC0415 (see report)

    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    data = strict_safe_load(norm) or {}
    return parse_declaration(data, where=declared_in, declared_in=declared_in,
                             declaration_digest=text_digest(norm))


def load_declaration_file(path: Union[str, Path]) -> AgentDeclaration:
    p = Path(path)
    decl = parse_declaration_text(normalised_text(p), declared_in=_rel(p))
    stem = p.name[: -len(".agent.yaml")] if p.name.endswith(".agent.yaml") else p.stem
    if stem != decl.agent:
        raise MappingError(f"{p}: declares agent {decl.agent!r} but the file is named for "
                           f"{stem!r}")
    return decl


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(paths.DATABRIDGE_ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def load_agent(agent: str, agents_dir: Optional[Path] = None) -> tuple[WorkflowSpec,
                                                                       AgentDeclaration]:
    """``<agents_dir>/<agent>.workflow.yaml`` + ``<agent>.agent.yaml`` (§5.6)."""
    base = Path(agents_dir) if agents_dir is not None else paths.agents_dir()
    wf_path = base / f"{agent}.workflow.yaml"
    decl_path = base / f"{agent}.agent.yaml"
    if not wf_path.is_file():
        raise MappingError(f"no workflow for agent {agent!r} at {wf_path}")
    if not decl_path.is_file():
        raise MappingError(f"no declaration for agent {agent!r} at {decl_path}; an agent with no "
                           f"declaration has no grants and cannot run")
    spec = load_spec(wf_path)
    if not isinstance(spec, WorkflowSpec):
        raise MappingError(f"{wf_path} is not a workflow/v1 spec")
    return spec, load_declaration_file(decl_path)


def pair_problems(spec: WorkflowSpec, decl: AgentDeclaration) -> list[str]:
    """Where the two halves disagree. Any entry means the agent does not run."""
    out: list[str] = []
    if spec.agent != decl.agent:
        out.append(f"workflow is for agent {spec.agent!r} but the declaration is for {decl.agent!r}")
    if spec.mode == "read_only" and decl.actions:
        out.append(f"workflow is read_only but the declaration declares actions "
                   f"{[a.name for a in decl.actions]}; read-only means no write path at all")
    spec_actions = {a.name: a for a in spec.actions}
    decl_actions = {a.name: a for a in decl.actions}
    for name in sorted(set(spec_actions) - set(decl_actions)):
        out.append(f"workflow action {name!r} is not declared; it has no automation mode anyone "
                   f"reviewed")
    for name in sorted(set(decl_actions) - set(spec_actions)):
        out.append(f"declared action {name!r} has no tool in the workflow")
    for name in sorted(set(spec_actions) & set(decl_actions)):
        s, d = spec_actions[name], decl_actions[name]
        if s.automation_mode != d.automation_mode:
            out.append(f"action {name!r}: workflow says {s.automation_mode}, declaration says "
                       f"{d.automation_mode}; the declaration is the reviewed one, fix the workflow")
        if (s.reversal or None) != (d.reversal or None) and s.automation_mode == "autonomous":
            out.append(f"action {name!r}: workflow and declaration declare different reversals")
    held = set(decl.capabilities)
    needed: set[str] = set()
    for t in spec.tools:
        try:
            cap = capability_for_tool(t)
        except (SpecError, MappingError) as e:
            out.append(str(e))
            continue
        if cap is not None and cap not in held:
            out.append(f"workflow tool {t!r} needs capability {cap!r}, which the declaration does "
                       f"not grant")
        if cap is not None:
            needed.add(cap)
    # R-021 #10: a declaration may not hold MORE than the workflow uses. A surplus capability is
    # authority no behaviour needs (a delegate's live read could otherwise be admitted by a grant
    # for a tool the workflow never lists). The generator emits exactly the needed set.
    for cap in sorted(held - needed):
        out.append(f"the declaration grants {cap!r}, which no workflow tool uses; a grant with no "
                   f"behaviour behind it is refused (declare the tool, or drop the grant)")
    return out


def spec_digest(spec: WorkflowSpec, decl: AgentDeclaration) -> str:
    """One digest over BOTH halves: the workflow's content and the reviewed declaration text."""
    blob = json.dumps({"workflow": spec.model_dump(mode="json"),
                       "declaration_digest": decl.declaration_digest},
                      sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def workflow_text(spec: WorkflowSpec) -> str:
    return dump_spec(spec)


__all__ = [
    "GRAPH_SOURCE", "RETRIEVE_SOURCE", "FACTS_SOURCE", "DELEGATE_SOURCE", "GRAPH_OPS", "RETRIEVE_OPS", "SQL_CAPABILITY",
    "READ_ANY_CAPABILITY", "MappingError", "ToolAdvert", "ConnectionAdvert", "Governance",
    "fold", "source_id_for", "tool_capability", "capability_for_tool", "build_catalogue",
    "advert_for_connection", "declaration_from_workflow", "parse_declaration_text",
    "load_declaration_file", "load_agent", "pair_problems", "spec_digest", "workflow_text",
]
