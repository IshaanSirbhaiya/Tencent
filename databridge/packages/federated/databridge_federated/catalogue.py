"""What the planner may name: adapter agents, their connections, facts tables and columns, and the
bound ontology kinds — MEASURED from the live registry, facts stores and vocabulary, never from the
plan itself (a name checked against a list the plan's author also wrote is not checked).

A connection is ``tabular`` when its read-only facts store exists (exact SQL is then the fastest
correct method), otherwise ``documents`` (graph entities + passage search). One adapter agent may
hold both kinds.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from databridge_agent_runtime import sql_is_read_only

#: Facts-store bookkeeping tables (`_facts_tables`, `_facts_columns`, `_facts_meta`) start with "_".
_LIST_TABLES = ("SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND substr(name, 1, 1) <> '_' ORDER BY name")


@dataclass(frozen=True)
class FactsTable:
    name: str
    columns: tuple[str, ...]
    #: The row key: a column MEASURED to name exactly one row (no NULL, no duplicate as text),
    #: the first such column in table order. ``None`` when no column does: rows of that table
    #: cannot be named, so they are never read as rows or cited cell by cell (R-021 r2 #10).
    #: The facts store records no primary key, so the key is proven, never assumed; every read
    #: re-proves it in the same statement.
    key: Optional[str]


@dataclass(frozen=True)
class ConnectionEntry:
    connection: str
    channel: str                              # "tabular" | "documents"
    tables: tuple[FactsTable, ...] = ()
    extract_date: Optional[str] = None

    def table(self, name: str) -> Optional[FactsTable]:
        return next((t for t in self.tables if t.name == name), None)


@dataclass(frozen=True)
class AdapterAgentEntry:
    id: str
    title: str
    status: str
    connections: tuple[ConnectionEntry, ...]

    def connection(self, cid: str) -> Optional[ConnectionEntry]:
        return next((c for c in self.connections if c.connection == cid), None)


@dataclass
class Catalogue:
    agents: dict[str, AdapterAgentEntry] = field(default_factory=dict)
    node_kinds: frozenset[str] = frozenset()
    edge_kinds: frozenset[str] = frozenset()
    subject_kinds: frozenset[str] = frozenset()
    problems: list[str] = field(default_factory=list)
    #: The closed ontology is only enforceable when it LOADED. ``False`` is not "an empty
    #: vocabulary that admits everything": documents are neither planned nor read without it.
    vocabulary_loaded: bool = False
    #: E2E4 Q-01: connections whose facts store EXISTS but could not be read, after every retry,
    #: while the catalogue was measured (connection -> why). Their tables are unknown, not absent:
    #: nothing is planned against this catalogue (the service says it is being refreshed). Such a
    #: connection is never listed as a documents connection in its place.
    refreshing: dict[str, str] = field(default_factory=dict)

    def agent(self, aa: str) -> Optional[AdapterAgentEntry]:
        return self.agents.get(aa)

    def is_subject(self, kind: str) -> bool:
        return kind in self.subject_kinds

    def describe(self) -> dict:
        """The catalogue as the planner sees it (names only — no data)."""
        return {
            "adapter_agents": [
                {"id": a.id, "title": a.title,
                 "connections": [
                     {"connection": c.connection, "channel": c.channel,
                      **({"facts_tables": [{"table": t.name, "columns": list(t.columns)}
                                           for t in c.tables],
                          "extracted_at": c.extract_date} if c.channel == "tabular" else
                         {"document_fields": ["text", "date"],
                          "entity_kinds": sorted(self.node_kinds)})}
                     for c in a.connections]}
                for a in self.agents.values()],
            "vocabulary_loaded": self.vocabulary_loaded,
            "node_kinds": sorted(self.node_kinds),
            "edge_kinds": sorted(self.edge_kinds),
            "subject_kinds (people: candidates only, exact-identifier links only)":
                sorted(self.subject_kinds),
        }


def describe_facts_tables(facts: Any, connection: str) -> tuple[list[FactsTable], Optional[str]]:
    """Tables and columns of one facts store, read through the store's own read-only query."""
    ok, why = sql_is_read_only(_LIST_TABLES)
    assert ok, why
    ans = facts.query(connection, _LIST_TABLES)
    out: list[FactsTable] = []
    for (name,) in [tuple(r) for r in getattr(ans, "rows", [])]:
        q = f'SELECT * FROM {_qi(str(name))} LIMIT 0'
        cols = list(getattr(facts.query(connection, q), "columns", []) or [])
        if cols:
            out.append(FactsTable(name=str(name), columns=tuple(cols),
                                  key=measure_key(facts, connection, str(name), cols)))
    return out, getattr(ans, "extract_date", None)


#: E2E4 Q-01: how often a facts store is described before it counts as unreadable, and the first
#: pause (doubled each time: 0.05 + 0.1 + 0.2 + 0.4 + 0.8 s). The description is a FIXED set of
#: read-only statements, so a failure is never the statement's fault: it is the store being
#: contended or republished (a concurrent reader replacing a query artefact, a Windows file lock,
#: a manifest written just after its file). It is read again rather than taken as "no tables".
DESCRIBE_ATTEMPTS = 6
DESCRIBE_PAUSE_S = 0.05


def describe_steady(facts: Any, connection: str, *, attempts: int = DESCRIBE_ATTEMPTS,
                    pause: float = DESCRIBE_PAUSE_S,
                    sleep: Any = None) -> tuple[list[FactsTable], Optional[str]]:
    """`describe_facts_tables`, read again on failure (a consistent read of the last published
    store). Raises the last error when every attempt failed."""
    last: Optional[BaseException] = None
    for i in range(max(1, attempts)):
        try:
            return describe_facts_tables(facts, connection)
        except Exception as e:                               # noqa: BLE001 - read again
            last = e
            if i < attempts - 1:
                (sleep or time.sleep)(pause * (2 ** i))
    assert last is not None
    raise last


class SteadyFacts:
    """A facts store whose reads are retried on a FILE-SYSTEM error (``OSError``: a sharing
    violation or a file replaced under the reader), never on a refused or failed statement — the
    same read-only statement, read again (E2E4 Q-01). Everything else is the wrapped store's."""

    def __init__(self, inner: Any, *, attempts: int = DESCRIBE_ATTEMPTS,
                 pause: float = DESCRIBE_PAUSE_S, sleep: Any = None) -> None:
        self.inner = inner
        self._attempts, self._pause, self._sleep = max(1, attempts), pause, sleep

    def available(self, connection: str) -> bool:
        return self.inner.available(connection)

    def query(self, connection: str, sql: str, **kw: Any) -> Any:
        for i in range(self._attempts):
            try:
                return self.inner.query(connection, sql, **kw)
            except OSError:
                if i == self._attempts - 1:
                    raise
                (self._sleep or time.sleep)(self._pause * (2 ** i))
        raise AssertionError("unreachable")                  # pragma: no cover

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def key_proof_sql(table: str, column: str) -> str:
    """A scalar subquery: 0 exactly when ``column`` names one row each — no NULL and no duplicate
    as TEXT (the row id a pointer carries is the text form)."""
    return (f"(SELECT COUNT(*) - COUNT(DISTINCT CAST({_qi(column)} AS TEXT)) FROM {_qi(table)})")


def measure_key(facts: Any, connection: str, table: str, cols: list[str]) -> Optional[str]:
    """The first column that is MEASURED unique and non-NULL across the whole table, in one
    read-only statement; ``None`` when there is none (or it cannot be measured)."""
    sql = "SELECT " + ", ".join(f"{key_proof_sql(table, c)} AS k{i}"
                                 for i, c in enumerate(cols))
    ok, _why = sql_is_read_only(sql)
    if not ok:
        return None
    try:
        res = facts.query(connection, sql)
        row = list(getattr(res, "rows", [])[0])
    except OSError:
        # E2E4 Q-01: the store was contended, not keyless — the description is read again
        raise
    except Exception:                                         # noqa: BLE001 - no key, fail closed
        return None
    for c, dupes in zip(cols, row):
        if dupes is not None and int(dupes) == 0:
            return c
    return None


def build_catalogue(specs: Iterable[Any], *, facts: Any = None, documents: bool = True,
                    vocabulary: Any = None) -> Catalogue:
    """From adapter-agent specs (``AdapterAgentSpec``: ``id``, ``title``, ``status``,
    ``connections``), the facts store and the vocabulary. Paused agents are left out (their
    sources are not being kept right); their absence is recorded as a problem, not hidden."""
    cat = Catalogue()
    if vocabulary is None:
        cat.problems.append("the ontology vocabulary is not loaded; documents cannot be read "
                            "(the closed ontology fails closed)")
    else:
        cat.vocabulary_loaded = True
        cat.node_kinds = frozenset(getattr(vocabulary, "node_kinds", {}) or {})
        cat.edge_kinds = frozenset(getattr(vocabulary, "edge_kinds", {}) or {})
        cat.subject_kinds = frozenset(getattr(vocabulary, "subject_kinds", {}) or {})
    for spec in specs:
        if getattr(spec, "status", "active") == "paused":
            cat.problems.append(f"adapter agent {spec.id!r} is paused; it is not asked")
            continue
        conns: list[ConnectionEntry] = []
        for cid in spec.connections:
            has_facts = False
            try:
                has_facts = facts is not None and bool(facts.available(cid))
            except Exception as e:                           # noqa: BLE001 - reported
                cat.problems.append(f"{cid}: facts store could not be checked: {e}")
            if has_facts:
                try:
                    tables, extracted = describe_steady(facts, cid)
                    conns.append(ConnectionEntry(cid, "tabular", tuple(tables), extracted))
                except Exception as e:                       # noqa: BLE001 - reported
                    # E2E4 Q-01: the store exists, so its tables are UNKNOWN, not absent. It is
                    # not offered as a documents connection in its place (a plan over that says
                    # "there is no invoices table"); the service says the catalogue is being
                    # refreshed and plans nothing.
                    why = f"{type(e).__name__}: {e}"
                    cat.refreshing[cid] = why
                    cat.problems.append(f"{cid}: facts store could not be described: {why}")
                continue
            if documents:
                conns.append(ConnectionEntry(cid, "documents"))
        cat.agents[spec.id] = AdapterAgentEntry(id=spec.id, title=getattr(spec, "title", spec.id),
                                                status=getattr(spec, "status", "active"),
                                                connections=tuple(conns))
    return cat


def registry_specs(root: Any = None) -> tuple[list[Any], dict[str, str]]:
    """Adapter-agent specs from the registry (read-only use of `databridge_adapter_agents`)."""
    from databridge_adapter_agents import AdapterAgentRegistry        # noqa: PLC0415
    reg = AdapterAgentRegistry(root)
    specs = reg.list()
    return specs, dict(reg.problems)


@dataclass(frozen=True)
class DirectFactsSpec:
    """A declared connection no adapter agent keeps, offered to the planner through its own
    read-only facts store (FIN-15). Shaped like an ``AdapterAgentSpec`` for `build_catalogue`; its
    id is the connection's own id, so a plan names the source it reads."""

    id: str
    title: str
    connections: tuple[str, ...]
    status: str = "active"


def declared_connections(sources_dir: Any = None) -> list[str]:
    """Every connection the deployment declares: ``<WALNUT_SOURCES_DIR>/<id>.yaml`` (the plan,
    mapping and tools files beside it — ``<id>.<what>.yaml`` — are not connections). Read-only;
    an unreadable directory declares nothing."""
    from pathlib import Path                                      # noqa: PLC0415

    if sources_dir is None:
        try:
            from databridge_mcp_ingest import paths as _paths      # noqa: PLC0415
            sources_dir = _paths.sources_dir()
        except Exception:                                         # noqa: BLE001 - none declared
            return []
    base = Path(sources_dir)
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.yaml") if "." not in p.stem)


def unowned_facts_specs(specs: Iterable[Any], facts: Any, connections: Optional[Iterable[str]]
                        = None) -> tuple[list[DirectFactsSpec], list[str]]:
    """``(specs, notes)``: one `DirectFactsSpec` per declared connection that NO adapter agent
    (active or paused) keeps and whose facts store exists. FIN-15: counting is a read of the facts
    store the ingest already built and a human already approved; it does not need an adapter agent
    to exist first. A paused adapter agent's connection is NOT offered this way (the operator
    paused it). The caller's grant still decides what is read (`CatalogueAdmission`)."""
    if facts is None:
        return [], []
    kept = {c for s in specs for c in getattr(s, "connections", ())}
    taken = {getattr(s, "id", None) for s in specs}
    out: list[DirectFactsSpec] = []
    for cid in (declared_connections() if connections is None else connections):
        cid = str(cid)
        if not cid or cid in kept or cid in taken:
            continue
        try:
            if not facts.available(cid):
                continue
        except Exception:                                         # noqa: BLE001 - not offered
            continue
        out.append(DirectFactsSpec(id=cid, title=f"{cid} (its facts store, read directly; no "
                                                 f"adapter agent keeps this source)",
                                   connections=(cid,)))
    notes = [f"{s.id}: no adapter agent keeps this connection; its read-only facts store is "
             f"offered directly" for s in out]
    return out, notes


__all__ = ["DirectFactsSpec", "declared_connections", "unowned_facts_specs", "FactsTable", "ConnectionEntry", "AdapterAgentEntry", "Catalogue", "build_catalogue",
           "describe_facts_tables", "describe_steady", "SteadyFacts", "DESCRIBE_ATTEMPTS",
           "registry_specs", "measure_key", "key_proof_sql"]
