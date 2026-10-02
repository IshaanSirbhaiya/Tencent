"""The **catalogue** — what the English compiler may refer to, and nothing else (P5: declare scope).

A sentence can only name things that exist: a live connection, one of its tools, a folder that
connection has been seen to hold, an ontology kind, an internal tool (`graph.*`, `retrieve.*`).
Anything the sentence names that the catalogue does not hold becomes a QUESTION, never a guess.

The catalogue is injectable. The gateway builds one from its live state (connections, tool lists
from `list_tools()`, the active vocabulary); tests build one in memory; `load_catalogue()` reads
the on-disk declarations `mcp_ingest` writes under `WALNUT_SOURCES_DIR` so the CLI works alone.

Every field is data. There is no per-source or per-domain branch here or in the compiler: a
source's type words ("database", "drive") come from `KIND_WORDS` + the connection's own
`aliases`, which a connection YAML may extend.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Sequence

import yaml

from databridge_agent_runtime.classify import (  # noqa: F401 - re-exported: ONE rule (E2E F-10)
    EXEC_VERB,
    READ_VERB,
    WRITE_VERB,
    WRITE_VERB_EXTRA,
    plan_denied_tools,
    plan_read_tools,
    writes_by_name,
)
from databridge_agent_runtime.classify import classify_tool as _shared_classify

from . import paths

# The read/write rule lives in `databridge_agent_runtime.classify` and is used by BOTH this compiler
# and the runtime's source gateway (E2E F-10: the compiler used to classify by name while the
# runtime called every unannotated tool a write, so postgres-mcp's `list_schemas` was granted here
# and refused there). The verb patterns are re-exported from there under their old names;
# `test_write_verb_agrees_with_ingest` still keeps them pattern-equal to `databridge_mcp_ingest`.

#: A read tool that answers SQL — exposed to agents as `source.query:<connection>`.
QUERY_TOOL = re.compile(r"(?:^|[_\-.])(query|sql|select)(?:[_\-.]|$)", re.I)

#: Generic words a user says for a TYPE of source. Data, not branches: the compiler looks a word
#: up here to find candidate connections by `ConnectionInfo.kind`, and asks when it finds several.
KIND_WORDS: dict[str, tuple[str, ...]] = {
    "files": ("files", "file share", "shared folder", "filesystem", "file system"),
    "database": ("database", "db", "sql database", "postgres", "postgresql"),
    "drive": ("drive", "google drive", "gdrive"),
}

#: The internal tools every agent may be granted. The gateway may pass a narrower set.
DEFAULT_INTERNAL_TOOLS = ("graph.search", "graph.neighbors", "graph.path", "graph.node",
                          "retrieve.search")


@dataclass(frozen=True)
class ToolRef:
    name: str
    write: Optional[bool] = None
    """``None`` (the default) means "classify it": the shared fail-closed rule decides, so a
    hand-built ``ToolRef("execute_sql")`` is a write, never a read by omission (Codex batch 2 #3).
    Pass an explicit bool only with evidence, and say which in ``basis``."""
    description: str = ""
    basis: str = "declared"
    """Why it is read or write: ``annotation`` (the server's hints), ``name`` (a read/write verb in
    its name), ``unknown`` (neither — treated as a write), or ``declared`` (built by hand)."""

    def __post_init__(self) -> None:
        if self.write is None:
            c = _shared_classify(self.name)
            object.__setattr__(self, "write", bool(c.writes))
            object.__setattr__(self, "basis", c.basis)

    @property
    def query(self) -> bool:
        return not self.write and bool(QUERY_TOOL.search(self.name))


@dataclass(frozen=True)
class ConnectionInfo:
    """One live connection as the compiler sees it."""

    id: str
    title: str = ""
    kind: str = ""
    """A generic source type (`files`, `database`, `drive`, …) — keys `KIND_WORDS`. Optional."""
    tools: tuple[ToolRef, ...] = ()
    locations: tuple[str, ...] = ()
    """Folder paths (or `schema.table` names) this connection is known to hold, e.g.
    ``Contracts``, ``Shared/Follow-ups``. From the latest ingest inventory and the declared scope."""
    aliases: tuple[str, ...] = ()
    """Extra words a user may call it by ("crm", "larkspur files")."""
    plan_status: Optional[str] = None
    """Crawl-plan status (`proposed` / `approved`) if a plan exists."""
    purpose: str = ""
    """The connection YAML's `purpose`. ``agent_write`` marks a WRITE-ONLY connection (e.g. an
    INSERT-only database role, S2): it is never read, never crawled, never offered for reads —
    every tool on it is a write, usable only as an approved action."""
    writes_for: tuple[str, ...] = ()
    """For an ``agent_write`` connection: the read connection(s) whose writes it carries. Declared
    data; when absent the compiler ASKS which write connection a write should use."""
    facts: Optional[bool] = None
    """Whether the connection has a read-only facts store (`facts.query:<id>`). None = derive from
    `kind` (a ``database`` has one)."""
    reading_path: tuple[str, ...] = ()
    """FIN-11: the tools a HUMAN-APPROVED crawl plan reads this connection with (its roots, list
    and read steps; `plan_read_tools`), SQL aside. A document source is listed AND read along this
    path, so an agent granted one step of it is granted the rest (`compiler._read_tools`) — a
    folder it can list but not read is a grant that cannot answer."""

    @property
    def agent_write(self) -> bool:
        return self.purpose == "agent_write"

    @property
    def read_tools(self) -> tuple[ToolRef, ...]:
        if self.agent_write:
            return ()
        return tuple(t for t in self.tools if not t.write)

    @property
    def write_tools(self) -> tuple[ToolRef, ...]:
        """The tools the SHARED classifier calls writes (E2E N-21). ``agent_write`` does not turn a
        read-classified tool (``list_schemas``) into a write tool to offer: on a write-only
        connection such a tool is neither read nor offered as a write — it is simply unused."""
        return tuple(t for t in self.tools if t.write)

    @property
    def query_capable(self) -> bool:
        return not self.agent_write and any(t.query for t in self.tools)

    @property
    def facts_capable(self) -> bool:
        if self.agent_write:
            return False
        return self.facts if self.facts is not None else self.kind == "database"

    def tool(self, name: str) -> Optional[ToolRef]:
        for t in self.tools:
            if t.name == name:
                if self.agent_write and not t.write:
                    return replace(t, write=True, basis="purpose")
                return t
        return None

    def names(self) -> list[str]:
        """Every phrase that refers to exactly this connection (kind words excluded)."""
        out = {self.id.lower(), self.id.replace("-", " ").replace("_", " ").lower()}
        if self.title:
            out.add(self.title.lower())
        out.update(a.lower() for a in self.aliases if a.strip())
        return sorted(o for o in out if o)


@dataclass(frozen=True)
class AdapterAgentInfo:
    """A REGISTERED adapter agent as the compiler sees it (E2E N-04): something a workflow agent
    may delegate read-only sub-queries to with ``delegate.ask:<id>``. From the adapter-agent
    registry on disk; never invented from the user's words."""

    id: str
    title: str = ""
    connections: tuple[str, ...] = ()
    status: str = "active"

    @property
    def delegable(self) -> bool:
        """Offered as a delegate: not paused (the federated service answers for every adapter
        agent that is not paused — `databridge_federated.catalogue.build_catalogue`)."""
        return self.status != "paused"

    @property
    def tool(self) -> str:
        return f"delegate.ask:{self.id}"

    def names(self) -> list[str]:
        """Every phrase that refers to exactly this adapter agent."""
        out = {self.id.lower(), self.id.replace("-", " ").replace("_", " ").lower()}
        if self.title.strip():
            out.add(self.title.strip().lower())
        return sorted(o for o in out if o)


def adapter_agents_of(catalogue: Any) -> tuple[AdapterAgentInfo, ...]:
    """The delegable adapter agents of ``catalogue`` (an older catalogue without the field has
    none)."""
    return tuple(a for a in (getattr(catalogue, "adapter_agents", ()) or ()) if a.delegable)


def adapter_agent_of(catalogue: Any, aid: str) -> Optional[AdapterAgentInfo]:
    return next((a for a in adapter_agents_of(catalogue) if a.id == aid), None)


@dataclass(frozen=True)
class Governance:
    """The governance half's deployment defaults — what an English sentence cannot supply.

    `purpose` must be a purpose the pack declares; `golden_set` a repo-relative JSONL the eval
    binding points at. Both are checked by `databridge_agentdef.compile_agent`, which refuses —
    it never waives — when either is missing. The compiler surfaces that refusal verbatim.
    """

    pack: str = "workspace"
    purpose: Optional[str] = None
    trust_ceiling: str = "untrusted"
    citation: str = "required_per_field"
    golden_set: Optional[str] = None
    escalations_per_day: int = 20
    cost_ceiling_usd_per_day: float = 5.0


class CatalogueLike(Protocol):
    connections: Sequence[ConnectionInfo]
    kinds: Sequence[str]
    subject_kinds: Sequence[str]
    internal_tools: Sequence[str]
    governance: Governance
    existing_ids: Sequence[str]


@dataclass(frozen=True)
class Catalogue:
    connections: tuple[ConnectionInfo, ...] = ()
    kinds: tuple[str, ...] = ()
    subject_kinds: tuple[str, ...] = ()
    internal_tools: tuple[str, ...] = DEFAULT_INTERNAL_TOOLS
    governance: Governance = field(default_factory=Governance)
    existing_ids: tuple[str, ...] = ()
    adapter_agents: tuple[AdapterAgentInfo, ...] = ()
    """Registered adapter agents (E2E N-04): each delegable one is offered as
    ``delegate.ask:<id>``."""

    def connection(self, cid: str) -> Optional[ConnectionInfo]:
        for c in self.connections:
            if c.id == cid:
                return c
        return None

    def with_governance(self, **changes: Any) -> "Catalogue":
        return replace(self, governance=replace(self.governance, **changes))

    def as_record(self) -> dict:
        return {
            "connections": [{"id": c.id, "title": c.title, "kind": c.kind,
                             "tools": [{"name": t.name, "write": t.write, "basis": t.basis}
                                       for t in c.tools],
                             "locations": list(c.locations), "aliases": list(c.aliases),
                             "plan_status": c.plan_status, "purpose": c.purpose or None,
                             "writes_for": list(c.writes_for), "facts": c.facts_capable}
                            for c in self.connections],
            "kinds": list(self.kinds), "subject_kinds": list(self.subject_kinds),
            "internal_tools": list(self.internal_tools),
            "adapter_agents": [{"id": a.id, "title": a.title, "connections": list(a.connections),
                                "status": a.status, "delegable": a.delegable,
                                "tool": a.tool if a.delegable else None}
                               for a in self.adapter_agents],
        }


def connection_of(catalogue: CatalogueLike, cid: str) -> Optional[ConnectionInfo]:
    for c in catalogue.connections:
        if c.id == cid:
            return c
    return None


def classify_tool(name: str, *, read_only: Optional[bool] = None,
                  destructive: Optional[bool] = None, description: str = "",
                  approved_read: bool = False, denied: bool = False) -> ToolRef:
    """A tool as read or write — `databridge_agent_runtime.classify.classify_tool`, the rule the
    runtime's gateway applies to the same tool (E2E F-10). Fail closed, strict wins:

    * a declared deny list, a write verb in the name, ``destructive=True`` or ``read_only=False``
      -> **write** (a name that says it writes beats a server's ``readOnlyHint: true``);
    * else ``read_only=True``, a HUMAN-APPROVED crawl plan's read step (``approved_read``) or a
      read verb from the closed `READ_VERB` allow-list in the name -> read;
    * else **unknown, treated as a write**: it can only ever be an approved action, never a read
      grant, so an unannotated ``approve_invoice`` cannot reach a read-only agent."""
    c = _shared_classify(name, read_only=read_only, destructive=destructive,
                         approved_read=approved_read, denied=denied)
    return ToolRef(name=name, write=c.writes, description=description, basis=c.basis)


# --- the agentdef adapter catalogue ------------------------------------------------------------
# ONE naming scheme, owned by the runtime (W2, 2026-10-01). A draft compiled here is re-compiled by
# `databridge_agent_runtime.compile_for_run` before it runs, against the catalogue the runtime's
# `build_catalogue` builds. Screening it here against anything else (the old `larkspur_files.
# list_dir` naming) let a draft compile at authoring time and be refused at run time. So this is a
# thin adapter: `ConnectionInfo` -> the runtime's `ConnectionAdvert`, then the runtime's own
# `build_catalogue`. No capability name is spelled in this package.
def runtime_adverts(catalogue: CatalogueLike) -> list:
    """Each connection as the runtime's `ConnectionAdvert` — the shape its source gateway reports."""
    from databridge_agent_runtime import ConnectionAdvert, ToolAdvert

    out = []
    for c in catalogue.connections:
        tools = []
        for t in c.tools:
            ref = c.tool(t.name) or t                  # `agent_write` turns every tool into a write
            tools.append(ToolAdvert(ref.name, writes=bool(ref.write), description=ref.description))
        sql_tool = next((t.name for t in c.read_tools if t.query), None)
        out.append(ConnectionAdvert(connection=c.id, tools=tuple(tools), sql=c.query_capable,
                                    declared_in=f"<connection {c.id}: live tools/list>",
                                    sql_tool=sql_tool))
    return out


def runtime_catalogue(catalogue: CatalogueLike) -> tuple[Any, list[str]]:
    """``(AdapterCatalogue, problems)`` from `databridge_agent_runtime.build_catalogue` — the same
    function `compile_for_run` screens a declaration with at run time."""
    from databridge_agent_runtime import build_catalogue

    families = {str(t).partition(".")[0] for t in catalogue.internal_tools}
    return build_catalogue(
        pack=catalogue.governance.pack, connections=runtime_adverts(catalogue),
        graph="graph" in families, retrieval="retrieve" in families,
        facts=[c.id for c in catalogue.connections if c.facts_capable],
        # E2E N-04: a registered adapter agent is a delegate the runtime advertises when a
        # federated service answers for it (`compile_for_run` checks that again before each run)
        delegates=[a.id for a in adapter_agents_of(catalogue)])


def to_adapter_catalogue(catalogue: CatalogueLike):
    """What the catalogue advertises, as `databridge_agentdef.AdapterCatalogue`, so a generated
    declaration is screened against the LIVE tool lists rather than against itself — named
    exactly as the runtime names it (`runtime_catalogue`)."""
    return runtime_catalogue(catalogue)[0]


# --- loading from disk ---------------------------------------------------------------------------
def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _latest_inventory(ingest_root: Path, cid: str) -> list[dict]:
    base = ingest_root / cid
    if not base.is_dir():
        return []
    runs = sorted((p for p in base.iterdir() if (p / "inventory.jsonl").is_file()),
                  key=lambda p: p.name)
    if not runs:
        return []
    out = []
    for line in (runs[-1] / "inventory.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def folder_paths(rels: Iterable[str]) -> list[str]:
    """Every folder prefix of each relative item path (`a/b/c.pdf` -> `a`, `a/b`)."""
    out: set[str] = set()
    for rel in rels:
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
        for i in range(1, len(parts)):
            out.add("/".join(parts[:i]))
    return sorted(out)


def _literal_segments(globs: Iterable[str]) -> list[str]:
    out = []
    for g in globs:
        segs = [s for s in str(g).replace("\\", "/").split("/")
                if s and not any(ch in s for ch in "*?[")]
        if segs:
            out.append("/".join(segs))
    return out


def load_ontology_kinds(pack: str, packs_root: Optional[Path] = None
                        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(node kinds, subject kinds) declared by ``packs/<pack>/ontology.yaml``; empty if absent."""
    data = _read_yaml((packs_root or paths.packs_dir()) / pack / "ontology.yaml") or {}
    if not isinstance(data, dict):
        return (), ()
    kinds = tuple(str(k.get("name")) for k in data.get("node_kinds") or []
                  if isinstance(k, dict) and k.get("name"))
    subjects = tuple(str(k.get("kind")) for k in data.get("subject_kinds") or []
                     if isinstance(k, dict) and k.get("kind"))
    return kinds, subjects


def load_pack_purposes(pack: str, packs_root: Optional[Path] = None) -> tuple[str, ...]:
    data = _read_yaml((packs_root or paths.packs_dir()) / pack / "purposes.yaml") or {}
    if not isinstance(data, dict):
        return ()
    return tuple(str(p.get("id")) for p in data.get("purposes") or []
                 if isinstance(p, dict) and p.get("id"))


#: The purposes.yaml field that marks the ONE purpose agents compiled from English bind when the
#: pack declares several and the operator names none (``WALNUT_AGENT_PURPOSE``).
DEFAULT_PURPOSE_FIELD = "default_for_compiled_agents"
#: A pack's answer-contract golden set: the floor every agent compiled from English is measured
#: against when the operator names none (``WALNUT_AGENT_GOLDEN_SET``) and the agent has no own set.
AGENT_CONTRACT_GOLDEN = "evals/agent_contract.jsonl"


def default_agent_purpose(pack: str, packs_root: Optional[Path] = None) -> Optional[str]:
    """The purpose a compiled agent binds when nobody names one: the pack's only purpose, else
    the ONE it marks `DEFAULT_PURPOSE_FIELD`; None when that is not exactly one (the compiler
    then says so, naming the file — `declaration.compile_declaration`). Read from the pack, never
    chosen by the engine."""
    data = _read_yaml((packs_root or paths.packs_dir()) / pack / "purposes.yaml") or {}
    entries = [p for p in (data.get("purposes") or []) if isinstance(p, dict) and p.get("id")]         if isinstance(data, dict) else []
    if len(entries) == 1:
        return str(entries[0]["id"])
    marked = [str(p["id"]) for p in entries if p.get(DEFAULT_PURPOSE_FIELD) is True]
    return marked[0] if len(marked) == 1 else None


def default_golden_set(pack: str, packs_root: Optional[Path] = None) -> Optional[str]:
    """``packs/<pack>/evals/agent_contract.jsonl`` when the pack ships one, else None."""
    root = packs_root or paths.packs_dir()
    return f"packs/{pack}/{AGENT_CONTRACT_GOLDEN}" if (root / pack / AGENT_CONTRACT_GOLDEN
                                                       ).is_file() else None


def load_connection_info(path: Path, *, ingest_root: Optional[Path] = None
                         ) -> Optional[ConnectionInfo]:
    """One `WALNUT_SOURCES_DIR/<id>.yaml` (+ its `<id>.plan.yaml`, `<id>.tools.yaml` if present,
    + the latest ingest inventory) as a `ConnectionInfo`. Unreadable → None (never invented)."""
    data = _read_yaml(path)
    if not isinstance(data, dict) or not data.get("id"):
        return None
    cid = str(data["id"])
    base = path.parent
    plan = _read_yaml(base / f"{cid}.plan.yaml")
    plan = plan if isinstance(plan, dict) else None
    # the same evidence the runtime's gateway classifies with (E2E F-10): an APPROVED plan's read
    # steps vouch for a tool; the plan's and the scope's deny lists make it a write
    vouched = plan_read_tools(plan)
    deny = plan_denied_tools(plan, data)

    def classify(name: str, **hints: Any) -> ToolRef:
        return classify_tool(name, approved_read=name in vouched, denied=name in deny, **hints)

    tools: dict[str, ToolRef] = {}
    tool_file = _read_yaml(base / f"{cid}.tools.yaml")
    if isinstance(tool_file, dict):
        tool_file = tool_file.get("tools")
    for t in tool_file or []:
        if isinstance(t, dict) and t.get("name"):
            tools[str(t["name"])] = classify(
                str(t["name"]), read_only=t.get("read_only"), destructive=t.get("destructive"),
                description=str(t.get("description") or ""))
        elif isinstance(t, str):
            tools[t] = classify(t)
    hint = data.get("tools_hint") if isinstance(data.get("tools_hint"), dict) else {}
    for key in ("write", "read"):
        names = hint.get(key)
        for name in ([names] if isinstance(names, str) else (names or [])):
            if isinstance(name, str) and name and name not in tools:
                # a hint is the declaration's CLAIM: a "write" hint makes it a write; a "read"
                # hint is still classified by the rules (a claim of reading is not trusted)
                tools[name] = (ToolRef(name=name, write=True, basis="declared")
                               if key == "write" else classify(name))
    plan_status = None
    kind = str(data.get("kind") or data.get("source_class") or "")
    facts: Optional[bool] = data.get("facts") if isinstance(data.get("facts"), bool) else None
    if isinstance(plan, dict):
        plan_status = plan.get("status")
        for name in plan.get("tools_seen") or []:
            tools.setdefault(str(name), classify(str(name)))
        # a crawl-plan step names its reading strategy in `strategy` (mcp_ingest.plans:
        # documents | resources | tabular); `kind` is read too for older hand-written plans (E2E F-22)
        step_kinds = {str(s.get("strategy") or s.get("kind"))
                      for s in plan.get("steps") or [] if isinstance(s, dict)}
        if not kind:
            if step_kinds == {"tabular"}:
                kind = "database"
            elif step_kinds and step_kinds <= {"documents", "resources"}:
                kind = "files"
        if facts is None and "tabular" in step_kinds:
            facts = True
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    rels = [r.get("rel") for r in _latest_inventory(ingest_root or (paths.var_dir() / "ingest"), cid)
            if isinstance(r, dict) and r.get("rel")]
    locations = sorted(set(folder_paths(rels)) | set(_literal_segments(scope.get("include") or []))
                       | set(_literal_segments(scope.get("roots") or [])))
    aliases = tuple(str(a) for a in data.get("aliases") or [] if isinstance(a, str))
    wf = data.get("writes_for")
    writes_for = tuple(str(x) for x in ([wf] if isinstance(wf, str) else (wf or []))
                       if isinstance(x, str) and x)
    sql_steps = {str(s.get("sql", {}).get("tool")) for s in (plan or {}).get("steps") or []
                 if isinstance(s, dict) and isinstance(s.get("sql"), dict)}
    reading_path = tuple(sorted(t for t in vouched if t in tools and t not in sql_steps
                                and t not in deny and not tools[t].write and not tools[t].query))
    return ConnectionInfo(id=cid, title=str(data.get("title") or ""), kind=kind,
                          tools=tuple(tools.values()), locations=tuple(locations),
                          aliases=aliases, plan_status=plan_status,
                          purpose=str(data.get("purpose") or ""), writes_for=writes_for,
                          facts=facts, reading_path=reading_path)


def writers_for(catalogue: CatalogueLike, cid: str) -> list[ConnectionInfo]:
    """The ``agent_write`` connections that DECLARE they carry writes for ``cid``."""
    return [c for c in catalogue.connections if c.agent_write and cid in c.writes_for]


def load_adapter_agents(root: Optional[Path] = None) -> tuple[AdapterAgentInfo, ...]:
    """The adapter-agent registry on disk (``WALNUT_ADAPTER_AGENTS_DIR``), read the way
    `databridge_adapter_agents.AdapterAgentRegistry.list` reads it: every ``<id>.yaml`` that loads
    as an adapter_agent/v1 spec, minus those whose ``_state/<id>.json`` marks them deleted. A file
    that does not parse is skipped (the registry surfaces it; it is never offered)."""
    from databridge_workflow_spec import AdapterAgentSpec, load_spec

    base = root or paths.adapter_agents_dir()
    if not base.is_dir():
        return ()
    out: list[AdapterAgentInfo] = []
    for p in sorted(base.glob("*.yaml")):
        try:
            spec = load_spec(p)
        except Exception:                                    # noqa: BLE001 - not offered
            continue
        if not isinstance(spec, AdapterAgentSpec):
            continue
        state = base / "_state" / f"{spec.id}.json"
        try:
            if state.is_file() and json.loads(state.read_text(encoding="utf-8")).get("deleted"):
                continue
        except (OSError, ValueError, AttributeError):
            continue                                         # unreadable state: fail closed
        out.append(AdapterAgentInfo(id=spec.id, title=spec.title or "",
                                    connections=tuple(spec.connections), status=spec.status))
    return tuple(out)


def load_catalogue(sources_dir: Optional[Path] = None, *, pack: Optional[str] = None,
                   packs_root: Optional[Path] = None, ingest_root: Optional[Path] = None,
                   internal_tools: Sequence[str] = DEFAULT_INTERNAL_TOOLS,
                   existing_ids: Sequence[str] = (),
                   adapter_agents_root: Optional[Path] = None) -> Catalogue:
    """The catalogue as the files on disk declare it. A missing directory is an empty catalogue."""
    base = sources_dir or paths.sources_dir()
    conns: list[ConnectionInfo] = []
    if base.is_dir():
        for p in sorted(base.glob("*.yaml")):
            if "." in p.stem:                        # <id>.plan.yaml, <id>.mapping.yaml, …
                continue
            info = load_connection_info(p, ingest_root=ingest_root)
            if info is not None:
                conns.append(info)
    pk = pack or paths.bound_pack()
    kinds, subjects = load_ontology_kinds(pk, packs_root)
    gov = Governance(pack=pk, purpose=(os.environ.get("WALNUT_AGENT_PURPOSE")
                                       or default_agent_purpose(pk, packs_root)),
                     golden_set=(os.environ.get("WALNUT_AGENT_GOLDEN_SET")
                                 or default_golden_set(pk, packs_root)))
    return Catalogue(connections=tuple(conns), kinds=kinds, subject_kinds=subjects,
                     internal_tools=tuple(internal_tools), governance=gov,
                     existing_ids=tuple(existing_ids),
                     adapter_agents=load_adapter_agents(adapter_agents_root))


__all__ = ["Catalogue", "CatalogueLike", "ConnectionInfo", "Governance", "ToolRef",
           "AdapterAgentInfo", "adapter_agents_of", "adapter_agent_of", "load_adapter_agents",
           "KIND_WORDS", "WRITE_VERB", "WRITE_VERB_EXTRA", "writes_by_name", "READ_VERB",
           "QUERY_TOOL", "DEFAULT_INTERNAL_TOOLS", "classify_tool",
           "runtime_adverts", "runtime_catalogue", "to_adapter_catalogue", "load_catalogue",
           "load_connection_info", "load_ontology_kinds", "load_pack_purposes", "folder_paths",
           "default_agent_purpose", "default_golden_set", "DEFAULT_PURPOSE_FIELD",
           "AGENT_CONTRACT_GOLDEN",
           "connection_of", "writers_for", "EXEC_VERB"]
