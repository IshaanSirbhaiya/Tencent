"""What the planner may name: adapter agents, their connections, facts tables and columns, and the
bound ontology kinds — MEASURED from the live registry, facts stores and vocabulary, never from the
plan itself (a name checked against a list the plan's author also wrote is not checked).

A connection is ``tabular`` when its read-only facts store exists (exact SQL is then the fastest
correct method), otherwise ``documents`` (graph entities + passage search). One adapter agent may
hold both kinds.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
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
    #: E2E run 5, F-07: the stored values of each CATEGORICAL column (few distinct values, each
    #: repeated; see `measure_values`), ``((column, (value, ...)), ...)``. The planner reads a
    #: status as a filter on the table that holds it instead of guessing a document kind.
    values: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: Codex R-029 r1 #1/#6: the columns whose values name or identify a SUBJECT (a person),
    #: read from the source's APPROVED mapping (`<sources>/<connection>.mapping.yaml`): every
    #: column of a table mapped to a subject kind, and every column that is the evidence of an
    #: edge to a subject kind. ``None`` = the table is not in an approved mapping, so no column is
    #: known to be safe: nothing of it is resolved, case-widened or shown (fail closed).
    subject_columns: Optional[frozenset[str]] = None

    def is_subject_column(self, column: str) -> bool:
        """True unless the approved mapping shows ``column`` holds no subject's name or id."""
        return self.subject_columns is None or column in self.subject_columns


@dataclass(frozen=True)
class ConnectionEntry:
    connection: str
    channel: str                              # "tabular" | "documents"
    tables: tuple[FactsTable, ...] = ()
    extract_date: Optional[str] = None
    #: E2E run 5, F-07: for a documents connection, the node kinds the graph holds from THIS
    #: connection, MEASURED from the reverse pointer index (kind -> records). ``None`` = not
    #: measured (no store), and then every ontology kind is offered as before. A kind the
    #: connection holds no record of is not offered: offering every ontology kind made the filings
    #: keeper look like the owner of a `watchlist_entry` kind no source had ever produced.
    kinds: Optional[tuple[tuple[str, int], ...]] = None

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

    def connection_kinds(self, entry: "ConnectionEntry") -> list[str]:
        """The ontology kinds a documents connection may be asked for: the kinds it was MEASURED
        to hold (F-07). Unmeasured holds NOTHING (Codex R-029 r1 #7: fail closed — a source is
        never assigned a kind nobody showed it holds)."""
        if entry.kinds is None:
            return []
        return sorted(k for k, _n in entry.kinds if k in self.node_kinds)

    def describe(self) -> dict:
        """The catalogue as the planner sees it (names only — no data)."""
        return {
            "adapter_agents": [
                {"id": a.id, "title": a.title,
                 "connections": [
                     {"connection": c.connection, "channel": c.channel,
                      **({"facts_tables": [{"table": t.name, "columns": list(t.columns),
                                            # Codex R-029 r1 #5: column NAMES only. Stored
                                            # values never enter the planner's prompt (a value
                                            # is source data, and could carry instructions);
                                            # they are matched by rule outside the model.
                                            **({"categorical_columns": [
                                                col for col, _vs in t.values]}
                                               if t.values else {})}
                                           for t in c.tables],
                          "extracted_at": c.extract_date} if c.channel == "tabular" else
                         {"document_fields": ["text", "date"],
                          "entity_kinds": self.connection_kinds(c)})}
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
            key = measure_key(facts, connection, str(name), cols)
            out.append(FactsTable(name=str(name), columns=tuple(cols), key=key,
                                  values=_values_of(facts, connection, str(name), cols, key,
                                                    getattr(ans, "facts_sha256", None))))
    return out, getattr(ans, "extract_date", None)


#: (connection, facts file sha256, table, key) -> measured categorical values. A published facts
#: file never changes under its hash, so its values are measured once per file, not per request.
_VALUES_CACHE: dict = {}
_VALUES_CACHE_MAX = 512


def _values_of(facts: Any, connection: str, table: str, cols: list[str], key: Optional[str],
               sha: Optional[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(sha, str) or not sha:
        return measure_values(facts, connection, table, cols, key=key)
    ck = (connection, sha, table, key, tuple(cols))
    hit = _VALUES_CACHE.get(ck)
    if hit is None:
        hit = measure_values(facts, connection, table, cols, key=key)
        if len(_VALUES_CACHE) >= _VALUES_CACHE_MAX:
            _VALUES_CACHE.pop(next(iter(_VALUES_CACHE)))
        _VALUES_CACHE[ck] = hit
    return hit


#: E2E run 5, F-07: a column is CATEGORICAL when it holds at most this many distinct values
#: (letter case and spacing taken out) ...
CATEGORICAL_MAX = 12
#: ... and the table has at least this many rows per distinct value, so each value is a shared
#: label, never one row's own name (a 3-row table's names are not a category).
CATEGORICAL_REPEAT = 4
#: How long a categorical value may be, and how many spellings of all values are listed.
CATEGORICAL_VALUE_CHARS = 40
CATEGORICAL_SPELLINGS = 24


def _fold_sql(column: str) -> str:
    return f"lower(trim(CAST({_qi(column)} AS TEXT)))"


def measure_values(facts: Any, connection: str, table: str, cols: list[str], *,
                   key: Optional[str] = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The stored values of each CATEGORICAL column of one facts table, MEASURED with read-only
    statements (E2E run 5, F-07: "how many facilities are on the watchlist?" was planned against
    a document kind, because the planner saw `facility.status` only as a column name and never
    knew it holds ``WATCHLIST``).

    A column qualifies when, letter case and spacing taken out, it holds 2..`CATEGORICAL_MAX`
    distinct values, the table has at least `CATEGORICAL_REPEAT` rows per value, every value
    carries a letter (an amount or a date is never a category) and none is longer than
    `CATEGORICAL_VALUE_CHARS`. The row key never qualifies. Every stored SPELLING is listed (a
    filter on one of them is widened to the others by `delegates.fold_case_variants`). A store
    that cannot be measured lists nothing (fail closed: no values, never guessed ones)."""
    cand = [c for c in cols if c != key]
    if not cand:
        return ()
    sql = ("SELECT COUNT(*) AS n, " + ", ".join(
        f"COUNT(DISTINCT {_fold_sql(c)}) AS d{i}" for i, c in enumerate(cand))
        + f" FROM {_qi(table)}")
    ok, _why = sql_is_read_only(sql)
    if not ok:
        return ()
    try:
        row = list(getattr(facts.query(connection, sql), "rows", [])[0])
    except OSError:
        raise                                                 # contended: described again
    except Exception:                                         # noqa: BLE001 - none listed
        return ()
    n = int(row[0] or 0)
    out: list[tuple[str, tuple[str, ...]]] = []
    for c, d in zip(cand, row[1:]):
        d = int(d or 0)
        if d < 2 or d > CATEGORICAL_MAX or n < CATEGORICAL_REPEAT * d:
            continue
        q = (f"SELECT DISTINCT CAST({_qi(c)} AS TEXT) AS v FROM {_qi(table)} "
             f"WHERE {_qi(c)} IS NOT NULL ORDER BY v LIMIT {CATEGORICAL_SPELLINGS + 1}")
        if not sql_is_read_only(q)[0]:
            continue
        try:
            vals = [str(r[0]) for r in getattr(facts.query(connection, q), "rows", []) or []
                    if r and r[0] is not None]
        except OSError:
            raise
        except Exception:                                     # noqa: BLE001 - none listed
            continue
        if (not vals or len(vals) > CATEGORICAL_SPELLINGS
                or any(len(v) > CATEGORICAL_VALUE_CHARS or not any(ch.isalpha() for ch in v)
                       for v in vals)):
            continue
        out.append((c, tuple(vals)))
    return tuple(out)


#: (id(store), newest sources row, newest binding) -> whether the reverse index covers `sources`.
_COMPLETE_CACHE: dict = {}


def index_complete(store: Any) -> bool:
    """True when the reverse pointer index has seen every `sources` row (its own "known vs
    unknown" rule: one unbound row makes every answer from it unknown). Counted once per store
    state — the newest row of each table names the state — not once per connection."""
    fetch = getattr(store, "fetchall", None)
    if fetch is None:
        return False
    try:
        top = fetch("SELECT (SELECT MAX(rowid) FROM sources) AS s, "
                    "(SELECT MAX(source_rowid) FROM ptr_binding) AS b")[0]
        ck = (id(store), top[0], top[1])
        if ck not in _COMPLETE_CACHE:
            gap = fetch("SELECT (SELECT COUNT(*) FROM sources) - "
                        "(SELECT COUNT(*) FROM ptr_binding) AS gap")[0][0] or 0
            if len(_COMPLETE_CACHE) > 64:
                _COMPLETE_CACHE.clear()
            _COMPLETE_CACHE[ck] = gap <= 0
        return _COMPLETE_CACHE[ck]
    except Exception:                                         # noqa: BLE001 - no index
        return False


def source_node_kinds(store: Any, source_id: str) -> Optional[tuple[tuple[str, int], ...]]:
    """``((kind, open records), ...)`` — the node kinds the graph holds from ONE source, read
    through the reverse pointer index (`ptr_index` -> `ptr_binding` -> `nodes`, all indexed).
    When the store has no index, or the index has not seen every pointer (its own "known vs
    unknown" rule), the `sources` table is read instead. ``None`` when neither can be read: the
    connection's kinds are then unmeasured, never "none"."""
    # CROSS JOIN fixes the join order (SQLite's documented hint): the source's own pointers
    # first (`ix_ptr_by_source`), then their bindings and nodes by index. Left to the planner it
    # walked every node binding instead (0.25 s per source on the finance graph, against 0.01 s).
    by_index = ("SELECT n.kind AS kind, COUNT(DISTINCT n.id) AS c FROM ptr_index p "
                "CROSS JOIN ptr_binding b CROSS JOIN nodes n "
                "WHERE p.source_id = ? AND b.pointer_id = p.pointer_id "
                "AND b.entity_kind = 'node' AND n.id = b.entity_id AND n.system_to IS NULL "
                "GROUP BY n.kind ORDER BY n.kind")
    by_scan = ("SELECT n.kind AS kind, COUNT(DISTINCT n.id) AS c FROM sources s "
               "JOIN nodes n ON n.id = s.entity_id AND n.system_to IS NULL "
               "WHERE json_extract(s.source_pointer_json, '$.source_id') = ? "
               "GROUP BY n.kind ORDER BY n.kind")
    fetch = getattr(store, "fetchall", None)
    if fetch is None:
        return None
    complete = index_complete(store)
    for sql in ((by_index, by_scan) if complete else (by_scan,)):
        try:
            return tuple((str(r[0]), int(r[1])) for r in fetch(sql, (source_id,)))
        except Exception:                                     # noqa: BLE001 - next way
            continue
    return None


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


_UNSET = object()


def _people_kinds(vocabulary: Any, cat: "Catalogue") -> set[str]:
    """The subject kinds, and any kind the vocabulary calls a person (persons are subjects)."""
    is_person = getattr(vocabulary, "is_person", None)
    return set(cat.subject_kinds) | {k for k in cat.node_kinds
                                     if callable(is_person) and is_person(k)}


def subject_terms(store: Any, subject_kinds: Iterable[str]) -> Optional[frozenset[str]]:
    """Every label and canonical id of an open SUBJECT-kind node (case-folded), read through the
    kind index; ``None`` when it cannot be read."""
    kinds = sorted(set(subject_kinds))
    fetch = getattr(store, "fetchall", None)
    if fetch is None:
        return None
    if not kinds:
        return frozenset()
    try:
        rows = fetch("SELECT label, canonical_id FROM nodes WHERE system_to IS NULL AND kind IN ("
                     + ", ".join("?" for _ in kinds) + ")", tuple(kinds))
    except Exception:                                         # noqa: BLE001 - unreadable
        return None
    return frozenset(" ".join(str(v).casefold().split()) for r in rows for v in (r[0], r[1])
                     if v)


def screen_values(table: FactsTable, terms: Optional[frozenset[str]]) -> FactsTable:
    """A table's categorical values as anything downstream may use them (CONS standard 7: the
    subject predicate governs exposure). Codex R-029 r1 #6: a column the approved mapping marks
    subject-valued is dropped WHOLE (`FactsTable.subject_columns`); a table the mapping does not
    cover keeps none (fail closed). As a second guard, a column any of whose values is the name or
    id of a subject record in the graph is dropped too; with no way to check, none is kept."""
    if not table.values:
        return table
    if terms is None or table.subject_columns is None:
        return replace(table, values=())
    kept = tuple((c, vs) for c, vs in table.values
                 if c not in table.subject_columns
                 and not any(" ".join(v.casefold().split()) in terms for v in vs))
    return replace(table, values=kept)


def _endpoint_kind(template: Any) -> Optional[str]:
    """``person:{walnut_fk_x}`` -> ``person``; ``None`` for the row's own endpoint."""
    text = str(template or "")
    if "{walnut_row_key}" in text or ":" not in text:
        return None
    return text.split(":", 1)[0].strip() or None


def mapped_subject_columns(connection: str, *, is_subject: Any,
                           sources_dir: Any = None) -> Optional[dict[str, frozenset[str]]]:
    """``{facts table: subject-valued columns}`` from the connection's APPROVED mapping, or
    ``None`` when there is no readable mapping. A facts table is named by the source table's last
    dotted part (``core_banking.facility`` -> ``facility``), as the facts store names it.

    Subject-valued: every column of a table whose node kind (or any kind its row split may
    assign) is a subject kind; the evidence and key columns of every edge or approved link whose
    other end is a subject kind. Decided from the mapping and the vocabulary — never a name."""
    from pathlib import Path                                      # noqa: PLC0415

    if sources_dir is None:
        try:
            from databridge_mcp_ingest import paths as _paths      # noqa: PLC0415
            sources_dir = _paths.sources_dir()
        except Exception:                                         # noqa: BLE001 - unknown
            return None
    path = Path(sources_dir) / f"{connection}.mapping.yaml"
    try:
        import yaml                                               # noqa: PLC0415
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:                                             # noqa: BLE001 - unknown
        return None
    out: dict[str, frozenset[str]] = {}
    for src_table, spec in (doc.get("tables") or {}).items():
        if not isinstance(spec, dict) or spec.get("status") not in (None, "approved"):
            continue
        cols = {str(c) for c in (spec.get("columns") or [])}
        node = spec.get("node") or {}
        split = spec.get("split") or {}
        kinds = {str(node.get("kind") or "")} | {str(k) for k in
                                                  ((split.get("kinds") or {}).values())}
        if split.get("default"):
            kinds.add(str(split["default"]))
        subj: set[str] = set()
        if any(k and is_subject(k) for k in kinds):
            subj = set(cols) | {str(c) for c in (node.get("attrs") or {}).values()}
        for e in list(spec.get("edges") or []) + [
                {**(lk.get("edge") or {}), "evidence": [lk.get("column")],
                 "target": lk.get("target_kind")} for lk in (spec.get("links") or [])
                if isinstance(lk, dict)]:
            ends = {_endpoint_kind(e.get("src")), _endpoint_kind(e.get("dst")), e.get("target")}
            if any(k and is_subject(str(k)) for k in ends):
                subj |= {str(c) for c in (e.get("evidence") or []) if c}
                fk = str(e.get("fk") or "")
                if "->" in fk:
                    subj.add(fk.split("->", 1)[0].strip())
        out[str(src_table).rsplit(".", 1)[-1]] = frozenset(subj)
    return out


def build_catalogue(specs: Iterable[Any], *, facts: Any = None, documents: bool = True,
                    vocabulary: Any = None, store: Any = None,
                    sources_dir: Any = None) -> Catalogue:
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
    terms: Any = _UNSET
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
                    if cat.vocabulary_loaded:
                        people = _people_kinds(vocabulary, cat)
                        mapped = mapped_subject_columns(cid, is_subject=people.__contains__,
                                                        sources_dir=sources_dir)
                        tables = [replace(t, subject_columns=(mapped or {}).get(t.name))
                                  for t in tables]
                    if terms is _UNSET:
                        terms = (subject_terms(store, _people_kinds(vocabulary, cat))
                                 if store is not None and cat.vocabulary_loaded else None)
                    tables = [screen_values(t, terms) for t in tables]
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
                kinds = source_node_kinds(store, cid) if store is not None else None
                if kinds is None:
                    # r1 #7: unmeasured is not "every kind"; it is said, and nothing is planned
                    # against it until it is measured
                    cat.problems.append(f"{cid}: which record kinds it holds could not be "
                                        f"measured; its documents are not offered")
                conns.append(ConnectionEntry(cid, "documents", kinds=kinds))
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


def unowned_document_specs(specs: Iterable[Any], store: Any, facts: Any = None,
                           connections: Optional[Iterable[str]] = None
                           ) -> tuple[list[DirectFactsSpec], list[str]]:
    """``(specs, notes)``: one `DirectFactsSpec` per declared connection that NO adapter agent
    keeps, that has NO facts store, and whose records the graph HOLDS (measured kinds, not
    empty). E2E run 5, W-03: before an adapter agent was set up for the files source, the
    catalogue held only the database, so "invoices of customers discussed in our meeting notes"
    was answered "the catalogue has no meeting-notes source" while the graph held the meeting
    notes. The same rule as FIN-15: the graph was built from that source after a person approved
    its plan; reading it does not need an adapter agent first. The caller's grant still decides
    what is read, and a paused agent's connection is never offered this way."""
    if store is None:
        return [], []
    specs = list(specs)
    kept = {c for s in specs for c in getattr(s, "connections", ())}
    taken = {getattr(s, "id", None) for s in specs}
    out: list[DirectFactsSpec] = []
    for cid in (declared_connections() if connections is None else connections):
        cid = str(cid)
        if not cid or cid in kept or cid in taken:
            continue
        try:
            if facts is not None and facts.available(cid):
                continue                      # a facts store: `unowned_facts_specs` offers it
        except Exception:                                         # noqa: BLE001 - not offered
            continue
        kinds = source_node_kinds(store, cid)
        if not kinds:
            continue
        out.append(DirectFactsSpec(id=cid, title=f"{cid} (its graph records, read directly; no "
                                                 f"adapter agent keeps this source)",
                                   connections=(cid,)))
    notes = [f"{s.id}: no adapter agent keeps this connection; its graph records are offered "
             f"directly" for s in out]
    return out, notes


__all__ = ["DirectFactsSpec", "declared_connections", "unowned_facts_specs",
           "unowned_document_specs", "source_node_kinds", "measure_values", "index_complete",
           "subject_terms", "screen_values", "mapped_subject_columns", "FactsTable", "ConnectionEntry", "AdapterAgentEntry", "Catalogue", "build_catalogue",
           "describe_facts_tables", "describe_steady", "SteadyFacts", "DESCRIBE_ATTEMPTS",
           "registry_specs", "measure_key", "key_proof_sql"]
