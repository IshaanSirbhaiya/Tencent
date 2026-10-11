"""DSD — datasets built through DELEGATION to the per-source adapter agents (founder, 2026-10-02).

    "I want payments data for fraud, to train XGBoost"
      → a TABLE DESIGN is proposed           (model through the harness, else rules over the
                                               catalogue; validated against what is MEASURED)
      → a person edits and APPROVES it        (authenticated principals only; stored with a
                                               digest; the build runs only against that digest)
      → the main agent DELEGATES each part    (database parts: pushed-down SQL inside the closed
                                               read-only grammar — filters, `IN (...)` joins,
                                               grouped aggregates; documents: graph + passages)
      → a RAW table + receipt + pointers      (every cell has a pointer; time periods are COLUMNS
                                               people filter themselves; NO cleaning, NO
                                               imputation, NO point-in-time leakage filtering)

The design is DATA: a grain (one row per <entity>), columns (each with its meaning and the source
that supplies it), an optional label (a column, defined in words), time kept as metadata columns
and the join keys — APPROVED LINKS ONLY:

* ``lookup``    — a base column is a foreign key (or an approved C-3 link) of an APPROVED mapping:
                  the referenced row's columns are copied, each with its own pointer;
* ``aggregate`` — another table's APPROVED mapping declares a foreign key into the base: its rows
                  are counted/summed per base row in ONE pushed-down statement per chunk;
* ``documents`` — a documents connection's graph nodes linked by an ontology-declared edge to the
                  entity whose canonical id (declared by the base's APPROVED mapping) is the base
                  column's value.

Nothing here names a domain: tables, kinds, edges and labels come from the request, the measured
catalogue, the approved mappings and the pack ontology. A link whose far end is a SUBJECT (a
person) is never a merge: its cells are WITHHELD as candidates requiring human selection, per row
when the mapping splits the table's kind by a column.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import Absent, GroundedValue, SourcePointer, Withheld, WithholdingRecord
from databridge_core import decision_ledger as _dl
from databridge_core.provenance import SnapshotIdentity, Trust

_log = logging.getLogger(__name__)

MODE = "raw_delegated"
#: Said plainly on every receipt of this mode (founder brief).
RAW_STATEMENT = "raw extract; time as metadata; not leakage-filtered"
NOT_DONE = ("no data cleaning", "no imputation (a missing cell stays missing, with its reason)",
            "no point-in-time leakage filtering: every time period is a column, filter it "
            "yourself; use the labelled dataset mode for a leakage-checked training set")
DESIGN_TASK_CLASS = "agentic_orchestration"
DESIGN_PROMPT_VERSION = "dsd.design/1"
#: E2E5 F-10: the bound on ONE raw table's rows. It is the deployment's (``WALNUT_DSD_MAX_BASE_
#: ROWS``), it is checked against the source's own pushed-down COUNT of the design's selection
#: BEFORE any row is read, and the refusal names both numbers. (Before this the only bound was a
#: page ceiling — the facts store's 1,000 rows per statement — misreported as "more than 5000".)
MAX_BASE_ROWS_ENV = "WALNUT_DSD_MAX_BASE_ROWS"
DEFAULT_MAX_BASE_ROWS = 100_000
#: Rows per base PAGE (keyset-paged on the proven row key; ``WALNUT_DSD_PAGE_ROWS``). A store that
#: returns fewer per statement is paged at its own size: a page is read until the delegate says it
#: is complete, never assumed to be.
PAGE_ROWS_ENV = "WALNUT_DSD_PAGE_ROWS"
DEFAULT_PAGE_ROWS = 500
#: A delegate sub-task's own ``limit`` ceiling (`SubTask.limit`).
_SUBTASK_LIMIT = 5000
#: Values per pushed-down ``IN (...)`` statement (never more than a page).
IN_CHUNK = 500


def _env_int(name: str, default: int, *, lo: int, hi: Optional[int] = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise DesignRefused("BOUND_MISCONFIGURED", f"{name} must be a whole number of rows",
                            500) from None
    if v < lo or (hi is not None and v > hi):
        raise DesignRefused("BOUND_MISCONFIGURED", f"{name} must be between {lo} and "
                                                   f"{hi if hi is not None else 'any'} rows", 500)
    return v


def max_base_rows() -> int:
    """The deployment's bound on one raw table's rows (`MAX_BASE_ROWS_ENV`)."""
    return _env_int(MAX_BASE_ROWS_ENV, DEFAULT_MAX_BASE_ROWS, lo=1)


def page_rows() -> int:
    """Rows per base page (`PAGE_ROWS_ENV`), at most a sub-task's limit."""
    return _env_int(PAGE_ROWS_ENV, DEFAULT_PAGE_ROWS, lo=1, hi=_SUBTASK_LIMIT - 1)
#: Distinct values shown for a categorical column (more than this: not categorical).
MAX_CATEGORY_VALUES = 12
CANDIDATE_POLICY = "dsd.subject_link"
CANDIDATE_REASON = "person_candidate_requires_human_selection"
IDENTIFIER_POLICY = "dsd.identifier_screen"
IDENTIFIER_REASON = "direct_identifier_masked"
DOC_FIELDS = ("document", "text", "date")
AGG_OPS = ("count", "sum", "avg", "min", "max")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_LINK_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}(:?\d{2})?|Z)?)?$")
_WORD = re.compile(r"[a-z0-9]+")


#: R-028 TIDY #4: an approver-gate refusal answers a FIXED sentence per standard code; the gate's
#: own text (which names what was passed) stays in the server log.
APPROVER_REFUSED_MESSAGES = {
    "APPROVER_NOT_AUTHENTICATED": "a dataset design is approved by a signed-in person; the "
                                  "request's principal is not an authenticated approver",
    "MACHINE_ACTOR": "a machine actor cannot approve a dataset design; only a signed-in person can",
}


def _model_error(exc: BaseException) -> tuple[str, str]:
    """(the produced record's ``model_error``, the assumption the design carries) for a model call
    that failed: fixed text and an opaque reference, the exception in the server log only
    (R-028 TIDY #4, CONS_BRIEF standard 4)."""
    ref = f"ERR-{uuid.uuid4().hex[:10]}"
    _log.warning("[%s] the design model call failed", ref,
                 exc_info=(type(exc), exc, exc.__traceback__))
    return (f"the model call failed (reference {ref})",
            f"the model could not propose (reference {ref}); the rules drafted this design")


class DesignRefused(Exception):
    """A design operation refused, with a machine-readable code (the gateway answers 4xx)."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ================================================================================================
# the design — data, validated against the catalogue
# ================================================================================================
class _F(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _name(v: str, what: str) -> str:
    if not _NAME.match(v or ""):
        raise ValueError(f"{what} {v!r} is not a plain name")
    return v


class SourceRef(_F):
    adapter_agent: str
    connection: str
    entity: str                               # a facts table (tabular) or "" (documents)


class DesignFilter(_F):
    """A pushed-down filter on a BASE column. Values are bound, never spliced."""
    field: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "is_null", "not_null"]
    value: Any = None

    @model_validator(mode="after")
    def _shape(self) -> "DesignFilter":
        _name(self.field, "filter field")
        return self


class DesignLink(_F):
    id: str
    kind: Literal["lookup", "aggregate", "documents"]
    from_field: str                           # the BASE column the link starts from
    target: SourceRef
    to_field: Optional[str] = None            # lookup: target key; aggregate: target FK column
    entity_kind: Optional[str] = None         # documents: the kind whose canonical id = from_field
    doc_kinds: tuple[str, ...] = ()           # documents: the linked node kinds
    edge_kinds: tuple[str, ...] = ()          # documents: the ontology edges that link them
    text_contains: Optional[str] = None       # documents: an optional passage filter
    #: True when the far end is (or may be, per row) a SUBJECT kind: never merged, its cells are
    #: withheld as candidates. Copied from the catalogue, never believed from a draft.
    candidate: bool = False
    #: lookup into a table whose mapping SPLITS its kind by a column (per-row subject test)
    split: Optional[dict] = None
    evidence: str = ""
    approved_by: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self) -> "DesignLink":
        if not _LINK_ID.match(self.id):
            raise ValueError(f"link id {self.id!r} must be a short lowercase id")
        _name(self.from_field, "link from_field")
        if self.kind in ("lookup", "aggregate") and not self.to_field:
            raise ValueError(f"a {self.kind} link names to_field")
        if self.kind == "documents" and not (self.entity_kind and self.edge_kinds):
            raise ValueError("a documents link names entity_kind and edge_kinds")
        return self


class DesignColumn(_F):
    name: str
    meaning: str = ""
    role: Literal["key", "feature", "label", "time"] = "feature"
    via: Optional[str] = None                 # None = the base; else a link id
    field: str                                # source column / documents field / "*" (count)
    aggregate: Optional[Literal["count", "sum", "avg", "min", "max"]] = None
    #: WHEN this column's value became known, as far as the sources say (computed by
    #: `validate_design` from the catalogue, never taken from a draft): an EVENT time the source
    #: records, the record time of the copy it was read from, or "unknown" said plainly — so an
    #: engineer can spot a column that learns from the future. Walnut does not filter on it.
    when_known: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "DesignColumn":
        _name(self.name, "column name")
        if self.field != "*":
            _name(self.field, "column field")
        return self


class LabelSpec(_F):
    column: str
    definition: str


class TableDesign(_F):
    request: str
    grain: SourceRef
    grain_key: str
    grain_text: str = ""
    columns: tuple[DesignColumn, ...] = ()
    links: tuple[DesignLink, ...] = ()
    filters: tuple[DesignFilter, ...] = ()
    label: Optional[LabelSpec] = None
    questions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return design_digest(self)


def design_digest(design: TableDesign) -> str:
    """sha256 over the canonical JSON of the design: what was approved is exactly this."""
    body = json.dumps(design.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DesignDraft(BaseModel):
    """What a model (or the offline rules) returns. Permissive: `validate_design` is the gate."""
    model_config = ConfigDict(extra="ignore")
    grain: dict = Field(default_factory=dict)        # {adapter_agent, connection, entity}
    grain_text: str = ""
    columns: list[dict] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)  # {id, kind, from_field, target, to_field|..}
    filters: list[dict] = Field(default_factory=list)
    label: Optional[dict] = None
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ================================================================================================
# the catalogue — MEASURED from the registry, the facts stores, the approved mappings, the ontology
# ================================================================================================
@dataclass
class TableInfo:
    adapter_agent: str
    connection: str
    table: str
    columns: tuple[str, ...]
    key: Optional[str]
    rows: Optional[int] = None
    source_table: Optional[str] = None
    kind: Optional[str] = None                 # from the APPROVED mapping, else None
    canonical_id: Optional[str] = None         # the mapping's canonical id column
    subject: bool = False
    split: Optional[dict] = None
    approved_by: Optional[str] = None
    temporal: tuple[str, ...] = ()
    numeric: tuple[str, ...] = ()
    values: dict = field(default_factory=dict)  # categorical column -> distinct values
    identifiers: tuple[str, ...] = ()

    @property
    def ref(self) -> SourceRef:
        return SourceRef(adapter_agent=self.adapter_agent, connection=self.connection,
                         entity=self.table)


@dataclass
class DesignCatalogue:
    tables: dict = field(default_factory=dict)        # (agent, conn, table) -> TableInfo
    links: list = field(default_factory=list)         # approved lookup/aggregate DesignLinks
    documents: list = field(default_factory=list)     # (agent, conn) documents connections
    vocab: Any = None
    problems: list = field(default_factory=list)

    def table(self, ref: SourceRef) -> Optional[TableInfo]:
        return self.tables.get((ref.adapter_agent, ref.connection, ref.entity))

    def links_from(self, ref: SourceRef) -> list[DesignLink]:
        """Every APPROVED link that starts at ``ref``: lookups, aggregates and documents."""
        out = [lk for base, lk in self.links if _same(base, ref)]
        out += self._documents_links(ref, out)
        return out

    def _documents_links(self, ref: SourceRef, lookups: list[DesignLink]) -> list[DesignLink]:
        """Documents links from the base's canonical id (or a lookup's referenced canonical id) to
        nodes of another kind joined by an ONTOLOGY-declared edge. Evidence: the approved mapping
        that declares the canonical id, and the edge declarations."""
        t = self.table(ref)
        v = self.vocab
        if t is None or v is None or not self.documents:
            return []
        starts: list[tuple[str, str, str]] = []           # (from_field, kind, approved_by)
        if t.kind and t.canonical_id and not t.subject:
            starts.append((t.canonical_id, t.kind, t.approved_by or ""))
        for lk in lookups:
            if lk.kind != "lookup" or lk.candidate:
                continue
            far = self.table(lk.target)
            if far and far.kind and far.canonical_id == lk.to_field and not far.subject:
                starts.append((lk.from_field, far.kind, far.approved_by or ""))
        out: list[DesignLink] = []
        for from_field, kind, who in starts:
            edges, docs = linking_edges(v, kind)
            if not edges:
                continue
            for agent, conn in self.documents:
                lid = _link_id(f"docs_{from_field}", {lk.id for lk in out}
                               | {lk.id for lk in lookups})
                out.append(DesignLink(
                    id=lid, kind="documents", from_field=from_field,
                    target=SourceRef(adapter_agent=agent, connection=conn, entity=""),
                    entity_kind=kind, doc_kinds=tuple(docs), edge_kinds=tuple(edges),
                    approved_by=who or None,
                    evidence=(f"{from_field} is the canonical id of {kind!r} (approved mapping, "
                              f"approved by {who or 'unknown'}); documents in {conn} linked to "
                              f"it by the ontology's declared edge(s) {sorted(edges)}")))
        return out

    def describe(self) -> dict:
        """The catalogue as the designer sees it (names, classes, categorical values)."""
        tables = []
        for t in self.tables.values():
            tables.append({
                "adapter_agent": t.adapter_agent, "connection": t.connection, "table": t.table,
                "row_key": t.key, "rows": t.rows, "kind": t.kind, "subject": t.subject,
                "columns": list(t.columns), "time_columns": list(t.temporal),
                "numeric_columns": list(t.numeric),
                "categorical_values": {c: list(vs) for c, vs in t.values.items()},
                "identifier_columns (masked)": list(t.identifiers),
                "approved_links_from_here": [
                    {"id": lk.id, "kind": lk.kind, "from_field": lk.from_field,
                     "target": lk.target.model_dump(), "to_field": lk.to_field,
                     "entity_kind": lk.entity_kind, "edge_kinds": list(lk.edge_kinds),
                     "candidate_only (person)": lk.candidate}
                    for lk in self.links_from(t.ref)]})
        return {"tables": tables,
                "documents_connections": [{"adapter_agent": a, "connection": c}
                                          for a, c in self.documents],
                "time_rule": "time is kept as COLUMNS (event dates, record valid-from), never "
                             "as a filter",
                "record_time_column": RECORD_TIME}


def _same(a: SourceRef, b: SourceRef) -> bool:
    return (a.adapter_agent, a.connection, a.entity) == (b.adapter_agent, b.connection, b.entity)


def _link_id(seed: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", seed.lower()).strip("_") or "link"
    if not base[0].isalpha():
        base = "l_" + base
    base = base[:20]
    lid, n = base, 1
    while lid in taken:
        n += 1
        lid = f"{base[:18]}_{n}"
    return lid


def _col_name(seed: str, taken: set) -> str:
    """A column name from ``seed`` (plain, at most 63 characters), unique among ``taken``."""
    base = re.sub(r"[^A-Za-z0-9_]", "_", seed).strip("_") or "column"
    if not (base[0].isalpha() or base[0] == "_"):
        base = "c_" + base
    base = base[:60]
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base[:57]}_{n}"
    return name


def linking_edges(vocab: Any, kind: str) -> tuple[list[str], list[str]]:
    """(edge kinds, node kinds) the ONTOLOGY declares between ``kind`` and another, non-subject
    kind, with at least one endpoint named explicitly (an edge of ``*`` to ``*`` links everything
    and so says nothing)."""
    edges, docs = set(), set()
    for name, e in (getattr(vocab, "edge_kinds", {}) or {}).items():
        src, dst = tuple(e.src), tuple(e.dst)
        for here, there in ((dst, src), (src, dst)):      # `kind` at `here`, the others there
            if kind not in here and "*" not in here:
                continue
            others = [k for k in there if k not in ("*", kind) and not vocab.is_subject(k)]
            if not others:
                continue                   # only a wildcard (or people) at the far end
            edges.add(name)
            docs.update(others)
    return sorted(edges), sorted(docs)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _facts_file(connection: str, facts_dir: Optional[Path]) -> Path:
    from databridge_mcp_ingest.tabular.facts import facts_path      # noqa: PLC0415
    return facts_path(connection, facts_dir)


def _ro(path: Path) -> sqlite3.Connection:
    """The facts file, read-only three times over (mode=ro, query_only, the count authorizer)."""
    from databridge_mcp_ingest.tabular import count as cq           # noqa: PLC0415
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(cq._authorizer)
    return conn


def _qi(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _measure_table(path: Path, table: str, columns: tuple[str, ...]) -> tuple[tuple, tuple, dict]:
    """(temporal, numeric, categorical values) of one facts table, from ``_facts_columns`` when
    the store recorded classes, else measured from a sample. Read-only."""
    temporal, numeric, values = [], [], {}
    classes: dict[str, str] = {}
    try:
        conn = _ro(path)
    except sqlite3.Error:
        return (), (), {}
    try:
        try:
            for c, cls in conn.execute("SELECT column_name, class FROM _facts_columns "
                                       "WHERE facts_table = ?", (table,)):
                classes[str(c)] = str(cls or "")
        except sqlite3.Error:
            classes = {}
        sample = conn.execute(f"SELECT * FROM {_qi(table)} LIMIT 50").fetchall()
        names = list(columns)
        for i, c in enumerate(names):
            vals = [r[i] for r in sample if i < len(r) and r[i] is not None]
            cls = classes.get(c)
            if cls == "temporal" or (cls is None and vals and all(
                    isinstance(v, str) and _DATE.match(v.strip()) for v in vals)):
                temporal.append(c)
                continue
            if cls == "numeric" or (cls is None and vals and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals)):
                numeric.append(c)
                continue
            if cls in (None, "text", "boolean", "other"):
                got = conn.execute(f"SELECT DISTINCT CAST({_qi(c)} AS TEXT) FROM {_qi(table)} "
                                   f"WHERE {_qi(c)} IS NOT NULL LIMIT ?",
                                   (MAX_CATEGORY_VALUES + 1,)).fetchall()
                if 0 < len(got) <= MAX_CATEGORY_VALUES:
                    values[c] = tuple(sorted(str(g[0]) for g in got))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return tuple(temporal), tuple(numeric), values


def _source_tables(path: Path) -> dict[str, str]:
    """facts table -> the source table it was copied from (``_facts_tables``), when recorded."""
    try:
        conn = _ro(path)
    except sqlite3.Error:
        return {}
    try:
        return {str(ft): str(st) for ft, st in conn.execute(
            "SELECT facts_table, source_table FROM _facts_tables WHERE facts_table IS NOT NULL")}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _mapping_for(conn: str, source_table: Optional[str], facts_table: str, vocab: Any):
    """The EFFECTIVE (approval-verified) mapping entry for one facts table, or None."""
    try:
        from databridge_mcp_ingest.mapping import effective_mapping, load_mapping  # noqa: PLC0415
        m = effective_mapping(load_mapping(conn), vocab)
    except Exception:                                             # noqa: BLE001 - no mapping
        return None, None
    for ref, tm in m.tables.items():
        if ref == source_table or (source_table is None and ref.split(".")[-1] == facts_table):
            return ref, tm
    return None, None


def _is_subject(vocab: Any, kind: Optional[str]) -> bool:
    """THE subject predicate (R-028 standard 7): `mapping.is_subject_kind` = `vocab.is_subject`,
    failing closed. Persons are a subset of subjects."""
    if not kind:
        return False
    try:
        from databridge_mcp_ingest.mapping import is_subject_kind      # noqa: PLC0415
        return bool(is_subject_kind(vocab, kind))
    except Exception:                                             # noqa: BLE001 - fail closed
        return True


def _split_subject(vocab: Any, split: Optional[dict]) -> bool:
    if not split:
        return False
    kinds = [*(split.get("kinds") or {}).values(), split.get("default")]
    return any(_is_subject(vocab, k) for k in kinds if k)


def _fk_end(template: str) -> Optional[tuple[str, str]]:
    """``kind:{walnut_fk_<col>}`` -> (kind, col)."""
    m = re.match(r"^([A-Za-z0-9_]+):\{walnut_fk_([A-Za-z0-9_]+)\}$", str(template or ""))
    return (m.group(1), m.group(2)) if m else None


def _row_end(template: str) -> Optional[str]:
    m = re.match(r"^([A-Za-z0-9_]+):\{walnut_row_key\}$", str(template or ""))
    return m.group(1) if m else None


def build_design_catalogue(service: Any, *, vocab: Any = None,
                           facts_dir: Optional[Path] = None,
                           allowed: Optional[set] = None) -> DesignCatalogue:
    """From the federated service's MEASURED catalogue (adapter agents, connections, facts tables
    and their proven row keys), the facts stores (classes, categorical values), the APPROVED
    mappings (kinds, canonical ids, foreign keys, approved links) and the pack ontology."""
    if vocab is None:
        try:
            from databridge_graph.packs import active_vocabulary          # noqa: PLC0415
            vocab = active_vocabulary()
        except Exception as e:                                    # noqa: BLE001 - reported
            vocab = None
            problem = f"vocabulary unavailable: {e}"
        else:
            problem = None
    else:
        problem = None
    cat = DesignCatalogue(vocab=vocab)
    if problem:
        cat.problems.append(problem)
    fed = service.catalogue
    cat.problems += list(getattr(fed, "problems", []) or [])
    idents = set()
    if vocab is not None:
        for s in (getattr(vocab, "subject_kinds", {}) or {}).values():
            idents.update(_norm(i) for i in getattr(s, "identifiers", ()) or ())
    by_source: dict[tuple[str, str], tuple[str, Any]] = {}       # (conn, source_table) -> info
    for agent in fed.agents.values():
        for ce in agent.connections:
            if allowed is not None and ce.connection not in allowed:
                continue                       # the caller's grant: an ungranted source is unseen
            if ce.channel == "documents":
                cat.documents.append((agent.id, ce.connection))
                continue
            path = _facts_file(ce.connection, facts_dir)
            sources = _source_tables(path)
            for ft in ce.tables:
                temporal, numeric, values = _measure_table(path, ft.name, ft.columns)
                src = sources.get(ft.name)
                ref, tm = _mapping_for(ce.connection, src, ft.name, vocab)
                approved = tm is not None and tm.status == "approved" and tm.node
                kind = (tm.node or {}).get("kind") if approved else None
                split = (tm.split if approved else None) or None
                info = TableInfo(
                    adapter_agent=agent.id, connection=ce.connection, table=ft.name,
                    columns=tuple(ft.columns), key=ft.key, source_table=ref or src,
                    kind=kind, canonical_id=((tm.node or {}).get("canonical_id")
                                             if approved else None),
                    subject=_is_subject(vocab, kind) or _split_subject(vocab, split),
                    split=split,
                    approved_by=((tm.approval or {}).get("principal_id") if approved else None),
                    temporal=temporal, numeric=numeric,
                    values={c: v for c, v in values.items() if _norm(c) not in idents},
                    identifiers=tuple(c for c in ft.columns if _norm(c) in idents))
                cat.tables[(agent.id, ce.connection, ft.name)] = info
                if info.source_table:
                    by_source[(ce.connection, info.source_table)] = (ft.name, info)
                if tm is not None and approved:
                    by_source.setdefault((ce.connection, ft.name), (ft.name, info))
                    info_tm = tm
                    setattr(info, "_tm", info_tm)
    # approved foreign keys / links -> lookups (from the referencing table) and aggregates (into
    # the referenced table)
    for info in list(cat.tables.values()):
        tm = getattr(info, "_tm", None)
        if tm is None:
            continue
        for e in tm.edges or []:
            fk = str(e.get("fk") or "")
            if not fk or ";" in fk:
                continue                     # association edges are not a single-column join
            end = _fk_end(e.get("src")) or _fk_end(e.get("dst"))
            if end is None:
                continue
            far_kind, col = end
            ref = e.get("ref") or {}
            if ref:
                tconn, tsrc = str(ref.get("source") or ""), str(ref.get("table") or "")
                how = f"approved link {col} -> {tconn}:{tsrc}"
            else:
                tconn = info.connection
                tsrc = fk.split("->", 1)[1].strip() if "->" in fk else ""
                how = f"declared foreign key {col} -> {tsrc}"
            target = by_source.get((tconn, tsrc))
            if target is None or col not in info.columns:
                cat.problems.append(f"{info.connection}/{info.table}: {how} has no readable "
                                    f"facts table; not offered")
                continue
            far = target[1]
            to_field = (ref.get("key") if ref else None) or far.key
            if not to_field or to_field != far.key:
                cat.problems.append(f"{info.connection}/{info.table}: {how} does not reference "
                                    f"the target's proven row key; not offered")
                continue
            who = info.approved_by
            far_subject = _is_subject(vocab, far_kind) or far.subject
            lid = _link_id(far.table, {lk.id for _b, lk in cat.links if _same(_b, info.ref)})
            cat.links.append((info.ref, DesignLink(
                id=lid, kind="lookup", from_field=col, target=far.ref, to_field=to_field,
                candidate=far_subject, split=far.split,
                evidence=f"{how} in the mapping of {info.source_table} approved by {who}",
                approved_by=who)))
            if info.key:
                lid2 = _link_id(info.table,
                                {lk.id for _b, lk in cat.links if _same(_b, far.ref)})
                cat.links.append((far.ref, DesignLink(
                    id=lid2, kind="aggregate", from_field=far.key, target=info.ref,
                    to_field=col, candidate=info.subject,
                    evidence=f"{how} in the mapping of {info.source_table} approved by {who}: "
                             f"{info.table} rows grouped per {far.table} row",
                    approved_by=who)))
    return cat


# ================================================================================================
# validation — a draft (model, rules or a person's edit) becomes a design, or questions
# ================================================================================================
RECORD_TIME = "_record_as_of"


def _approved(cat: DesignCatalogue, base: SourceRef, draft: dict) -> Optional[DesignLink]:
    """The catalogue's APPROVED link matching a draft link — evidence, candidate flag and split
    come from the catalogue, never from the draft."""
    for lk in cat.links_from(base):
        if lk.kind != draft.get("kind") or lk.from_field != draft.get("from_field"):
            continue
        tgt = draft.get("target") or {}
        if lk.kind in ("lookup", "aggregate"):
            if (tgt.get("connection"), tgt.get("entity")) != (lk.target.connection,
                                                              lk.target.entity):
                continue
            if draft.get("to_field") not in (None, lk.to_field):
                continue
        else:
            if tgt.get("connection") != lk.target.connection:
                continue
            if draft.get("entity_kind") not in (None, lk.entity_kind):
                continue
        upd: dict = {"id": draft.get("id") or lk.id}
        if lk.kind == "documents":
            ek = tuple(draft.get("edge_kinds") or lk.edge_kinds)
            dk = tuple(draft.get("doc_kinds") or lk.doc_kinds)
            if not set(ek) <= set(lk.edge_kinds) or not set(dk) <= set(lk.doc_kinds):
                return None
            upd.update(edge_kinds=ek, doc_kinds=dk,
                       text_contains=(str(draft["text_contains"]).strip() or None)
                       if draft.get("text_contains") else None)
        try:
            return DesignLink.model_validate({**lk.model_dump(), **upd})
        except Exception:                                         # noqa: BLE001 - not a link
            return None
    return None


def validate_design(draft: DesignDraft, cat: DesignCatalogue, *, request: str,
                    time_scope: bool = False) -> TableDesign:
    """The gate. Every name is checked against the MEASURED catalogue; a link is accepted only if
    it is an APPROVED one; time columns are made metadata. Whatever cannot be honoured becomes a
    QUESTION (a design with questions cannot be approved).

    A filter on a TIME column (E2E5 F-10: never dropped silently):

    * ``time_scope=False`` — a filter a MODEL drafted is not applied, and the design says so and
      how to apply it (time stays a column; the person decides);
    * ``time_scope=True`` — a filter a PERSON put in an edit (`apply_edit`), or one already in an
      approved design's own filters (re-validation), is APPLIED as a row scope, pushed down to the
      source like any other filter, and the design says so. It is a selection the person chose,
      not leakage filtering: every time column is still a column."""
    qs = [str(q) for q in draft.questions if str(q).strip()]
    notes = [str(a) for a in draft.assumptions if str(a).strip()]
    g = draft.grain or {}
    base_ref = None
    base = None
    # R-027 f1 #8: an incomplete source reference never picks "the first" table of that name:
    # exactly one catalogue table must match, or the design asks which one
    matches = [t for t in cat.tables.values() if t.table == g.get("entity")
               and (not g.get("connection") or t.connection == g.get("connection"))
               and (not g.get("adapter_agent") or t.adapter_agent == g.get("adapter_agent"))]
    if len(matches) == 1:
        base, base_ref = matches[0], matches[0].ref
    elif len(matches) > 1:
        qs.append(f"Which {g.get('entity')!r}? Several sources hold a table of that name: "
                  + ", ".join(sorted(f"{t.adapter_agent}/{t.connection}" for t in matches))
                  + "; name the adapter agent and connection")
    if base is None:
        if not matches:
            qs.append(f"Which table should give one row per what? {g.get('entity')!r} is not a "
                      f"readable table" if g.get("entity") else
                      "Which table should give one row per what?")
        dummy = SourceRef(adapter_agent=str(g.get("adapter_agent") or "?"),
                          connection=str(g.get("connection") or "?"),
                          entity=str(g.get("entity") or "?"))
        return TableDesign(request=request, grain=dummy, grain_key="?",
                           grain_text=draft.grain_text, questions=tuple(dict.fromkeys(qs)),
                           assumptions=tuple(notes))
    if base.key is None:
        qs.append(f"{base.table} has no column proven to name exactly one row, so it cannot be "
                  f"the grain")
    if base.subject:
        notes.append(f"{base.table} holds people (a subject kind{' on some rows' if base.split else ''}"
                     f"): every person row's values are WITHHELD, all but its row key and record "
                     f"time, pending a person's candidate selection")
    links: list[DesignLink] = []
    for d in draft.links:
        lk = _approved(cat, base_ref, d)
        if lk is None:
            qs.append(f"The link {d.get('id') or d.get('from_field')!r} is not an approved link "
                      f"from {base.table}; approve it on the Sources page (or remove it)")
            continue
        if lk.id in {x.id for x in links}:
            qs.append(f"link id {lk.id!r} is used twice")
            continue
        links.append(lk)
    by_id = {lk.id: lk for lk in links}
    cols: list[DesignColumn] = []
    for c in draft.columns:
        try:
            col = DesignColumn.model_validate({k: v for k, v in c.items()
                                               if k in DesignColumn.model_fields})
        except Exception as e:                                    # noqa: BLE001 - a question
            qs.append(f"column {c.get('name')!r} is not well formed: {e}")
            continue
        why = _column_problem(col, base, by_id, cat)
        if why:
            qs.append(why)
            continue
        if col.name in {x.name for x in cols}:
            qs.append(f"two columns are named {col.name!r}; rename one")
            continue
        # time is METADATA: a temporal source column is a time column, whatever the draft said
        src_temporal = _is_temporal(col, base, by_id, cat)
        if src_temporal and col.role not in ("time", "label"):
            col = col.model_copy(update={"role": "time"})
        if col.role == "time" and not src_temporal:
            col = col.model_copy(update={"role": "feature"})
        col = col.model_copy(update={"when_known": when_known(col, base, by_id, cat)})
        cols.append(col)
    if base.key and not any(c.via is None and c.field == base.key for c in cols):
        k = DesignColumn(name=base.key if base.key not in {c.name for c in cols}
                         else f"{base.table}_{base.key}", role="key",
                         field=base.key, meaning=f"the row key of {base.table}")
        cols.insert(0, k.model_copy(update={"when_known": when_known(k, base, by_id, cat)}))
    if not any(c.via is None and c.field == RECORD_TIME for c in cols):
        r = DesignColumn(name=RECORD_TIME if RECORD_TIME not in {c.name for c in cols}
                         else f"{base.table}{RECORD_TIME}", role="time",
                         field=RECORD_TIME,
                         meaning=f"record valid-from: when the copy of {base.table} "
                                 f"this row was read from was extracted")
        cols.append(r.model_copy(update={"when_known": when_known(r, base, by_id, cat)}))
    used = {c.via for c in cols if c.via}
    links = [lk for lk in links if lk.id in used]
    filters: list[DesignFilter] = []
    for f in draft.filters:
        try:
            df = DesignFilter.model_validate(f)
        except Exception as e:                                    # noqa: BLE001 - a question
            qs.append(f"filter {f!r} is not well formed: {e}")
            continue
        if df.field not in base.columns:
            qs.append(f"filter on {df.field!r}: {base.table} has no such column")
            continue
        if df.field in base.temporal:
            if not time_scope:
                notes.append(f"the filter {df.field} {df.op} {df.value!r} was NOT applied: time "
                             f"is kept as a column for you to filter. To keep only those rows, "
                             f"add it as a filter in an edit: it is then applied as a row scope")
                continue
            dropped = f"the filter {df.field} {df.op} {df.value!r} was NOT applied"
            notes = [n for n in notes if not n.startswith(dropped)]
            notes.append(f"the filter {df.field} {df.op} {df.value!r} is APPLIED as a row scope "
                         f"(pushed down to the source), as the editor asked; time stays a column "
                         f"and nothing else is filtered by time")
        filters.append(df)
    label = None
    lc = str((draft.label or {}).get("column") or "") if draft.label else ""
    # R-027 f1 #5: ONE label, the LabelSpec's, and only a field a source RECORDS. A column that
    # merely claims the role is a feature again; a computed column cannot be a label.
    for c in [c for c in cols if c.role == "label" and c.name != lc]:
        notes.append(f"{c.name!r} was marked a label without being THE label; it is a feature")
    cols = [c.model_copy(update={"role": "feature"}) if c.role == "label" and c.name != lc
            else c for c in cols]
    if lc:
        col = next((c for c in cols if c.name == lc), None)
        why = _label_problem(col, base, by_id) if col is not None else \
            f"the label {lc!r} is not a column of this design"
        if why:
            qs.append(why + "; choose a column a source records, or no label")
            cols = [c.model_copy(update={"role": "feature"}) if c.name == lc and
                    c.role == "label" else c for c in cols]
        else:
            label = LabelSpec(column=lc, definition=str(draft.label.get("definition") or
                                                         f"the value of {lc}"))
            cols = [c.model_copy(update={"role": "label"}) if c.name == lc else c for c in cols]
    return TableDesign(request=request, grain=base_ref, grain_key=base.key or "?",
                       grain_text=draft.grain_text or f"one row per {base.table} row",
                       columns=tuple(cols), links=tuple(links), filters=tuple(filters),
                       label=label, questions=tuple(dict.fromkeys(qs)),
                       assumptions=tuple(dict.fromkeys(notes)))


def _label_problem(col: DesignColumn, base: TableInfo, links: dict) -> Optional[str]:
    """Why a column cannot be the label (None when it can): a label is a value a SOURCE RECORDS —
    the grain's own field, or a lookup's — never an aggregate, a document count, the row key or
    the record time."""
    if col.aggregate is not None:
        return f"the label {col.name!r} is computed ({col.aggregate}), not recorded by a source"
    if col.field in (RECORD_TIME, "*") or (col.via is None and col.field == base.key):
        return f"the label {col.name!r} is the row key or the record time, not an outcome"
    if col.via is not None and links.get(col.via) is not None and \
            links[col.via].kind != "lookup":
        return f"the label {col.name!r} comes through a {links[col.via].kind} link"
    return None


def _column_problem(col: DesignColumn, base: TableInfo, links: dict,
                    cat: DesignCatalogue) -> Optional[str]:
    if col.via is None:
        if col.aggregate:
            return f"column {col.name!r}: a base column is not aggregated"
        if col.field != RECORD_TIME and col.field not in base.columns:
            return f"column {col.name!r}: {base.table} has no column {col.field!r}"
        return None
    lk = links.get(col.via)
    if lk is None:
        return f"column {col.name!r} names link {col.via!r}, which is not an approved link here"
    if lk.kind == "documents":
        if col.field not in DOC_FIELDS:
            return f"column {col.name!r}: documents supply {list(DOC_FIELDS)}, not {col.field!r}"
        if col.aggregate not in (None, "count"):
            return f"column {col.name!r}: documents can be counted, not {col.aggregate}"
        return None
    far = cat.table(lk.target)
    if far is None:
        return f"column {col.name!r}: {lk.target.entity} is not readable"
    if lk.kind == "lookup":
        if col.aggregate:
            return f"column {col.name!r}: a lookup copies one value; it is not aggregated"
        if col.field not in far.columns:
            return f"column {col.name!r}: {far.table} has no column {col.field!r}"
        return None
    if not col.aggregate:
        return (f"column {col.name!r}: {far.table} has many rows per {base.table} row; name an "
                f"aggregate ({', '.join(AGG_OPS)})")
    if col.field == "*":
        return None if col.aggregate == "count" else \
            f"column {col.name!r}: {col.aggregate} needs a column, not '*'"
    if col.field not in far.columns:
        return f"column {col.name!r}: {far.table} has no column {col.field!r}"
    if lk.candidate and col.aggregate != "count":
        return (f"column {col.name!r}: {far.table} holds people; only a count of them is "
                f"offered (their values are never aggregated across persons)")
    return None


def when_known(col: DesignColumn, base: TableInfo, links: dict, cat: DesignCatalogue) -> str:
    """WHEN a column's value became known, from what the sources themselves record — or
    "unknown", said plainly. Computed from the catalogue; no column NAME is read as a meaning (a
    source declares a valid-from column, or nothing is claimed)."""
    rec = ("the copy it was read from was extracted at the row's "
           f"{RECORD_TIME} (the latest instant it is known to have held)")
    if col.field == RECORD_TIME and col.via is None:
        return "record time: the extraction instant of the source copy (when it was recorded)"
    if col.via is None:
        if col.field in base.temporal:
            return "event time: the value IS a date/time the source records for this row"
        others = [c for c in base.temporal if c != col.field]
        return ("unknown: the source does not record when this value was set or last changed; "
                + rec + (f". This row's own time columns: {others}" if others else "")
                + ". It may have been set AFTER those times")
    lk = links.get(col.via)
    if lk is None:
        return "unknown"
    if lk.kind == "documents":
        if col.field == "date" and col.aggregate is None:
            return "event time: the linked document's own grounded date"
        return ("known when the linked document(s) existed; each document's own date is its "
                "event time (a document dated after this row's times is counted too)")
    far = cat.table(lk.target)
    temporal = list(far.temporal) if far else []
    if lk.kind == "lookup":
        if far and col.field in far.temporal:
            return f"event time recorded on the linked {far.table} row"
        return (f"unknown: the linked {lk.target.entity} row's CURRENT value as extracted; the "
                f"source does not record when it was set"
                + (f" (that row's time columns: {temporal})" if temporal else ""))
    if col.aggregate in ("min", "max") and far and col.field in far.temporal:
        return f"event time: the {col.aggregate} of the linked {far.table} rows' {col.field}"
    return (f"computed over ALL linked {lk.target.entity} rows in the extracted copy, whatever "
            f"their dates"
            + (f" (their time columns: {temporal}); rows dated after this row's times are "
               f"included" if temporal else "; the source records no time for those rows"))


def _is_temporal(col: DesignColumn, base: TableInfo, links: dict, cat: DesignCatalogue) -> bool:
    if col.field == RECORD_TIME:
        return True
    if col.via is None:
        return col.field in base.temporal
    lk = links.get(col.via)
    if lk is None:
        return False
    if lk.kind == "documents":
        return col.field == "date" and col.aggregate is None
    far = cat.table(lk.target)
    return bool(far and col.field in far.temporal and col.aggregate in (None, "min", "max"))


# ================================================================================================
# proposing — the model through the harness, else rules over the request and the catalogue
# ================================================================================================
_PROMPT = """You design a RAW dataset table for the request below, using ONLY the catalogue.
Return JSON: {{"grain": {{"adapter_agent","connection","entity"}}, "grain_text": "one row per ...",
"columns": [{{"name","meaning","role": key|feature|label|time,"via": null|<link id>,"field",
"aggregate": null|count|sum|avg|min|max}}], "links": [<copy approved links you use, by id>],
"filters": [{{"field","op","value"}}], "label": null|{{"column","definition"}},
"questions": [], "assumptions": []}}.
Rules: join only through approved_links_from_here; time columns are metadata columns, NEVER
filters; ask a question rather than guess. No cleaning, no imputation.
CATALOGUE
{catalogue}
REQUEST
{request}{answers}
"""


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text).lower())


def _stem(w: str) -> str:
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def _tokens(name: str) -> set[str]:
    return {_stem(w) for w in _words(name.replace("_", " "))}


#: English words that introduce WHAT the dataset is for (grammar, not a domain).
_TARGET_CUES = ("for", "predict", "predicting", "detect", "detecting", "flag", "flagging",
                "classify", "label", "target", "whether", "which")
#: English function words: never a target (grammar, not a domain).
_STOP = frozenset("a an the to of and or with in on at by be is are was will which whether that "
                  "this these those it its as from into per each every all any some my our "
                  "their i we you me us data dataset table rows row".split())
#: English words that say the request wants a supervised training set.
_LABEL_INTENT = ("train", "training", "predict", "predicting", "classify", "classifier",
                 "label", "labelled", "labeled", "target", "detect", "detecting", "model")


def rules_draft(request: str, cat: DesignCatalogue) -> DesignDraft:
    """The offline designer: a rule over the request's words and the MEASURED catalogue (no model,
    no domain names). The base is the table whose name and columns share the most words with the
    request; every approved link from it is used; documents are joined when the request names
    one of the linked node kinds; the label is the column (or categorical value) the request's
    target words name, else a QUESTION."""
    words = [_stem(w) for w in _words(request)]
    wset = set(words)
    scored = []
    for t in cat.tables.values():
        name_hits = len(_tokens(t.table) & wset)
        col_hits = sum(1 for c in t.columns if _tokens(c) & wset)
        val_hits = sum(1 for vs in t.values.values() for v in vs if _tokens(v) & wset)
        scored.append(((name_hits * 4 + col_hits + val_hits), name_hits, t))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2].table))
    qs: list[str] = []
    if not scored or scored[0][0] == 0:
        return DesignDraft(questions=["Which table should give one row per what? No table's "
                                      "name or columns match the request"])
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        qs.append(f"One row per what? {scored[0][2].table!r} and {scored[1][2].table!r} match "
                  f"the request equally")
    base = scored[0][2]
    cols: list[dict] = [{"name": c, "field": c, "via": None,
                         "role": "key" if c == base.key else
                         ("time" if c in base.temporal else "feature"),
                         "meaning": f"{base.table}.{c}"} for c in base.columns]
    links: list[dict] = []
    taken = set(base.columns)
    for lk in cat.links_from(base.ref):
        if lk.kind == "documents":
            named = set(_tokens(" ".join(lk.doc_kinds))) | _tokens(lk.target.connection)
            if not (named & wset):
                continue
            nm = _col_name(f"{lk.id}_count", taken)
            cols.append({"name": nm, "via": lk.id, "field": "document", "aggregate": "count",
                         "meaning": f"how many {'/'.join(lk.doc_kinds)} in "
                                    f"{lk.target.connection} are linked to this row's "
                                    f"{lk.entity_kind} ({', '.join(lk.edge_kinds)})"})
            taken.add(nm)
        elif lk.kind == "lookup":
            far = cat.table(lk.target)
            for c in far.columns if far else ():
                if c == lk.to_field:
                    continue
                nm = c if c not in taken else _col_name(f"{far.table}_{c}", taken)
                taken.add(nm)
                cols.append({"name": nm, "via": lk.id, "field": c,
                             "meaning": f"{far.table}.{c} of the row {lk.from_field} names"})
        else:
            far = cat.table(lk.target)
            if far is None:
                continue
            nm = _col_name(f"{far.table}_count", taken)
            taken.add(nm)
            cols.append({"name": nm, "via": lk.id, "field": "*", "aggregate": "count",
                         "meaning": f"how many {far.table} rows have {lk.to_field} = this "
                                    f"row's {lk.from_field}"})
            if not lk.candidate:
                for c in far.numeric:
                    if c in (far.key, lk.to_field):
                        continue
                    nm = _col_name(f"{far.table}_{c}_sum", taken)
                    taken.add(nm)
                    cols.append({"name": nm, "via": lk.id, "field": c, "aggregate": "sum",
                                 "meaning": f"total {far.table}.{c} over those rows"})
        links.append({"id": lk.id, "kind": lk.kind, "from_field": lk.from_field,
                      "target": lk.target.model_dump(), "to_field": lk.to_field,
                      "entity_kind": lk.entity_kind})
    label = None
    if wset & {_stem(w) for w in _LABEL_INTENT}:
        targets = []
        intent = {_stem(w) for w in _LABEL_INTENT} | set(_TARGET_CUES)
        for clause in re.split(r"[,;.]", request.lower()):
            ws = _words(clause)
            for i, w in enumerate(ws):
                if w in _TARGET_CUES:
                    for x in ws[i + 1:i + 4]:
                        if x in _TARGET_CUES:
                            break
                        if x not in _STOP and _stem(x) not in intent:
                            targets.append(_stem(x))
        targets = [t for t in targets if t not in _tokens(base.table)] or targets
        hit = None
        for c in base.columns:
            if c != base.key and _tokens(c) & set(targets):
                hit = (c, f"the value of {base.table}.{c}")
                break
        if hit is None:
            for c, vs in base.values.items():
                named = [v for v in vs if _tokens(v) & set(targets)]
                if named:
                    hit = (c, f"{base.table}.{c} as it stands; the request names the value(s) "
                              f"{sorted(named)} (raw: the column is not recoded)")
                    break
        notes = ["drafted by rules over the catalogue (no model)"]
        if hit:
            label = {"column": hit[0], "definition": hit[1]}
        else:
            # never invented: the table is still a table; the gap is said, not filled
            notes.append(f"NO LABEL: none of {base.table}'s columns or values records "
                         f"{', '.join(sorted(set(targets))) or 'the outcome'}; no label is "
                         f"invented. If a source records it, add that column and mark it the "
                         f"label")
        return DesignDraft(grain=base.ref.model_dump(),
                           grain_text=f"one row per {base.table} row", columns=cols,
                           links=links, label=label, questions=qs, assumptions=notes)
    return DesignDraft(grain=base.ref.model_dump(), grain_text=f"one row per {base.table} row",
                       columns=cols, links=links, label=label, questions=qs,
                       assumptions=["drafted by rules over the catalogue (no model)"])


def propose_design(request: str, cat: DesignCatalogue, *, harness: Any = None,
                   answers: Optional[dict] = None) -> tuple[TableDesign, dict]:
    """(design, produced_by). A model proposes when a harness is given; whatever it returns is
    validated by `validate_design`. A model that cannot answer is said, and the rules draft."""
    produced = {"agent": "dsd_designer", "task_class": None, "model": None,
                "prompt_version": None, "by": "rules", "at": _now()}
    text = request + "".join(f"\nANSWER {k}: {v}" for k, v in (answers or {}).items())
    draft = None
    if harness is not None:
        prompt = _PROMPT.format(catalogue=json.dumps(cat.describe(), sort_keys=True,
                                                     default=str),
                                request=request,
                                answers="".join(f"\nAnswer to {k}: {v}"
                                                for k, v in (answers or {}).items()))
        try:
            res = harness.complete_structured(DESIGN_TASK_CLASS,
                                              [{"role": "user", "content": prompt}],
                                              DesignDraft, strict=False)
            draft = res.parsed
            ev = getattr(res, "event", None)
            # E2E5 W-10: the harness records the ROUTED model on its event as `model_used`
            produced.update(task_class=getattr(ev, "task_class", None) or DESIGN_TASK_CLASS,
                            by="model",
                            model=getattr(ev, "model_used", None) or getattr(ev, "model", None),
                            prompt_version=DESIGN_PROMPT_VERSION)
        except Exception as e:                                    # noqa: BLE001 - said
            produced["model_error"], model_note = _model_error(e)
    if draft is None:
        draft = rules_draft(text, cat)
    design = validate_design(draft, cat, request=request)
    if produced.get("model_error"):
        design = design.model_copy(update={"assumptions": (*design.assumptions, model_note)})
    return design, produced


def question_id(text: str) -> str:
    """A question's stable id: ``q:`` + sha1 of its text."""
    return "q:" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12]


def question_items(design: TableDesign) -> list[dict]:
    return [{"id": question_id(q), "text": q} for q in design.questions]


def apply_edit(design: TableDesign, edit: dict, cat: DesignCatalogue) -> TableDesign:
    """A person's edit — rename/add/remove columns, change the grain, change the label, change
    the filters — re-validated like any draft. Links are re-derived from the catalogue.

    R-027 f1 #7: an OPEN QUESTION stays open across edits until it is explicitly ANSWERED
    (``answers: {question id: text}``); the answer is kept in the design's assumptions. An edit
    that happens to remove what prompted a question does not answer it."""
    answers = edit.get("answers") or {}
    if not isinstance(answers, dict):
        raise DesignRefused("BAD_REQUEST", "answers is {question id: text}", 422)
    open_ids = {question_id(q): q for q in design.questions}
    unknown = [k for k in answers if k not in open_ids]
    if unknown:
        raise DesignRefused("UNKNOWN_QUESTION", f"{unknown} are not open questions of this "
                                                f"design", 422)
    blank = [k for k, v in answers.items() if not str(v or "").strip()]
    if blank:
        raise DesignRefused("BAD_REQUEST", f"an answer says something: {blank}", 422)
    still_open = [q for qid, q in open_ids.items() if qid not in answers]
    answered = [f"answered by the editor: {open_ids[k]} -> {str(v).strip()}"
                for k, v in answers.items()]
    body = design.model_dump(mode="json")
    for k in ("grain", "grain_text", "columns", "links", "filters", "label"):
        if k in edit:
            body[k] = edit[k]
    if "grain" in edit and "links" not in edit:
        body["links"] = []                     # a new grain starts from its own approved links
    draft = DesignDraft(grain=body.get("grain") or {}, grain_text=body.get("grain_text") or "",
                        columns=list(body.get("columns") or []),
                        links=list(body.get("links") or []),
                        filters=list(body.get("filters") or []), label=body.get("label"),
                        questions=still_open,
                        assumptions=[*design.assumptions, *answered])
    # E2E5 F-10: a filter a PERSON puts in an edit is applied, a time filter included (as a row
    # scope, said in the design) — never dropped silently
    return validate_design(draft, cat, request=design.request, time_scope=True)


# ================================================================================================
# the design store — proposed → edited → approved (authenticated principals only)
# ================================================================================================
ANCHOR_ENV = "WALNUT_DESIGN_ANCHORS"
#: The ONE public text of an unavailable key (R-028 standard 4: no paths, no exception text; the
#: details go to the server log).
_KEY_UNAVAILABLE = ("the deployment cannot seal or verify design decisions: its approval key or "
                    "anchor ledger is not configured safely (see the server log)")


def anchor_path() -> Path:
    """The deployment's append-only design anchor ledger: ``WALNUT_DESIGN_ANCHORS``, else beside
    the other sealed approval logs in the sources directory — never under the materialise root."""
    raw = os.environ.get(ANCHOR_ENV, "").strip()
    if raw:
        return Path(raw)
    from databridge_mcp_ingest import paths                          # noqa: PLC0415
    return paths.sources_dir() / "dataset_designs.anchors.jsonl"


class AnchorLedger(_dl.AnchorLedger):
    """The design anchor ledger (R-028: the common `databridge_core.decision_ledger.AnchorLedger`,
    one sealed, hash-chained line per log head). Defaults to `anchor_path()`. A design log must
    EQUAL its latest anchor: shorter was rolled back, longer has an unanchored tail."""

    def __init__(self, key: bytes, path: Optional[Path] = None) -> None:
        super().__init__(key, Path(path) if path else anchor_path())


class DesignStore:
    """``<root>/designs/<id>/``: IMMUTABLE revisions and an append-only, SEALED event log.

    R-027 f1 #1/#2. A design's authority is never a mutable file:

    * ``rev-<n>.json`` — one revision per proposal or edit, created exclusively (never rewritten);
      its design must hash to the digest its event names;
    * ``events.jsonl`` — ``proposed`` / ``edited`` / ``approved`` / ``built`` events, each HMAC-sealed
      under the DEPLOYMENT APPROVAL KEY (the key the plan and screen approvals use, which does not
      live under this directory) and hash-chained (``seq``, ``prev``). A line that does not verify,
      or a gap in the chain, fails the WHOLE design closed: nothing in it is honoured.

    The state (current revision, digest, approval) is DERIVED from the verified log every time it is
    read. Edits and approvals are compare-and-set on the revision number under a per-design lock; a
    build appends a ``built`` event and never rewrites design state.

    R-028: the log IS the common decision ledger (`databridge_core.decision_ledger.DecisionLedger`,
    in the format these logs were first written in: ``record_digest`` / genesis ``""``), anchored
    in `anchor_path()` by design id. The anchor must EQUAL the log (#4): an unanchored tail is not
    trusted; the next change of the design completes an interrupted append explicitly."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "designs"

    # --- plumbing -------------------------------------------------------------------------------
    def _ddir(self, design_id: str) -> Optional[Path]:
        if not re.match(r"^dsg-[0-9a-f]{12}$", design_id or ""):
            return None
        return self.dir / design_id

    def _lock(self, design_id: str):
        """The design's lock: in-process (an RLock) AND cross-process (an advisory lock on
        ``designs/<id>.lock``, R-027 f2 #6). Reentrant within one thread, so a publication can
        hold it across the final check, the rename and the ``built`` event (f2 #2)."""
        return self.transaction(design_id)

    @__import__("contextlib").contextmanager
    def transaction(self, design_id: str, *, timeout: float = 120.0):
        """The design lock: cross-process and re-entrant within a thread (R-028: the ONE
        `databridge_core.decision_ledger.file_lock`). Global order: ... plan -> mapping -> DESIGN
        -> ledger append."""
        if self._ddir(design_id) is None:
            raise DesignRefused("DESIGN_NOT_FOUND", f"no design {design_id!r}", 404)
        with _dl.file_lock(self.dir / f"{design_id}.lock", timeout=timeout,
                           on_timeout=lambda _m: DesignRefused(
                               "DESIGN_BUSY", "the design is being changed or published; try "
                                              "again", 409)):
            yield

    def _key(self) -> bytes:
        """The deployment approval key (`mcp_ingest.screen`), refused when it lies under this
        store (whoever can write an event could then also seal it)."""
        from databridge_mcp_ingest import screen                      # noqa: PLC0415
        try:
            key = screen._approval_key(None)
        except Exception as exc:                                     # noqa: BLE001 - fail closed
            _log.warning("design store: no deployment approval key (%s)", type(exc).__name__)
            raise DesignRefused("APPROVAL_KEY_UNAVAILABLE", _KEY_UNAVAILABLE, 503) from None
        here = self.root.resolve()
        ap = anchor_path().resolve()
        if ap == here or here in ap.parents:
            _log.warning("design store: the design anchor ledger lies inside the store root")
            raise DesignRefused("APPROVAL_KEY_UNAVAILABLE", _KEY_UNAVAILABLE, 503)
        if not os.environ.get(screen.APPROVAL_KEY_ENV, ""):
            kp = screen.approval_key_path().resolve()
            if kp == here or here in kp.parents:
                _log.warning("design store: the approval key file lies inside the store root")
                raise DesignRefused("APPROVAL_KEY_UNAVAILABLE", _KEY_UNAVAILABLE, 503)
        return key

    @staticmethod
    def _record_digest(rec: dict) -> str:
        return _dl.digest_of(rec, exclude=("record_digest", "seal"))

    @staticmethod
    def _seal(key: bytes, digest: str) -> str:
        return _dl.seal_of(key, digest)

    def _ledger(self, design_id: str) -> "_dl.DecisionLedger":
        return _dl.DecisionLedger(self._ddir(design_id) / "events.jsonl", key=self._key(),
                                  anchors=anchor_path(), log_id=design_id,
                                  digest_field="record_digest", genesis="",
                                  what="design log", lock_timeout=120.0)

    def _chain(self, design_id: str) -> tuple[list[dict], str]:
        """(verified events, why broken or ""): the decision ledger's verified prefix and its
        verdict (seal, chain, and the anchor EQUAL to the log), plus every record naming this
        design. Nothing in a log with a reason is honoured."""
        d = self._ddir(design_id)
        if d is None or not (d / "events.jsonl").is_file():
            return [], "no such design"
        try:
            led = self._ledger(design_id)
        except DesignRefused as exc:
            # R-029 TIDY2: a READ never provisions the key (nor writes anything). With no key the
            # log cannot be verified, so nothing in it is honoured: it is NOT READABLE.
            return [], f"the design log is not readable: {exc.message}"
        events, why = led.inspect()
        for n, rec in enumerate(events, start=1):
            if rec.get("design_id") != design_id:
                return events[:n - 1], why or f"record {n} names another design"
        return events, why

    def verified_events(self, design_id: str) -> tuple[list[dict], str]:
        """PUBLIC read (R-028, for Activity and audits): ``(events, why)`` — the design log's
        verified events (sealed, chained, anchor EQUAL to the log) and ``""``, or the verified
        prefix and why the log is not honoured. ``([], "no such design")`` for an unknown id.
        Never writes; nothing in a log with a reason is evidence."""
        return self._chain(design_id)

    def _recover(self, design_id: str) -> None:
        """Under the design lock, before a change: complete an interrupted append of THIS log
        explicitly (`DecisionLedger.recover`, recorded in the log). Nothing else is repaired. With
        no provisioned key the change refuses here (`APPROVAL_KEY_UNAVAILABLE`), explicitly."""
        led = self._ledger(design_id)
        if led.path.is_file() and led.interrupted():
            try:
                led.recover()
            except _dl.DecisionRefused as exc:
                raise DesignRefused("DESIGN_INTEGRITY_FAILED", exc.message, 409) from None

    def _append(self, design_id: str, events: list[dict], rec: dict) -> dict:
        """Append ONE event through the decision ledger (verified first, sealed, chained,
        anchored). ``events`` (what the caller verified) is extended with it."""
        try:
            rec = self._ledger(design_id).append({**rec, "design_id": design_id, "at": _now()})
        except _dl.DecisionRefused as exc:
            code = "DESIGN_INTEGRITY_FAILED" if exc.code == _dl.LEDGER_INTEGRITY_FAILED else \
                exc.code
            raise DesignRefused(code, exc.message, exc.status) from None
        events.append(rec)
        return rec

    def _write_rev(self, design_id: str, rev: int, design: TableDesign) -> None:
        d = self._ddir(design_id)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"rev-{rev}.json", "x", encoding="utf-8") as fh:   # never overwritten
            fh.write(json.dumps({"rev": rev, "digest": design.digest,
                                 "design": design.model_dump(mode="json")},
                                sort_keys=True, default=str))

    def _read_rev(self, design_id: str, rev: int, digest: str) -> Optional[TableDesign]:
        p = self._ddir(design_id) / f"rev-{rev}.json"
        try:
            design = TableDesign.model_validate(json.loads(p.read_text(encoding="utf-8"))
                                                ["design"])
        except Exception:                                            # noqa: BLE001 - unreadable
            return None
        return design if design.digest == digest else None

    # --- the derived view -----------------------------------------------------------------------
    def view(self, design_id: str) -> Optional[dict]:
        """The design as its VERIFIED log says, or a fail-closed view naming why it is not."""
        if self._ddir(design_id) is None:
            return None
        events, broken = self._chain(design_id)
        if broken == "no such design":
            return None
        if not events or events[0].get("action") != "proposed":
            broken = broken or "the log does not start with a proposal"
        owner = events[0].get("owner") if events else None
        rev, digest, approval, history, builds = 0, None, None, [], []
        for e in events:
            history.append({k: e.get(k) for k in ("at", "action", "rev", "digest")} |
                           {"by": e.get("principal_id") or e.get("by")})
            if e["action"] in ("proposed", "edited"):
                rev, digest, approval = e["rev"], e["digest"], None
            elif e["action"] == "approved" and e.get("rev") == rev and e.get("digest") == digest:
                approval = {k: e.get(k) for k in ("principal_id", "authenticated_by",
                                                  "principal_type", "dev_only", "at", "digest",
                                                  "rev", "record_digest")}
            elif e["action"] == "built":
                builds.append({k: e.get(k) for k in ("at", "by", "build", "digest", "rev")})
        design = self._read_rev(design_id, rev, digest) if rev and not broken else None
        if rev and design is None and not broken:
            broken = f"revision {rev} is missing or does not hash to its digest"
        produced = next((e.get("produced_by") for e in events if e["action"] == "proposed"), {})
        return {"id": design_id, "owner": owner, "rev": rev, "digest": digest,
                "status": ("integrity_failed" if broken else "approved" if approval else
                           "proposed" if rev == 1 else "edited"),
                "integrity": broken or "verified",
                "design": design.model_dump(mode="json") if design else None,
                "question_items": question_items(design) if design else [],
                "approval": None if broken else approval, "produced_by": produced,
                "history": history, "builds": builds,
                "created_at": events[0]["at"] if events else None,
                "updated_at": events[-1]["at"] if events else None}

    def get(self, design_id: str) -> Optional[dict]:
        return self.view(design_id)

    def list(self, *, owner: str) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = [v for p in sorted(self.dir.glob("dsg-*")) if p.is_dir()
               for v in [self.view(p.name)] if v and v.get("owner") == owner]
        return sorted(out, key=lambda d: d.get("updated_at") or "", reverse=True)

    # --- changes: compare-and-set under the design's lock ---------------------------------------
    def create(self, design: TableDesign, *, owner: str, produced_by: dict) -> dict:
        design_id = f"dsg-{uuid.uuid4().hex[:12]}"
        with self._lock(design_id):
            self._write_rev(design_id, 1, design)
            self._append(design_id, [], {"action": "proposed", "rev": 1,
                                         "digest": design.digest, "owner": owner, "by": owner,
                                         "produced_by": produced_by})
        return self.view(design_id)

    def _current(self, design_id: str, owner: str) -> tuple[list[dict], dict]:
        if self._ddir(design_id) is not None:
            with self._lock(design_id):
                self._recover(design_id)
        events, broken = self._chain(design_id)
        v = self.view(design_id)
        if v is None or v.get("owner") != owner:
            raise DesignRefused("DESIGN_NOT_FOUND", f"no design {design_id!r}", 404)
        if broken or v["integrity"] != "verified":
            raise DesignRefused("DESIGN_INTEGRITY_FAILED", f"the design's log or revisions do "
                                f"not verify ({broken or v['integrity']}); nothing in it is "
                                f"honoured", 409)
        return events, v

    def replace(self, design_id: str, design: TableDesign, *, by: str,
                expected_rev: int) -> dict:
        with self._lock(design_id):
            events, v = self._current(design_id, by)
            if v["rev"] != expected_rev:
                raise DesignRefused("STALE_REVISION", f"the design is at revision {v['rev']}, "
                                                      f"this edit was made on {expected_rev}; "
                                                      f"reload it", 409)
            rev = v["rev"] + 1
            self._write_rev(design_id, rev, design)
            self._append(design_id, events, {"action": "edited", "rev": rev,
                                             "digest": design.digest, "by": by})
        return self.view(design_id)

    def approve(self, design_id: str, *, who: dict, digest: str, owner: str,
                rev: Optional[int] = None) -> dict:
        with self._lock(design_id):
            events, v = self._current(design_id, owner)
            if digest != v["digest"] or (rev is not None and rev != v["rev"]):
                raise DesignRefused("DIGEST_MISMATCH", f"you approved {digest[:12]}…"
                                    f"{f' at revision {rev}' if rev is not None else ''}; the "
                                    f"design is now {str(v['digest'])[:12]}… at revision "
                                    f"{v['rev']}; review it again")
            design = TableDesign.model_validate(v["design"])
            if design.questions:
                raise DesignRefused("DESIGN_HAS_QUESTIONS", "the design still asks: "
                                    + " | ".join(design.questions)
                                    + " — answer them in an edit first")
            self._append(design_id, events, {"action": "approved", "rev": v["rev"],
                                             "digest": digest, **who})
        return self.view(design_id)

    def record_build(self, design_id: str, *, by: str, build_id: str, rev: int,
                     digest: str, actor: Optional[dict]) -> None:
        """A build is an EVENT; it never rewrites the design's state. ``actor`` (E2E5 W-10, R-029
        codex r1 #4: REQUIRED) is the common actor object of whoever asked for the build; its
        id must be ``by``."""
        if not actor:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "a build names the signed-in "
                                                              "person running it", 403)
        try:
            actor = _dl.actor_of(actor)
        except _dl.DecisionRefused:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "a build names the signed-in "
                                                              "person running it", 403) \
                from None
        if actor["principal_id"] != by:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "the build's actor is not the "
                                                              "person running it", 403)
        with self._lock(design_id):
            self._recover(design_id)
            events, broken = self._chain(design_id)
            if broken:
                raise DesignRefused("DESIGN_INTEGRITY_FAILED", broken, 409)
            self._append(design_id, events, {"action": "built", "by": by, "build": build_id,
                                             "rev": rev, "digest": digest, "actor": actor})


def design_of(doc: dict) -> TableDesign:
    return TableDesign.model_validate(doc["design"])


def approve(store: DesignStore, design_id: str, *, principal: Any, digest: str,
            owner: str, rev: Optional[int] = None) -> dict:
    """An AUTHENTICATED principal approves EXACTLY the design with ``digest`` (and ``rev``). A
    string, a dict or any object merely carrying a name is refused
    (`require_authenticated_approver`). The approval is a SEALED event in the design's log."""
    from databridge_mcp_ingest.plans import ApprovalRefused, require_authenticated_approver

    try:
        who = require_authenticated_approver(principal, what="dataset design approval")
    except ApprovalRefused as exc:
        code = getattr(exc, "code", None) or "APPROVER_NOT_AUTHENTICATED"
        code = code if code in APPROVER_REFUSED_MESSAGES else "APPROVER_NOT_AUTHENTICATED"
        _log.info("dataset design approval refused (%s): %s", code, exc)
        raise DesignRefused(code, APPROVER_REFUSED_MESSAGES[code], 403) from None
    return store.approve(design_id, who=who, digest=digest, owner=owner, rev=rev)


def approved_design(store: DesignStore, design_id: str, *, digest: str, owner: str
                    ) -> tuple[dict, TableDesign]:
    """The design a build may run: its sealed log verifies, its current revision hashes to
    ``digest``, and the LATEST state event is an approval of exactly that revision and digest by
    an authenticated principal. Anything else refuses."""
    _events, v = store._current(design_id, owner)
    a = v.get("approval") or {}
    if v.get("status") != "approved" or not a:
        raise DesignRefused("DESIGN_NOT_APPROVED", "this design has not been approved; a person "
                                                   "must approve it before it is built")
    if not str(a.get("principal_id") or "").strip() or \
            not str(a.get("authenticated_by") or "").strip():
        raise DesignRefused("DESIGN_NOT_APPROVED", "the approval names no authenticated "
                                                   "principal")
    design = design_of(v)
    if not (digest == v.get("digest") == a.get("digest") == design.digest) or \
            a.get("rev") != v.get("rev"):
        raise DesignRefused("DIGEST_MISMATCH", "the build must name the APPROVED digest, and the "
                                               "stored revision must still hash to it")
    return v, design



# ================================================================================================
# the build — delegation, pushdown, assembly
# ================================================================================================
@dataclass
class RawBuild:
    dataset: Any
    receipt: dict
    results: dict                    # subtask id -> SubResult
    #: R-029 codex r1 #1: {connection: the ONE content hash this build read it at}
    pins: dict = field(default_factory=dict)


def _withheld(field_path: str, *, policy: str, reason: str, by: str) -> Withheld:
    return Withheld(record=WithholdingRecord(field_path=field_path, policy_id=policy,
                                             reason_code=reason, principal=by,
                                             purpose="dataset_materialisation",
                                             appealable=True))


def _dec(v: Any) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _facts_snapshot(connection: str, facts_dir: Optional[Path]) -> Optional[SnapshotIdentity]:
    """The facts file's identity now (for a zero count, which no returned cell carries)."""
    from databridge_mcp_ingest.tabular.facts import load_manifest, sha256_file  # noqa: PLC0415
    p = _facts_file(connection, facts_dir)
    if not p.is_file():
        return None
    m = load_manifest(connection, facts_dir) or {}
    when = m.get("extracted_at") or dt.datetime.fromtimestamp(
        p.stat().st_mtime, dt.timezone.utc).isoformat()
    return SnapshotIdentity(source_id=connection,
                            as_of=dt.datetime.fromisoformat(str(when).replace("Z", "+00:00")),
                            content_hash=sha256_file(p), schema_hash="")


def aggregate_pointer(*, connection: str, table: str, key: str, op: str, field: Optional[str],
                      link_field: str, link_value: Any, row_ids: list[str],
                      snapshot: SnapshotIdentity, leaves: Optional[list] = None
                      ) -> SourcePointer:
    """A ``derived`` pointer for one aggregated cell that re-reads LIVE: its predicate names the
    exact read-only selection (connection, table, link column = value, the facts file's content
    hash), and the leaves (each contributing cell) ride along when there are any."""
    from databridge_federated.pointers import rows_digest            # noqa: PLC0415
    pred = json.dumps({"dsd": "aggregate", "connection": connection, "table": table,
                       "key": key, "op": op, "field": field, "link_field": link_field,
                       "link_value": link_value, "content_hash": snapshot.content_hash},
                      sort_keys=True, default=str)
    ids = sorted({str(r) for r in row_ids})
    leaves = list(leaves or [])
    return SourcePointer(source_id=connection, kind="derived",
                         derivation=f"{op.upper()}({field or '*'}) of {table} rows with "
                                    f"{link_field} = {link_value!r}",
                         predicate=pred, row_count=len(ids), rows_digest=rows_digest(ids),
                         sample_row_ids=ids[:20] or None, snapshot=snapshot,
                         derived_from=leaves if 0 < len(leaves) <= 2000 else None,
                         trust=Trust.UNTRUSTED)


def documents_count_pointer(*, connection: str, link: str, value: Any,
                            leaves: list[SourcePointer], entity_kind: str = "",
                            doc_kinds: tuple = (), edge_kinds: tuple = ()) -> SourcePointer:
    from databridge_federated.pointers import rows_digest            # noqa: PLC0415
    ids = sorted({f"{p.document_id}:{p.char_start}:{p.char_end}" for p in leaves})
    return SourcePointer(source_id=connection, kind="derived",
                         derivation=f"COUNT(documents linked to {value!r} via {link})",
                         predicate=json.dumps({"dsd": "documents_count", "link": link,
                                               "value": value, "connection": connection,
                                               "entity_kind": entity_kind,
                                               "doc_kinds": list(doc_kinds),
                                               "edge_kinds": list(edge_kinds)},
                                              sort_keys=True, default=str),
                         derived_from=list(leaves), row_count=len(ids),
                         rows_digest=rows_digest(ids), sample_row_ids=ids[:20] or None,
                         trust=Trust.UNTRUSTED)


def dsd_kind(pointer: SourcePointer) -> Optional[str]:
    """``aggregate`` / ``documents_count`` for a pointer this module minted, else None."""
    if pointer.kind != "derived" or not pointer.predicate or \
            not pointer.predicate.startswith('{"'):
        return None
    try:
        return json.loads(pointer.predicate).get("dsd")
    except ValueError:
        return None


def documents_selection(store: Any, *, connection: str, entity_kind: str, value: Any,
                        doc_kinds, edge_kinds) -> Optional[set]:
    """The FULL selection a documents count stands for, re-run on the current graph: the own
    pointer (in ``connection``) of every node of ``doc_kinds`` joined by a DIRECT ``edge_kinds``
    edge to the ``entity_kind`` node whose canonical id is ``value``. None without a store."""
    if store is None:
        return None
    from databridge_graph.store import row_to_edge, row_to_node      # noqa: PLC0415
    nodes = {n.id: n for n in (row_to_node(store, r) for r in store.fetchall(
        "SELECT * FROM nodes WHERE system_to IS NULL"))}

    def kind(x):
        return str(getattr(x.kind, "value", x.kind))
    targets = {i for i, n in nodes.items() if kind(n) == entity_kind
               and n.canonical_id is not None and str(n.canonical_id) == str(value)}
    out = set()
    for r in store.fetchall("SELECT * FROM edges WHERE system_to IS NULL"):
        e = row_to_edge(store, r)
        if kind(e) not in set(edge_kinds):
            continue
        for here, there in ((e.src_id, e.dst_id), (e.dst_id, e.src_id)):
            o = nodes.get(there)
            if here in targets and o is not None and kind(o) in set(doc_kinds):
                mine = [p for p in o.sources if p.source_id == connection]
                if mine:
                    out.add(mine[0].model_dump_json(exclude_none=True))
    return out


def resolve_dsd(pointer: SourcePointer, *, facts_dir: Optional[Path],
                resolve_leaf: Callable[[SourcePointer], Any],
                graph_store: Any = None) -> Optional[Any]:
    """Re-read a DSD pointer LIVE: an aggregate re-runs its selection on the facts file (which
    must still be the file the pointer names) and recomputes the value in Decimal, checking the
    contributing row set; a documents count re-resolves each document it counted."""
    kind = dsd_kind(pointer)
    if kind == "documents_count":
        leaves = list(pointer.derived_from or [])
        if not leaves or any(resolve_leaf(p) is None for p in leaves):
            return None
        # R-027 f1 #3: the FULL selection is re-run; the count holds only if it is exactly the
        # documents cited (a partial read cannot pass for a census)
        try:
            p = json.loads(pointer.predicate or "")
            full = documents_selection(graph_store, connection=p["connection"],
                                       entity_kind=p["entity_kind"], value=p["value"],
                                       doc_kinds=p["doc_kinds"], edge_kinds=p["edge_kinds"])
        except (ValueError, KeyError, TypeError):
            return None
        if full is None or full != {x.model_dump_json(exclude_none=True) for x in leaves}:
            return None
        return len(leaves)
    if kind != "aggregate":
        return None
    from databridge_federated.pointers import rows_digest            # noqa: PLC0415
    from databridge_mcp_ingest.tabular.facts import sha256_file       # noqa: PLC0415
    try:
        p = json.loads(pointer.predicate or "")
        path = _facts_file(p["connection"], facts_dir)
        if not path.is_file() or sha256_file(path) != p["content_hash"]:
            return None
        conn = _ro(path)
        try:
            cols = [d[0] for d in conn.execute(
                f"SELECT * FROM {_qi(p['table'])} LIMIT 0").description]
            for c in (p["key"], p["link_field"], p.get("field")):
                if c is not None and c not in cols:
                    return None
            fld = p.get("field")
            rows = conn.execute(
                f"SELECT CAST({_qi(p['key'])} AS TEXT), "
                + (f"CAST({_qi(fld)} AS TEXT)" if fld else "NULL")
                + f" FROM {_qi(p['table'])} WHERE {_qi(p['link_field'])} = ?",
                (p["link_value"],)).fetchall()
        finally:
            conn.close()
    except (ValueError, KeyError, TypeError, sqlite3.Error):
        return None
    op = p["op"]
    if op == "count" and not p.get("field"):
        ids, value = [r[0] for r in rows], len(rows)
    else:
        present = [(k, v) for k, v in rows if v is not None]
        ids = [k for k, _v in present]
        if op == "count":
            value = len(present)
        else:
            nums = [_dec(v) for _k, v in present]
            if not nums or any(n is None for n in nums):
                return None
            value = (sum(nums, Decimal(0)) if op == "sum" else
                     sum(nums, Decimal(0)) / Decimal(len(nums)) if op == "avg" else
                     min(nums) if op == "min" else max(nums))
    if len(set(ids)) != pointer.row_count or rows_digest(ids) != pointer.rows_digest:
        return None
    # R-029 codex r1 #3: the pointer's own snapshot and every leaf it carries name exactly this
    # file and this selection — a leaf from another snapshot, or of a row the selection does not
    # hold, never resolves
    if pointer.snapshot is not None and pointer.snapshot.content_hash != p["content_hash"]:
        return None
    leaves = list(pointer.derived_from or [])
    if leaves:
        want_col = p.get("field") or p["key"]
        got_ids = []
        for leaf in leaves:
            if leaf.source_id != p["connection"] or leaf.table != p["table"] or \
                    leaf.column != want_col or leaf.row_id is None:
                return None
            if leaf.snapshot is None or leaf.snapshot.content_hash != p["content_hash"]:
                return None
            if leaf.capture_digest is not None and leaf.capture_digest != p["content_hash"]:
                return None
            got_ids.append(str(leaf.row_id))
        if sorted(got_ids) != sorted({str(i) for i in ids}):
            return None
    return value


def _screen():
    try:
        from databridge_mcp_ingest.screen import apply_masks, load_screen  # noqa: PLC0415
        return load_screen().redactions, apply_masks
    except Exception:                                             # noqa: BLE001 - fail closed
        return None, None


def _tkey(v: Any) -> tuple:
    """A join key that keeps its TYPE: integer 1 and text "1" are different keys (a number is
    compared as a number, so 1 and 1.0 are one key, as SQL compares them)."""
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, (int, float, Decimal)):
        d = _dec(v)
        return ("n", d.normalize() if d is not None else str(v))
    if isinstance(v, str):
        return ("s", v)
    return ("o", repr(v))


def _tkey_any(index: dict, v: Any) -> bool:
    """Whether some key of ``index`` has the same TEXT as ``v`` (a type mismatch, said)."""
    return any(str(k[1]) == str(v) or (k[0] == "n" and str(v) == format(k[1], "f"))
               for k in index)


def _refuse_ambiguous(index: dict, what: str) -> None:
    dup = sorted(str(k[1]) for k, v in index.items() if len(v) > 1)
    if dup:
        raise DesignRefused("AMBIGUOUS_JOIN", f"{what}: {len(dup)} key(s) match more than one "
                                              f"row (e.g. {dup[:3]}); a join is never resolved "
                                              f"by picking one", 422)


def _count_base(service: Any, base: "TableInfo", design: TableDesign,
                admission: Any) -> tuple[Any, int, str]:
    """The source's OWN pushed-down COUNT of the design's selection, read FIRST (E2E5 F-10): it is
    what the size bound is checked against before any row is read, and — independently — the
    proof of the paged read (R-027 f1 #4): its contributing row keys must be exactly the rows the
    pages returned. Returns ``(result, count, key-set digest)``."""
    from databridge_federated.contracts import Aggregate, Filter, SubTask  # noqa: PLC0415
    from databridge_federated.pointers import rows_digest                  # noqa: PLC0415
    task = SubTask(id="base-count", adapter_agent=base.adapter_agent,
                   connection=base.connection, entity_kinds=(base.table,),
                   aggregate=Aggregate(op="count"),
                   filters=tuple(Filter(field=f.field, op=f.op, value=f.value)
                                 for f in design.filters))
    got = service.run_subtask(task, admission=admission)
    if got.error:
        raise DesignRefused("BASE_NOT_READ", f"the adapter agent {base.adapter_agent!r} could not "
                                             f"read {base.table}: {got.error}")
    if got.coverage.excluded or got.coverage.skipped:
        raise DesignRefused("BASE_UNPROVEN", "the base row count could not be proven: "
                            + "; ".join([*got.coverage.excluded,
                                         *(s.reason for s in got.coverage.skipped)]), 422)
    cell = got.rows[0].cells.get("count") if got.rows else None
    n = int(cell.value) if cell is not None else 0
    digest = cell.pointer.rows_digest if cell is not None else rows_digest([])
    return got, n, digest


def _prove_base(base: "TableInfo", res: Any, n: int, digest: str) -> None:
    """The paged read is exactly the counted selection: same number of rows, same key set."""
    from databridge_federated.pointers import rows_digest                  # noqa: PLC0415
    keys = [str(r.cells[base.key].value) for r in res.rows]
    if n != len(keys) or digest != rows_digest(keys):
        raise DesignRefused("BASE_UNPROVEN", f"the delegate returned {len(keys)} {base.table} "
                                             f"row(s) and the source counts {n} (or a different "
                                             f"key set); nothing is built", 422)


def _snap_id(s: Any) -> Optional[tuple]:
    """A source snapshot's FILE identity (the statement's own query hash left out): what two
    reads must share to be one read of one source."""
    if s is None:
        return None
    return (s.source_id, s.as_of.isoformat() if s.as_of else None, s.content_hash,
            s.schema_hash)


_TABULAR = ("facts_sql_rows", "facts_sql_pushdown")


def _one_snapshot_per_connection(results: dict) -> dict:
    """R-029 codex r1 #1/#3: every tabular read of one connection in this build is of ONE
    snapshot. Returns ``{connection: file identity}``; a second identity refuses, and a tabular
    read that returned rows without a snapshot refuses (it cannot be bound)."""
    seen: dict = {}
    for tid, r in results.items():
        if r is None or r.method not in _TABULAR or r.error:
            continue
        if r.snapshot is None:
            if r.rows:
                raise DesignRefused("SOURCE_UNPROVEN", f"the read {tid} of {r.connection} "
                                                       f"returned rows without a source "
                                                       f"snapshot; nothing is built", 422)
            continue
        sid = _snap_id(r.snapshot)
        if seen.setdefault(r.connection, sid) != sid:
            raise DesignRefused("SOURCE_MOVED", f"{r.connection} was read at two different "
                                                f"snapshots within one build; nothing is built "
                                                f"from a mix", 409)
    return seen


def _incomplete(r: Any) -> Optional[str]:
    """Why a linked read cannot stand for its whole selection (None when it can): an error, a
    skipped source, a ranked or cut read. (Excluded rows are handled per row by the caller.)"""
    if r is None:
        return "not run"
    if r.error:
        return r.error
    if r.coverage.skipped:
        return "; ".join(s.reason for s in r.coverage.skipped)
    if not r.coverage.exhaustive:
        return "the read was a ranked search, not every matching row"
    if r.coverage.truncated:
        return "the read was cut short"
    return None


def _read_paged(service: Any, task: Any, key: str, *, admission: Any, first: Any = None,
                max_pages: Optional[int] = None, expect: Optional[tuple] = None
                ) -> tuple[Any, int]:
    """Every row of ``task``'s selection, KEYSET-PAGED on the table's proven row key (E2E5 F-10).

    The delegate orders by the row key and says when a read was cut (`coverage.truncated`: its own
    limit, or the store's per-statement ceiling). Each further page is the same statement with one
    more bound filter, ``key > <the last key read>`` — pushed down, read-only, a pointer per cell
    exactly as on page one. Paging ends only when a page says it is complete. A cut page with no
    row to continue from, or a page read from another snapshot than the first, refuses; a page
    with an error or rows the delegate could not use is returned merged, for the caller to refuse.
    Returns ``(one merged result, pages read)``."""
    from databridge_federated.contracts import Filter                       # noqa: PLC0415
    pages: list = []
    r = first
    what = task.entity_kinds[0] if task.entity_kinds else "the source"
    while True:
        if r is None and not pages:
            r = service.run_subtask(task, admission=admission)
        elif r is None:
            after = pages[-1].rows[-1].cells[key].value
            r = service.run_subtask(task.model_copy(update={
                "id": f"{task.id[:24]}-p{len(pages)}",
                "filters": (*task.filters, Filter(field=key, op="gt", value=after))}),
                admission=admission)
        if r.error or r.coverage.excluded or r.coverage.skipped:
            pages.append(r)
            break
        # R-029 codex r1 #1: EVERY page names its snapshot, and it is the first page's (the
        # full file identity: source, as-of, content and schema), never assumed
        if r.snapshot is None:
            raise DesignRefused("SOURCE_UNPROVEN", f"a page of the {what} read names no source "
                                                   f"snapshot, so its rows cannot be bound to "
                                                   f"one; nothing is built", 422)
        if (pages and _snap_id(r.snapshot) != _snap_id(pages[0].snapshot)) or (
                expect is not None and _snap_id(r.snapshot) != expect):
            raise DesignRefused("SOURCE_MOVED", f"{what} changed between two reads of one build; "
                                                f"nothing is built from a mix of two snapshots",
                                409)
        pages.append(r)
        if not r.coverage.truncated:
            break
        if not r.rows or key not in r.rows[-1].cells:
            raise DesignRefused("BASE_UNPROVEN", f"a page of the {what} read was cut short with no "
                                                 f"usable row to continue from; nothing is built",
                                422)
        if max_pages is not None and len(pages) >= max_pages:
            raise DesignRefused("BASE_UNPROVEN", f"the {what} read did not finish within "
                                                 f"{max_pages} pages; nothing is built", 422)
        r = None
    return (pages[0] if len(pages) == 1 else _merge_pages(pages)), len(pages)


def _merge_pages(pages: list) -> Any:
    """The pages of one read as ONE result: every row in key order, every statement in its
    notes, every exclusion kept."""
    first = pages[0]
    cov = first.coverage.model_copy(update={
        "truncated": pages[-1].coverage.truncated,
        "notes": tuple(n for p in pages for n in p.coverage.notes),
        "excluded": tuple(e for p in pages for e in p.coverage.excluded),
        "skipped": tuple(s for p in pages for s in p.coverage.skipped)})
    return first.model_copy(update={
        "rows": tuple(row for p in pages for row in p.rows), "coverage": cov,
        "queries": sum(int(p.queries or 0) for p in pages),
        "error": next((p.error for p in pages if p.error), None),
        "timings": {"ms": sum(int((p.timings or {}).get("ms", 0)) for p in pages),
                    "pages": len(pages)}})


def build_raw(design: TableDesign, cat: DesignCatalogue, service: Any, *, admission: Any,
              by: str, facts_dir: Optional[Path] = None,
              emit: Callable[[dict], None] = lambda _e: None) -> RawBuild:
    """Delegate every part to the adapter agent holding it; join on the approved links; return the
    raw dataset and its receipt. Raises `DesignRefused` when the base cannot be read whole."""
    from databridge_federated.contracts import Aggregate, Filter, SubTask  # noqa: PLC0415
    from .dataset import Dataset

    base = cat.table(design.grain)
    if base is None or base.key is None:
        raise DesignRefused("GRAIN_UNREADABLE", f"{design.grain.entity} is not a readable table "
                                                f"with a proven row key")
    links = {lk.id: lk for lk in design.links}
    base_fields = [c.field for c in design.columns if c.via is None and c.field != RECORD_TIME]
    base_fields += [lk.from_field for lk in design.links]
    if base.split:
        base_fields.append(base.split.get("column"))
    base_fields = [f for f in dict.fromkeys([base.key, *base_fields]) if f]
    size = page_rows()
    bound = max_base_rows()
    base_task = SubTask(
        id="base", adapter_agent=base.adapter_agent, connection=base.connection,
        entity_kinds=(base.table,), fields=tuple(base_fields), limit=size,
        record_time=True,
        filters=tuple(Filter(field=f.field, op=f.op, value=f.value) for f in design.filters))
    emit({"stage": "delegating", "part": "base", "adapter_agent": base.adapter_agent,
          "at": _now()})
    # E2E5 F-10: the source COUNTS the selection first; the bound is checked against that count
    # before a single row is read, and the refusal names both numbers
    proof, counted, counted_digest = _count_base(service, base, design, admission)
    if counted > bound:
        raise DesignRefused("BASE_TOO_LARGE", f"{base.table} has {counted} row(s) in this "
                                              f"design's selection (counted by the source); this "
                                              f"deployment builds at most {bound} rows in one raw "
                                              f"table ({MAX_BASE_ROWS_ENV}). Add a filter to the "
                                              f"design, or ask an operator to raise the bound. "
                                              f"Nothing was read", 422)
    emit({"stage": "delegating", "part": "base", "rows_counted": counted, "page_rows": size,
          "at": _now()})
    if proof.snapshot is None:
        raise DesignRefused("SOURCE_UNPROVEN", f"the count of {base.table} names no source "
                                               f"snapshot; nothing is built", 422)
    # R-029 codex r1 #1: the pages must be read from the snapshot the COUNT was read from
    res, base_pages = _read_paged(service, base_task, base.key, admission=admission,
                                  max_pages=counted + 2, expect=_snap_id(proof.snapshot))
    if res.error:
        raise DesignRefused("BASE_NOT_READ", f"the adapter agent {base.adapter_agent!r} could not "
                                             f"read {base.table}: {res.error}")
    if res.coverage.excluded or res.coverage.skipped:
        # R-027 f1 #4: a raw grain is one row per base row; a row the delegate could not use
        # (no pointer, no key) is never silently dropped from it
        raise DesignRefused("BASE_INCOMPLETE", f"the adapter agent could not use every "
                                               f"{base.table} row: "
                            + "; ".join([*res.coverage.excluded,
                                         *(s.reason for s in res.coverage.skipped)])
                            + ". Nothing is built rather than a table missing rows", 422)
    _prove_base(base, res, counted, counted_digest)
    results = {"base": res, "base-count": proof}
    rows = sorted(res.rows, key=lambda r: str(r.cells[base.key].value))
    # ---- the linked parts, all at once (each a pushed-down statement per chunk) ----------------
    tasks: list = []
    plan: dict[str, list[str]] = {}
    paged: dict[str, tuple] = {}            # lookup sub-task id -> (task, the far table's key)
    agg_cols = [c for c in design.columns if c.via and links[c.via].kind == "aggregate"]
    for lk in design.links:
        vals = sorted({r.cells[lk.from_field].value for r in rows
                       if lk.from_field in r.cells and r.cells[lk.from_field].value is not None},
                      key=lambda v: (str(type(v)), str(v)))
        if lk.kind == "documents":
            t = SubTask(id=_tid(lk.id, 0), adapter_agent=lk.target.adapter_agent,
                        connection=lk.target.connection, entity_kinds=lk.doc_kinds,
                        link_edges=lk.edge_kinds, limit=5000,
                        filters=((Filter(field="text", op="contains", value=lk.text_contains),)
                                 if lk.text_contains else ()))
            tasks.append(t)
            plan.setdefault(lk.id, []).append(t.id)
            continue
        if not vals:
            continue
        far = cat.table(lk.target)
        step = min(IN_CHUNK, size)
        chunks = [vals[i:i + step] for i in range(0, len(vals), step)]
        if lk.kind == "lookup":
            need = [c.field for c in design.columns if c.via == lk.id]
            if lk.split:
                need.append(lk.split.get("column"))
            fields = tuple(f for f in dict.fromkeys([lk.to_field, *need]) if f)
            for i, ch in enumerate(chunks):
                t = SubTask(id=_tid(lk.id, i), adapter_agent=lk.target.adapter_agent,
                            connection=lk.target.connection, entity_kinds=(far.table,),
                            fields=fields, limit=min(_SUBTASK_LIMIT, len(ch) + 1),
                            filters=(Filter(field=lk.to_field, op="in", value=list(ch)),))
                tasks.append(t)
                paged[t.id] = (t, far.key)
                plan.setdefault(lk.id, []).append(t.id)
        else:
            for c in [c for c in agg_cols if c.via == lk.id]:
                for i, ch in enumerate(chunks):
                    t = SubTask(id=_tid(f"{lk.id}_{c.name}", i),
                                adapter_agent=lk.target.adapter_agent,
                                connection=lk.target.connection, entity_kinds=(far.table,),
                                aggregate=Aggregate(op=c.aggregate,
                                                    field=None if c.field == "*" else c.field,
                                                    group_by=(lk.to_field,)),
                                filters=(Filter(field=lk.to_field, op="in", value=list(ch)),))
                    tasks.append(t)
                    plan.setdefault(f"{lk.id}:{c.name}", []).append(t.id)
    if tasks:
        emit({"stage": "delegating", "parts": len(tasks), "at": _now()})
        results.update(service.dispatch(tasks, admission=admission))
        # E2E5 F-10: a lookup read the store cut short is CONTINUED, keyset-paged on the far
        # table's proven key — never read as "no such row" (which would be an orphan it is not)
        for tid, (t, far_key) in paged.items():
            r = results.get(tid)
            if r is None or r.error or not r.coverage.truncated:
                continue
            if far_key is None:
                results[tid] = r.model_copy(update={"error": "the read was cut short and the "
                                                             "table has no proven row key to "
                                                             "page on"})
                continue
            results[tid], _n = _read_paged(service, t, far_key, admission=admission, first=r,
                                           max_pages=len(t.filters[0].value) + 2)
    # R-029 codex r1 #1/#3: the count, the pages and every linked read of one connection are ONE
    # snapshot (never "the last one seen"); these are the build's pins
    conn_snap = _one_snapshot_per_connection(results)
    pins = {c: sid[2] for c, sid in conn_snap.items()}
    zero_snap: dict = {}

    def snap_for(conn: str) -> Optional[SnapshotIdentity]:
        """The verified file snapshot of ``conn`` for a zero no read returned a row for."""
        if conn in conn_snap:
            src, as_of, content, schema = conn_snap[conn]
            return SnapshotIdentity(source_id=src, as_of=(dt.datetime.fromisoformat(as_of)
                                                          if as_of else None),
                                    content_hash=content, schema_hash=schema)
        if conn not in zero_snap:
            zero_snap[conn] = _facts_snapshot(conn, facts_dir)
            if zero_snap[conn] is not None:
                pins[conn] = zero_snap[conn].content_hash
        return zero_snap[conn]
    # ---- assembly ------------------------------------------------------------------------------
    redactions, apply_masks = _screen()
    unmatched: dict[str, dict] = {}
    columns = [c.name for c in design.columns]
    out_rows: list[tuple] = []
    lookup_index: dict[str, dict] = {}
    lookup_failed: dict[str, str] = {}
    lookup_partial: dict[str, str] = {}
    for lk in design.links:
        if lk.kind != "lookup":
            continue
        idx: dict[str, Any] = {}
        for tid in plan.get(lk.id, []):
            r = results.get(tid)
            why = _incomplete(r)
            if why is not None:
                # R-029 codex r1 #2: an incomplete read never stands for its selection
                lookup_failed[lk.id] = why
                continue
            if r.coverage.excluded:
                # rows the delegate could not use: an unmatched row may be one of them, so it is
                # never called an orphan
                lookup_partial[lk.id] = "; ".join(r.coverage.excluded)
            for row in r.rows:
                k = row.cells.get(lk.to_field)
                if k is not None and k.value is not None:
                    idx.setdefault(_tkey(k.value), []).append(row)
        _refuse_ambiguous(idx, f"{lk.target.entity}.{lk.to_field}")
        lookup_index[lk.id] = {k: v[0] for k, v in idx.items()}
    agg_index: dict[str, dict] = {}
    agg_failed: dict[str, str] = {}
    agg_partial: dict[str, str] = {}
    for c in agg_cols:
        lk = links[c.via]
        idx = {}
        for tid in plan.get(f"{lk.id}:{c.name}", []):
            r = results.get(tid)
            why = _incomplete(r)
            if why is not None:
                # R-029 codex r1 #2: a missing group of an incomplete read is never a zero
                agg_failed[c.name] = why
                continue
            if r.coverage.excluded:
                agg_partial[c.name] = "; ".join(r.coverage.excluded)
            label = Aggregate(op=c.aggregate, field=None if c.field == "*" else c.field).label()
            for row in r.rows:
                k = row.cells.get(lk.to_field)
                v = row.cells.get(label)
                if k is not None and k.value is not None:
                    # R-029 codex r1 #3: each group keeps the snapshot of the chunk it came from
                    idx.setdefault(_tkey(k.value), []).append((v, row, r.snapshot))
        _refuse_ambiguous(idx, f"{lk.target.entity} grouped by {lk.to_field}")
        agg_index[c.name] = {k: v[0] for k, v in idx.items()}
    doc_index: dict[str, dict] = {}
    doc_failed: dict[str, str] = {}
    doc_notes: dict[str, list] = {}
    for lk in design.links:
        if lk.kind != "documents":
            continue
        idx: dict[str, dict] = {}
        for tid in plan.get(lk.id, []):
            r = results.get(tid)
            if r is None or r.error:
                doc_failed[lk.id] = r.error if r is not None else "not run"
                continue
            doc_notes[lk.id] = [*r.coverage.notes, *r.coverage.excluded]
            for row in r.rows:
                doc = row.cells.get("document")
                if doc is None:
                    continue
                # one DOCUMENT counts once, however many of its passages matched
                ident = doc.pointer.model_dump_json(exclude_none=True)
                for e in row.entities:
                    # a DIRECT, declared edge only (the resolver re-runs exactly this selection)
                    if e.kind != lk.entity_kind or e.subject or e.via not in lk.edge_kinds or \
                            e.canonical_id is None:
                        continue
                    idx.setdefault(str(e.canonical_id), {}).setdefault(ident, row)
            if not r.coverage.exhaustive or r.coverage.truncated:
                # R-027 f1 #3: a ranked or truncated read is not a census; no count comes of it
                doc_failed[lk.id] = ("the documents read was not exhaustive (ranked search or "
                                     "truncated), so no count or single value is taken from it")
        doc_index[lk.id] = idx
    # identifier screen over every value this build would emit (whole column, never cell by cell)
    masked: set[str] = set()
    for c in design.columns:
        tbl = base if c.via is None else cat.table(links[c.via].target) \
            if links[c.via].kind != "documents" else None
        if tbl is not None and c.field in tbl.identifiers and c.aggregate is None:
            masked.add(c.name)
    base_snap = res.snapshot
    subject_rows = 0
    for r in rows:
        bkey = str(r.cells[base.key].value)
        base_subject = base.subject and _row_is_subject(cat.vocab, base, r.cells)
        cells: list = []
        for c in design.columns:
            path = f"{design.grain.connection}.{design.grain.entity}.{c.name}"
            if c.name in masked:
                cells.append(_withheld(path, policy=IDENTIFIER_POLICY, reason=IDENTIFIER_REASON,
                                       by=by))
                continue
            if c.via is None:
                cell = r.cells.get(c.field)
                if cell is None or cell.value is None:
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                else:
                    cells.append(GroundedValue(value=cell.value, pointer=cell.pointer))
                continue
            lk = links[c.via]
            kv = r.cells.get(lk.from_field)
            key = None if kv is None or kv.value is None else kv.value
            if lk.kind == "lookup":
                if lk.id in lookup_failed:
                    _miss(unmatched, c.name, f"the delegate failed: {lookup_failed[lk.id]}")
                    cells.append(Absent(field_path=path))
                    continue
                if key is None:
                    _miss(unmatched, c.name, f"{lk.from_field} is empty on this row")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                hit = lookup_index.get(lk.id, {}).get(_tkey(key))
                if hit is None and _tkey_any(lookup_index.get(lk.id, {}), key):
                    _miss(unmatched, c.name, f"a {lk.target.entity} row's {lk.to_field} has the "
                                             f"same text as this row's {lk.from_field} but a "
                                             f"different type; not joined")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                if hit is None and lk.id in lookup_partial:
                    _miss(unmatched, c.name, f"not matched, and the delegate could not use "
                                             f"every {lk.target.entity} row it read "
                                             f"({lookup_partial[lk.id]}); the match may be one "
                                             f"of them")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                if hit is None:
                    _miss(unmatched, c.name, f"no {lk.target.entity} row has {lk.to_field} = "
                                             f"the row's {lk.from_field} (an orphan reference)")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                if lk.candidate and (not lk.split or _row_is_subject(cat.vocab, None, hit.cells,
                                                                      split=lk.split)):
                    _miss(unmatched, c.name, "the linked record is a person: a candidate only, "
                                             "never merged (withheld)", withheld=True)
                    cells.append(_withheld(path, policy=CANDIDATE_POLICY,
                                           reason=CANDIDATE_REASON, by=by))
                    continue
                cell = hit.cells.get(c.field)
                cells.append(GroundedValue(value=cell.value, pointer=cell.pointer)
                             if cell is not None and cell.value is not None
                             else Absent(field_path=path))
                continue
            if lk.kind == "aggregate":
                if c.name in agg_failed:
                    _miss(unmatched, c.name, f"the delegate failed: {agg_failed[c.name]}")
                    cells.append(Absent(field_path=path))
                    continue
                if key is None:
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                far = cat.table(lk.target)
                got = agg_index.get(c.name, {}).get(_tkey(key))
                if got is None and _tkey_any(agg_index.get(c.name, {}), key):
                    _miss(unmatched, c.name, f"{lk.target.entity} rows' {lk.to_field} have the "
                                             f"same text as this row's {lk.from_field} but a "
                                             f"different type; not counted")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                fld = None if c.field == "*" else c.field
                if got is None and c.name in agg_partial:
                    _miss(unmatched, c.name, f"no group came back, and the delegate could not "
                                             f"use every {lk.target.entity} row "
                                             f"({agg_partial[c.name]}); a zero is not proven")
                    cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                if got is None or got[0] is None:
                    if c.aggregate == "count" and got is None:
                        snap = snap_for(lk.target.connection)
                        if snap is None:
                            cells.append(Absent(field_path=path))
                            continue
                        cells.append(GroundedValue(value=0, pointer=aggregate_pointer(
                            connection=lk.target.connection, table=far.table, key=far.key,
                            op="count", field=fld, link_field=lk.to_field, link_value=key,
                            row_ids=[], snapshot=_file_snapshot(snap))))
                    else:
                        _miss(unmatched, c.name, f"no {far.table} row (with a value) is linked "
                                                 f"to this row")
                        cells.append(Absent(field_path=path, snapshot=base_snap))
                    continue
                vcell, grp, gsnap = got
                leaves = list(vcell.pointer.derived_from or [])
                ids = [p.row_id for p in leaves if p.row_id is not None]
                if vcell.pointer.derived_from is None:
                    cells.append(Absent(field_path=path))
                    _miss(unmatched, c.name, "too many contributing rows to cite one by one")
                    continue
                snap = _file_snapshot(gsnap)
                if snap is None:
                    _miss(unmatched, c.name, "the facts file could not be identified")
                    cells.append(Absent(field_path=path))
                    continue
                cells.append(GroundedValue(value=vcell.value, pointer=aggregate_pointer(
                    connection=lk.target.connection, table=far.table, key=far.key,
                    op=c.aggregate, field=fld, link_field=lk.to_field, link_value=key,
                    row_ids=ids, snapshot=snap, leaves=leaves)))
                continue
            # documents
            if lk.id in doc_failed:
                _miss(unmatched, c.name, f"the delegate failed: {doc_failed[lk.id]}")
                cells.append(Absent(field_path=path))
                continue
            found = list(doc_index.get(lk.id, {}).get(str(key), {}).values()) \
                if key is not None else []
            if c.aggregate == "count":
                leaves = [row.cells["document"].pointer for row in found
                          if "document" in row.cells]
                if not leaves:
                    _miss(unmatched, c.name, "no linked document was found (a zero is not "
                                             "proven: document reads are not exhaustive)")
                    cells.append(Absent(field_path=path))
                else:
                    cells.append(GroundedValue(value=len(leaves), pointer=documents_count_pointer(
                        connection=lk.target.connection, link=lk.id, value=key, leaves=leaves,
                        entity_kind=lk.entity_kind, doc_kinds=lk.doc_kinds,
                        edge_kinds=lk.edge_kinds)))
                continue
            if len(found) != 1:
                _miss(unmatched, c.name, f"{len(found)} linked documents: a single value is "
                                         f"taken only when exactly one is linked (never merged)")
                cells.append(Absent(field_path=path))
                continue
            cell = found[0].cells.get(c.field)
            cells.append(GroundedValue(value=cell.value, pointer=cell.pointer)
                         if cell is not None and cell.value is not None
                         else Absent(field_path=path))
        if base_subject:
            # R-027 f2 #4: this ROW is a person (the table's kind, or its per-row split): every
            # value but its record key and record time is withheld as a candidate, pending a
            # person's selection — a raw table never ships a person's attributes
            subject_rows += 1
            cells = [c if d.via is None and d.field in (base.key, RECORD_TIME) else
                     _withheld(f"{design.grain.connection}.{design.grain.entity}.{d.name}",
                               policy=CANDIDATE_POLICY, reason=CANDIDATE_REASON, by=by)
                     for c, d in zip(cells, design.columns)]
        out_rows.append(tuple(cells))
    # the screen: a column whose emitted values match the deployment's contact-identifier screen
    # is withheld WHOLE (never cell by cell, which would make the mask itself a signal)
    if redactions is None:
        screened = {c.name for c in design.columns if c.aggregate is None and c.role != "key"
                    and any(isinstance(row[i], GroundedValue) and isinstance(row[i].value, str)
                            for row in out_rows for i in [columns.index(c.name)])}
        screen_note = "the identifier screen could not load: every text column is withheld"
    else:
        screened = set()
        for i, c in enumerate(design.columns):
            if any(isinstance(row[i], GroundedValue) and isinstance(row[i].value, str)
                   and apply_masks(row[i].value, redactions)[1] for row in out_rows):
                screened.add(c.name)
        screen_note = None
    if screened:
        fixed = []
        for row in out_rows:
            fixed.append(tuple(
                _withheld(f"{design.grain.connection}.{design.grain.entity}.{c.name}",
                          policy=IDENTIFIER_POLICY, reason=IDENTIFIER_REASON, by=by)
                if c.name in screened and isinstance(row[i], GroundedValue) else row[i]
                for i, c in enumerate(design.columns)))
        out_rows = fixed
    keys = [str(r.cells[base.key].value) for r in rows]
    if len(out_rows) != len(rows) or len(set(keys)) != len(keys):
        # the approved grain is one row per base key: a join that fanned out (or a key that
        # repeats) is refused, never shipped as silent duplicates
        raise DesignRefused("GRAIN_VIOLATED", f"{len(out_rows)} rows for {len(set(keys))} "
                                              f"distinct {base.key} values; the grain is one row "
                                              f"per {base.table} row")
    dataset = Dataset(columns=tuple(columns), rows=tuple(out_rows))
    receipt = _receipt(design, cat, results, plan, dataset, unmatched,
                       masked | screened, screen_note, base)
    receipt["subject_rows_withheld"] = subject_rows
    # E2E5 F-10: how the base was read, and the bound it was checked against (before reading)
    receipt["rows"].update({"base_pages": base_pages, "page_rows": size,
                            "bound": {"max_rows": bound, "set_by": MAX_BASE_ROWS_ENV,
                                      "checked_against": "the source's own COUNT of the "
                                                         "selection, before any row was read"}})
    return RawBuild(dataset=dataset, receipt=receipt, results=results, pins=pins)


def _file_snapshot(snap: Any) -> Optional[SnapshotIdentity]:
    """The FILE's identity (a statement's snapshot names the statement too)."""
    if snap is None:
        return None
    return SnapshotIdentity(source_id=snap.source_id, as_of=snap.as_of,
                            content_hash=snap.content_hash, schema_hash=snap.schema_hash)


def _row_is_subject(vocab: Any, table: Optional[TableInfo], cells: dict, *,
                    split: Optional[dict] = None) -> bool:
    """Whether ONE row is a subject: by the mapping's per-row kind split when there is one (an
    unknown or missing value takes the split's default — the safe side), else the table's kind."""
    split = split or (table.split if table else None)
    if not split:
        return bool(table.subject) if table else True
    from databridge_mcp_ingest.mapping import split_value           # noqa: PLC0415
    cell = cells.get(split.get("column"))
    v = None if cell is None else cell.value
    kind = (split.get("kinds") or {}).get(split_value(v)) if v is not None else None
    return _is_subject(vocab, kind or split.get("default"))


def _tid(seed: str, i: int) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", seed.lower())[:26] or "t"
    if not base[0].isalpha():
        base = "t" + base
    return f"{base}-{i}"


def _miss(unmatched: dict, column: str, why: str, *, withheld: bool = False) -> None:
    rec = unmatched.setdefault(column, {})
    rec[why] = rec.get(why, 0) + 1


def _receipt(design: TableDesign, cat: DesignCatalogue, results: dict, plan: dict, dataset,
             unmatched: dict, masked: set, screen_note: Optional[str], base: TableInfo) -> dict:
    sources = []
    for tid, r in results.items():
        sources.append({
            "subtask": tid, "adapter_agent": r.adapter_agent, "connection": r.connection,
            "method": r.method, "queries": r.queries, "rows_returned": len(r.rows),
            "error": r.error,
            "snapshot": ({"content_hash": r.snapshot.content_hash,
                          "as_of": r.snapshot.as_of.isoformat()} if r.snapshot else None),
            "statements": [n for n in r.coverage.notes],
            "excluded": list(r.coverage.excluded), "skipped": [s.model_dump(mode="json")
                                                               for s in r.coverage.skipped],
            "truncated": r.coverage.truncated, "exhaustive": r.coverage.exhaustive,
            "produced_by": r.produced_by})
    cols = []
    for i, c in enumerate(design.columns):
        cells = [row[i] for row in dataset.rows]
        g = sum(isinstance(x, GroundedValue) for x in cells)
        w = sum(isinstance(x, Withheld) for x in cells)
        a = len(cells) - g - w
        lk = next((x for x in design.links if x.id == c.via), None)
        cols.append({"name": c.name, "role": c.role, "meaning": c.meaning,
                     "supplied_by": (lk.target.model_dump() if lk else design.grain.model_dump()),
                     "field": c.field, "aggregate": c.aggregate, "via": c.via,
                     "join": (lk.evidence if lk else None),
                     "grounded": g, "absent": a, "withheld": w,
                     "coverage": (round(g / len(cells), 4) if cells else None),
                     "unmatched": unmatched.get(c.name, {}),
                     "masked": c.name in masked,
                     "when_known": c.when_known,
                     "pointer_kinds": sorted({x.pointer.kind for x in cells
                                              if isinstance(x, GroundedValue)})})
    base_res = results.get("base")
    base_excl = list(base_res.coverage.excluded) if base_res else []
    return {
        "mode": MODE, "statement": RAW_STATEMENT, "not_done": list(NOT_DONE),
        "request": design.request, "design_digest": design.digest,
        "grain": {**design.grain.model_dump(), "key": design.grain_key,
                  "text": design.grain_text},
        "label": (design.label.model_dump() if design.label else
                  {"column": None, "note": "no label: none is invented. A raw table carries "
                                           "only what the sources record"}),
        "who_decides": "Walnut provides the data. Cleaning, splits, class balance and leakage "
                       "decisions are the engineer's: each column says where it came from, its "
                       "pointer kind and WHEN its value became known (or that this is unknown)",
        "grain_check": {"rule": f"one row per {design.grain.entity} row "
                                f"({design.grain_key} unique)",
                        "rows": dataset.n_rows,
                        "fan_out": "none: lookups join on a proven unique key, aggregates are "
                                   "grouped per row, documents are counted (or taken only when "
                                   "exactly one is linked)"},
        "time_columns": [c.name for c in design.columns if c.role == "time"],
        "filters_applied": [f.model_dump(mode="json") for f in design.filters],
        "rows": {"emitted": dataset.n_rows,
                 "base_rows_read": len(base_res.rows) if base_res else 0,
                 "dropped": base_excl,
                 "base_rows_counted": (int(results["base-count"].rows[0].cells["count"].value)
                                       if results.get("base-count") is not None
                                       and results["base-count"].rows else 0),
                 "note": "the base was read whole and its row count and key set were proven by "
                         "a separate pushed-down COUNT; every base row is a row of the table and "
                         "a missing value is an absent cell, never a dropped row"},
        "columns": cols, "sources": sources, "links": [lk.model_dump(mode="json")
                                                         for lk in design.links],
        "parts": plan,
        "identifier_screen": {"masked_columns": sorted(masked), "note": screen_note},
        "persons": "never merged: a link to a person-kind record is withheld as a candidate "
                   "requiring human selection",
        "cells": dataset.cell_counts(),
        "content_hash": dataset.content_hash(),
    }


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


__all__ = ["MODE", "RAW_STATEMENT", "NOT_DONE", "RECORD_TIME", "DesignRefused", "SourceRef",
           "DesignFilter", "DesignLink", "DesignColumn", "LabelSpec", "TableDesign",
           "DesignDraft", "TableInfo", "DesignCatalogue", "build_design_catalogue",
           "validate_design", "rules_draft", "propose_design", "apply_edit", "DesignStore",
           "design_of", "approve", "approved_design", "RawBuild", "build_raw",
           "aggregate_pointer", "documents_count_pointer", "dsd_kind", "resolve_dsd",
           "design_digest", "linking_edges", "question_id", "question_items",
           "AnchorLedger", "anchor_path"]
