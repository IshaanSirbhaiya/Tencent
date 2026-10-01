"""Source SHAPE fingerprints and their diff — how an adapter agent notices a source changed format.

A `SourceShape` is what a source looks like, not what it holds:

* **documents sources** — the tool list (names, input-schema signature, read/write annotations),
  resource templates (when the route can list them), the item FIELD names a listing returns, and
  the file types (extensions) and mime types seen;
* **tabular sources** — tables, columns and their declared types, primary and foreign keys, and a
  small sample of each column's values **as digests** (sha256 prefixes — the fingerprint never
  copies a source value to disk; digests are enough to measure overlap for rename detection).

Every facet is `None` when it was NOT observed (a probe that failed, a route that cannot list it).
`diff` never infers a removal from a facet it did not see on both sides: absence of evidence is not
a `removed` drift (P7, P8).

`diff(old, new) -> list[Drift]` speaks a CLOSED vocabulary (`DRIFT_KINDS`). A `renamed` drift is
always a CANDIDATE (`candidate=True`, with the measured similarity as `confidence`): it pairs a
removed and an added column/field of the same type whose value samples overlap — it is never a
certainty and is never auto-applied. `subject_touching` is a QUALIFIER (`Drift.subject_touching`)
set by `mark_subject`: any drift on a table mapped to (or proposed for) a pack subject kind, or on a
column/field named like a declared subject identifier. `Drift.kinds` includes it when set.

Nothing here names a source or a server; everything is computed from what the server reported.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DriftKind = Literal["added_field", "added_column", "removed", "renamed", "type_changed",
                    "new_mime", "new_file_type", "tool_added", "tool_removed",
                    "tool_signature_changed", "new_write_tool", "new_table", "subject_touching"]
#: The closed drift vocabulary. `subject_touching` qualifies another drift (see module doc).
DRIFT_KINDS: tuple[str, ...] = DriftKind.__args__                     # type: ignore[attr-defined]

#: Rename candidates need at least this Jaccard overlap between value-sample digests.
RENAME_MIN_SIMILARITY = 0.6
SAMPLE_ROWS = 20
SAMPLE_ITEMS = 25
_DIGEST_CHARS = 16


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def value_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolShape(_M):
    name: str
    params: dict[str, str] = Field(default_factory=dict)      # arg -> JSON-schema type (text)
    required: list[str] = Field(default_factory=list)
    signature: str = ""                                        # digest of params + required
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    writes: bool = False                                       # plans.is_write_tool


class ColumnShape(_M):
    name: str
    type: str = ""
    samples: list[str] = Field(default_factory=list)          # value DIGESTS, never values


class TableShape(_M):
    ref: str
    columns: dict[str, ColumnShape] = Field(default_factory=dict)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = Field(default_factory=list)


class FieldShape(_M):
    name: str
    samples: list[str] = Field(default_factory=list)          # value DIGESTS


FACETS = ("tools", "resource_templates", "item_fields", "tables", "file_types", "mimes")
#: unit prefix -> facet
UNIT_FACET = {"tool": "tools", "resource_template": "resource_templates",
              "item_field": "item_fields", "table": "tables", "file_type": "file_types",
              "mime": "mimes"}


class SourceShape(_M):
    source_id: str
    taken_at: str = Field(default_factory=_now)
    tools: Optional[dict[str, ToolShape]] = None
    resource_templates: Optional[dict[str, str]] = None        # template -> mime ("" unknown)
    item_fields: Optional[dict[str, FieldShape]] = None
    tables: Optional[dict[str, TableShape]] = None
    file_types: Optional[dict[str, int]] = None                # ".pdf" -> items seen
    mimes: Optional[dict[str, int]] = None
    #: every table the declared scope excluded, WITH the rule that excluded it and how to reverse
    #: it (R-022 f2 #16): ``{uri, rule, reverse}``. A baseline written before this carried bare
    #: URIs; those are read as ``rule: None`` ("not recorded"), never invented.
    tables_out_of_scope: list[dict[str, Optional[str]]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)

    @field_validator("tables_out_of_scope", mode="before")
    @classmethod
    def _structured_exclusions(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [{"uri": x, "rule": None, "reverse": None} if isinstance(x, str) else x
                for x in v]

    def digest(self) -> str:
        body = self.model_dump(mode="json", exclude={"taken_at", "problems"})
        return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def observed(self) -> list[str]:
        return [f for f in FACETS if getattr(self, f) is not None]

    def overlay(self, other: "SourceShape") -> "SourceShape":
        """This shape with every facet `other` observed replaced by `other`'s."""
        upd = {f: getattr(other, f) for f in FACETS if getattr(other, f) is not None}
        upd["problems"] = [*self.problems, *other.problems]
        if other.tables is not None:
            upd["tables_out_of_scope"] = other.tables_out_of_scope
        return self.model_copy(update=upd)


class DriftAdvanceRefused(ValueError):
    """A baseline advance that would absorb a drift nobody approved (R-022 f2 #7)."""


def unapproved_in_units(baseline: SourceShape, current: SourceShape, units: Iterable[str],
                        approved: Iterable[Any]) -> list["Drift"]:
    """The drifts between `baseline` and `current` that moving `units` would absorb but that are
    NOT among `approved` (Drift objects or drift ids)."""
    ids = {a.drift_id if isinstance(a, Drift) else str(a) for a in approved}
    want = set(units)
    return [d for d in diff(baseline, current)
            if set(d.units()) & want and d.drift_id not in ids]


def advance(baseline: Optional[SourceShape], current: SourceShape,
            units: Optional[Iterable[str]] = None, *, adopt_unobserved: bool = True,
            approved_drifts: Optional[Iterable[Any]] = None) -> SourceShape:
    """A new baseline: `baseline` with the given UNITS taken from `current` (`units=None`: every
    unit). A facet the baseline never observed is adopted whole from `current` (first sight is not
    drift) when `adopt_unobserved`. Never mutates its inputs.

    A unit moves WHOLE (a table's columns, keys and types together), so approving one drift of a
    unit would silently absorb every other change in it. With `approved_drifts` (the drifts — or
    drift ids — a person or a delegation approved), the advance is REFUSED
    (`DriftAdvanceRefused`) when the units it moves carry any drift outside that set (R-022 f2
    #7). An approval path must pass it; `units=None` with it checks every unit."""
    if baseline is None:
        return current.model_copy(deep=True)
    if approved_drifts is not None:
        approved_drifts = list(approved_drifts)
        moving = list(units) if units is not None else sorted(
            {u for d in diff(baseline, current) for u in d.units()})
        units = moving
        extra = unapproved_in_units(baseline, current, moving, approved_drifts)
        if extra:
            raise DriftAdvanceRefused(
                "advancing " + ", ".join(sorted(set(moving))[:10]) + " would absorb drift(s) "
                "nobody approved: " + "; ".join(f"{d.drift_id} {d.detail}" for d in extra[:10]))
    data = baseline.model_dump(mode="python")
    cur = current.model_dump(mode="python")
    if units is None:
        for f in FACETS:
            if cur[f] is not None:
                data[f] = cur[f]
    else:
        for unit in units:
            prefix, _, key = unit.partition(":")
            facet = UNIT_FACET.get(prefix)
            if facet is None or cur[facet] is None:
                continue
            if data[facet] is None:
                data[facet] = {}
            if key in cur[facet]:
                data[facet][key] = cur[facet][key]
            else:
                data[facet].pop(key, None)
    if adopt_unobserved:
        for f in FACETS:
            if data[f] is None and cur[f] is not None:
                data[f] = cur[f]
    data["taken_at"] = current.taken_at
    data["problems"] = []
    return SourceShape.model_validate(data)


# --- builders ---------------------------------------------------------------------------------------
def _type_text(schema: dict) -> str:
    t = (schema or {}).get("type")
    if isinstance(t, list):
        return "|".join(sorted(str(x) for x in t))
    if t:
        return str(t)
    for key in ("anyOf", "oneOf"):
        if isinstance((schema or {}).get(key), list):
            return "|".join(sorted(_type_text(s) for s in schema[key]))
    return "any"


def tool_shape(tool: Any) -> ToolShape:
    """From a `client.ToolInfo` (or anything with name / input_schema / read_only / destructive)."""
    from .plans import is_write_tool

    schema = getattr(tool, "input_schema", None) or {}
    props = schema.get("properties") or {}
    params = {str(k): _type_text(v if isinstance(v, dict) else {}) for k, v in props.items()}
    required = sorted(str(r) for r in schema.get("required") or [])
    sig = hashlib.sha256(json.dumps({"params": params, "required": required},
                                    sort_keys=True).encode()).hexdigest()[:_DIGEST_CHARS]
    return ToolShape(name=tool.name, params=params, required=required, signature=sig,
                     read_only=getattr(tool, "read_only", None),
                     destructive=getattr(tool, "destructive", None), writes=is_write_tool(tool))


def table_shape(ref: str, columns: list[dict], primary_key: list[str],
                foreign_keys: list[dict], rows: Optional[list[dict]] = None) -> TableShape:
    rows = rows or []
    cols: dict[str, ColumnShape] = {}
    for c in columns:
        name = c.get("name") or c.get("column_name")
        if not name:
            continue
        name = str(name)
        vals = [r.get(name) for r in rows if isinstance(r, dict) and r.get(name) is not None]
        cols[name] = ColumnShape(name=name, type=str(c.get("type") or c.get("data_type") or "")
                                 .lower(), samples=[value_digest(v) for v in vals][:SAMPLE_ROWS])
    fks = [{k: fk.get(k) for k in ("column", "ref_schema", "ref_table", "ref_column")}
           for fk in foreign_keys or []]
    return TableShape(ref=ref, columns=cols, primary_key=list(primary_key or []),
                      foreign_keys=sorted(fks, key=lambda f: json.dumps(f, sort_keys=True,
                                                                        default=str)))


def fields_shape(entries: Iterable[dict]) -> dict[str, FieldShape]:
    out: dict[str, list[str]] = {}
    for e in entries:
        for k, v in (e or {}).items():
            out.setdefault(str(k), [])
            if v is not None and len(out[str(k)]) < SAMPLE_ITEMS:
                out[str(k)].append(value_digest(v))
    return {k: FieldShape(name=k, samples=v) for k, v in sorted(out.items())}


def ext_of_name(name: str) -> str:
    from .extract import ext_of

    return ext_of(name or "") or ""


def extractor_exists(ext: str, mime: Optional[str] = None) -> bool:
    """Whether mcp_ingest has an extractor for this file type (the scope rule `decide` applies)."""
    from .extract import extractor_for

    return extractor_for(f"item{ext}" if ext else "item", mime) is not None


def shape_from_run(run_dir: Path, source_id: str) -> SourceShape:
    """File types and mime types seen by one run, from its typed artefacts (`records.read_run`):
    every inventoried item — collected, excluded, blocked or failed — counts as SEEN. A run with no
    inventory observed nothing, and the facets stay `None`."""
    from .records import read_run

    arts = read_run(Path(run_dir))
    items = [r for r in arts.ledgers.get("inventory", []) if r.kind != "folder"]
    if not (Path(run_dir) / "inventory.jsonl").is_file():
        return SourceShape(source_id=source_id, problems=["no inventory.jsonl in the run"])
    types: dict[str, int] = {}
    mimes: dict[str, int] = {}
    for r in items:
        if r.kind == "table":
            continue
        ext = ext_of_name(r.name or r.uri)
        types[ext or "(none)"] = types.get(ext or "(none)", 0) + 1
    for r in arts.ledgers.get("captured", []):
        if r.kind != "table" and r.mime:
            mimes[r.mime] = mimes.get(r.mime, 0) + 1
    # R-022 f1 #19: an inventory with no file item observed NO file types — the facets stay
    # `None` (unobserved), never `{}` (an observed empty shape that would read every type as gone
    # and report its reappearance as new drift).
    observed = any(r.kind != "table" for r in items)
    return SourceShape(source_id=source_id, file_types=types if observed else None,
                       mimes=mimes if observed else None,
                       problems=[f"{p.file}:{p.line}: {p.error}" for p in arts.problems])


def probe_shape(conn, plan, client, *, sample_rows: int = SAMPLE_ROWS,
                sample_items: int = SAMPLE_ITEMS) -> SourceShape:
    """Observe a source's shape through an open client, READ-ONLY (every call goes through
    `ReadOnlyClient` with the approved plan's deny list). Tools and resource templates; for each
    documents step, the fields of a sample of listed items; for each tabular step, every in-scope
    table's columns, keys and a digest sample of rows (the dialect's own `describe` / `keys` /
    `select` templates). A facet whose probe failed is `None` and the failure is in `problems`."""
    from .client import ReadOnlyClient
    from .crawl import decide, enumerate_documents, enumerate_tables
    from .tabular.profiling import SqlRunner

    _require_approved(plan, conn.id)
    shape = SourceShape(source_id=conn.id)
    problems: list[str] = []
    ro = ReadOnlyClient(client, plan=plan)
    try:
        shape.tools = {t.name: tool_shape(t) for t in ro.list_tools()}
    except Exception as exc:                                         # noqa: BLE001
        problems.append(f"tools: {type(exc).__name__}: {exc}")
    lister = getattr(client, "list_resource_templates", None)
    # a route that cannot observe templates (MCPJungle) leaves the facet None — unobserved, never
    # an empty set that would read as "every template removed"
    if callable(lister) and getattr(client, "supports_resource_templates", True):
        def _get(t, *names):
            for n in names:
                v = t.get(n) if isinstance(t, dict) else getattr(t, n, None)
                if v:
                    return v
            return None

        try:
            shape.resource_templates = {str(_get(t, "uri_template", "uriTemplate") or t):
                                        str(_get(t, "mime", "mimeType") or "")
                                        for t in lister()}
        except Exception as exc:                                     # noqa: BLE001
            problems.append(f"resource templates: {type(exc).__name__}: {exc}")
    doc_steps = [s for s in plan.steps if s.strategy == "documents"]
    if doc_steps:
        entries: list[dict] = []
        ok = True
        for step in doc_steps:
            try:
                for item in itertools.islice(enumerate_documents(ro, step, conn.scope, conn.id),
                                             sample_items):
                    entries.append({k: v for k, v in item.ctx.items()})
            except Exception as exc:                                 # noqa: BLE001
                ok = False
                problems.append(f"item fields ({step.id}): {type(exc).__name__}: {exc}")
        shape.item_fields = fields_shape(entries) if ok else None
    tab_steps = [s for s in plan.steps if s.strategy == "tabular" and s.sql is not None]
    if tab_steps:
        tables: dict[str, TableShape] = {}
        ok = True
        for step in tab_steps:
            try:
                runner = SqlRunner(ro, step)
                for item in enumerate_tables(ro, step, conn.id):
                    # R-022 f1 #12: the ACTUAL number accepted so far, so `max_items` binds here
                    # exactly as it does in the crawl
                    d = decide(item, conn.scope, collected_so_far=len(tables), holds=False)
                    if d.outcome != "collect":
                        # R-022 f2 #16: the exclusion keeps its governing rule and its reversal
                        shape.tables_out_of_scope.append({"uri": item.uri, "rule": d.rule,
                                                          "reverse": d.reverse})
                        continue
                    ctx = {"schema": item.ctx.get("schema") or "", "table": item.ctx.get("table")}
                    cols = [{"name": r.get("column_name"), "type": r.get("data_type")}
                            for r in runner.rows("describe", ctx)]
                    keys = runner.rows("keys", ctx) if runner.has("keys") else []
                    pk = [k["column_name"] for k in keys
                          if str(k.get("constraint_type")).upper() == "PRIMARY KEY"]
                    fks = [{"column": k["column_name"], "ref_schema": k.get("ref_schema"),
                            "ref_table": k.get("ref_table"), "ref_column": k.get("ref_column")}
                           for k in keys
                           if str(k.get("constraint_type")).upper() == "FOREIGN KEY"]
                    rows: list[dict] = []
                    if runner.has("select") and sample_rows > 0:
                        # R-022 f1 #14: the DIALECT's identifier quoting (backticks, brackets)
                        order = ", ".join(runner.quote(c) for c in pk) if pk else "1"
                        rows = runner.rows("select", {**ctx, "order_by": order,
                                                      "limit": sample_rows})
                    tables[item.uri] = table_shape(item.uri, cols, pk, fks, rows)
            except Exception as exc:                                 # noqa: BLE001
                ok = False
                problems.append(f"tables ({step.id}): {type(exc).__name__}: {exc}")
        shape.tables = tables if ok else None
    shape.problems = problems
    return shape


class DriftProbeRefused(ValueError):
    """A drift probe asked for without an approved plan, or for a table outside the scope."""


def _require_approved(plan, source_id: str) -> None:
    """R-022 f1 #12: drift probing reads the source, so it runs only under an APPROVED plan —
    the same gate as a crawl. It never bypasses approval."""
    if plan is None or getattr(plan, "status", None) != "approved":
        raise DriftProbeRefused(
            f"the plan for {source_id!r} is {getattr(plan, 'status', None)!r}: a drift probe "
            f"reads the source only under an APPROVED plan")


def probe_source(source_id: str, *, root: Optional[Path] = None, client=None) -> SourceShape:
    """`probe_shape` for a declared connection and its APPROVED plan, over its own session (a
    pooled lease when pooling is on) unless a client is given."""
    from .client import open_client
    from .connections import load_connection
    from .plans import load_plan

    conn = load_connection(source_id, root)
    plan = load_plan(source_id, root)
    if client is not None:
        return probe_shape(conn, plan, client)
    with open_client(conn) as c:
        return probe_shape(conn, plan, c)


def _split_ref(ref: str) -> tuple[str, str]:
    schema, _, table = ref.rpartition(".")
    return schema, table


def profile_tables(source_id: str, refs: list[str], *, root: Optional[Path] = None,
                   client=None) -> dict[str, dict]:
    """Mapping snapshots for the given tables, in the shape `mapping.py` reads, with MEASURED
    uniqueness (the tabular recipe's own `profile_table` + history detection) — what a new
    table's mapping is proposed from, exactly as the recipe would propose it."""
    from .client import ReadOnlyClient, open_client
    from .connections import load_connection
    from .plans import load_plan
    from .tabular.pipeline import _snapshots_from_profiles
    from .tabular.policy import load_policy
    from .tabular.profiling import SqlRunner, detect_history, profile_table

    from .crawl import Item, decide

    conn = load_connection(source_id, root)
    plan = load_plan(source_id, root)
    _require_approved(plan, source_id)
    steps = [s for s in plan.steps if s.strategy == "tabular" and s.sql is not None]
    if not steps or not refs:
        return {}
    # R-022 f1 #12: every requested table is scope-checked BEFORE any SQL is issued — a ref the
    # declared scope would not collect is refused, never profiled.
    refused = []
    for n, ref in enumerate(refs):
        schema, table = _split_ref(ref)
        item = Item(source_id=source_id, step_id=steps[0].id, kind="table", uri=ref,
                    name=table, rel=ref, mime="application/json",
                    ctx={"schema": schema, "table": table})
        d = decide(item, conn.scope, collected_so_far=n, holds=False)
        if d.outcome != "collect":
            refused.append(f"{ref} ({d.rule})")
    if refused:
        raise DriftProbeRefused(f"outside the declared scope of {source_id!r}: {refused}")

    def run(c) -> dict[str, dict]:
        runner = SqlRunner(ReadOnlyClient(c, plan=plan), steps[0])
        policy = load_policy(dialect=runner.dialect, source_id=source_id, root=root)
        profiles = [profile_table(runner, *_split_ref(ref), policy) for ref in refs]
        detect_history(profiles, policy)
        return _snapshots_from_profiles(profiles)

    if client is not None:
        return run(client)
    with open_client(conn) as c:
        return run(c)


def propose_plan_version(source_id: str, *, root: Optional[Path] = None, client=None):
    """A fresh RULES-ONLY crawl plan from the live tool list (status `proposed`, not saved) — the
    proposal an adapter agent files when a tool its approved plan uses changed or went away. A plan
    with problems cannot be approved (`plans.approve_plan` refuses it)."""
    from .client import open_client
    from .connections import load_connection
    from .plans import plan_from_tools

    conn = load_connection(source_id, root)

    def run(c):
        tools = c.list_tools()
        supports = getattr(c, "supports_resources", True)
        resources = list(c.list_resources()) if supports else []
        return plan_from_tools(conn, tools, has_resources=bool(resources),
                               supports_resources=supports, harness=None, resources=resources)

    if client is not None:
        return run(client)
    with open_client(conn) as c:
        return run(c)


# --- diff ------------------------------------------------------------------------------------------
class Drift(_M):
    drift_id: str = ""
    kind: DriftKind
    unit: str                                  # the baseline unit it belongs to (see UNIT_FACET)
    detail: str
    table: Optional[str] = None
    column: Optional[str] = None
    tool: Optional[str] = None
    field: Optional[str] = None
    old: Any = None
    new: Any = None
    candidate: bool = False                    # True for every `renamed`: never a certainty
    confidence: Optional[float] = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    subject_touching: bool = False
    subject_reason: Optional[str] = None

    def model_post_init(self, _ctx: Any) -> None:
        if not self.drift_id:
            basis = json.dumps({"k": self.kind, "u": self.unit, "c": self.column, "f": self.field,
                                "o": self.old, "n": self.new}, sort_keys=True, default=str)
            self.drift_id = "drift_" + hashlib.sha256(basis.encode()).hexdigest()[:16]

    def units(self) -> list[str]:
        """Every baseline unit this drift moves (a renamed field moves its old unit too)."""
        extra = self.evidence.get("old_unit")
        return [self.unit] + ([str(extra)] if extra else [])

    @property
    def kinds(self) -> set[str]:
        return {self.kind} | ({"subject_touching"} if self.subject_touching else set())


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _pair_renames(removed: dict[str, Any], added: dict[str, Any], *, typed: bool,
                  threshold: float) -> list[tuple[str, str, float]]:
    """Greedy best-first pairs (old, new, similarity) of the same type whose samples overlap."""
    scored = []
    for r, ro in removed.items():
        for a, ao in added.items():
            if typed and getattr(ro, "type", "") != getattr(ao, "type", ""):
                continue
            s = _jaccard(ro.samples, ao.samples)
            if s >= threshold:
                scored.append((s, r, a))
    pairs, used_r, used_a = [], set(), set()
    for s, r, a in sorted(scored, key=lambda x: (-x[0], x[1], x[2])):
        if r in used_r or a in used_a:
            continue
        used_r.add(r)
        used_a.add(a)
        pairs.append((r, a, round(s, 3)))
    return pairs


def _diff_tables(old: dict[str, TableShape], new: dict[str, TableShape], threshold: float
                 ) -> list[Drift]:
    out: list[Drift] = []
    for ref in sorted(set(new) - set(old)):
        t = new[ref]
        out.append(Drift(kind="new_table", unit=f"table:{ref}", table=ref,
                         new={"columns": {c.name: c.type for c in t.columns.values()},
                              "primary_key": t.primary_key},
                         detail=f"new table {ref!r} ({len(t.columns)} columns)"))
    for ref in sorted(set(old) - set(new)):
        out.append(Drift(kind="removed", unit=f"table:{ref}", table=ref,
                         old={"columns": sorted(old[ref].columns)},
                         evidence={"primary_key": list(old[ref].primary_key),
                                   "foreign_keys": list(old[ref].foreign_keys)},
                         detail=f"table {ref!r} is no longer listed"))
    for ref in sorted(set(old) & set(new)):
        o, n = old[ref], new[ref]
        unit = f"table:{ref}"
        removed = {c: o.columns[c] for c in o.columns if c not in n.columns}
        added = {c: n.columns[c] for c in n.columns if c not in o.columns}
        for r, a, s in _pair_renames(removed, added, typed=True, threshold=threshold):
            out.append(Drift(kind="renamed", unit=unit, table=ref, column=a, old=r, new=a,
                             candidate=True, confidence=s,
                             evidence={"type": n.columns[a].type, "sample_overlap": s,
                                       "samples_old": len(o.columns[r].samples),
                                       "samples_new": len(n.columns[a].samples)},
                             detail=f"{ref}: column {r!r} looks renamed to {a!r} (same type "
                                    f"{n.columns[a].type!r}, {s:.0%} of sampled values in "
                                    f"common) — a CANDIDATE, not a certainty"))
            removed.pop(r)
            added.pop(a)
        new_fk = {f["column"]: f for f in n.foreign_keys}
        for c in sorted(added):
            out.append(Drift(kind="added_column", unit=unit, table=ref, column=c,
                             new={"type": added[c].type, "foreign_key": new_fk.get(c)},
                             detail=f"{ref}: new column {c!r} ({added[c].type or 'untyped'})"))
        for c in sorted(removed):
            out.append(Drift(kind="removed", unit=unit, table=ref, column=c,
                             old={"type": removed[c].type},
                             detail=f"{ref}: column {c!r} is gone"))
        for c in sorted(set(o.columns) & set(n.columns)):
            if o.columns[c].type != n.columns[c].type:
                out.append(Drift(kind="type_changed", unit=unit, table=ref, column=c,
                                 old=o.columns[c].type, new=n.columns[c].type,
                                 detail=f"{ref}.{c}: type {o.columns[c].type!r} -> "
                                        f"{n.columns[c].type!r}"))
        if o.primary_key != n.primary_key:
            out.append(Drift(kind="type_changed", unit=unit, table=ref, old=o.primary_key,
                             new=n.primary_key, evidence={"what": "primary_key"},
                             detail=f"{ref}: primary key {o.primary_key} -> {n.primary_key} "
                                    f"(row identity changes)"))
        old_fk = {json.dumps(f, sort_keys=True, default=str) for f in o.foreign_keys}
        for f in n.foreign_keys:
            if f["column"] in added:
                continue                                  # reported with its added column
            if json.dumps(f, sort_keys=True, default=str) not in old_fk:
                out.append(Drift(kind="type_changed", unit=unit, table=ref, column=f["column"],
                                 new=f, evidence={"what": "foreign_key"},
                                 detail=f"{ref}.{f['column']}: foreign key now references "
                                        f"{f.get('ref_table')}.{f.get('ref_column')}"))
        new_fk_set = {json.dumps(f, sort_keys=True, default=str) for f in n.foreign_keys}
        for f in o.foreign_keys:
            if f["column"] in removed:
                continue
            if json.dumps(f, sort_keys=True, default=str) not in new_fk_set:
                out.append(Drift(kind="type_changed", unit=unit, table=ref, column=f["column"],
                                 old=f, evidence={"what": "foreign_key"},
                                 detail=f"{ref}.{f['column']}: foreign key to "
                                        f"{f.get('ref_table')} dropped"))
    return out


def diff(old: Optional[SourceShape], new: SourceShape, *,
         rename_threshold: float = RENAME_MIN_SIMILARITY) -> list[Drift]:
    """Every shape change from `old` to `new`, over facets observed on BOTH sides."""
    if old is None:
        return []
    out: list[Drift] = []
    if old.tools is not None and new.tools is not None:
        for name in sorted(set(new.tools) - set(old.tools)):
            t = new.tools[name]
            kind = "new_write_tool" if t.writes else "tool_added"
            out.append(Drift(kind=kind, unit=f"tool:{name}", tool=name,
                             new=t.model_dump(mode="json"),
                             detail=(f"new tool {name!r} can change the source (by name or "
                                     f"annotation)" if t.writes else
                                     f"new read-only tool {name!r} ({len(t.params)} argument(s))")))
        for name in sorted(set(old.tools) - set(new.tools)):
            out.append(Drift(kind="tool_removed", unit=f"tool:{name}", tool=name,
                             old=old.tools[name].model_dump(mode="json"),
                             detail=f"tool {name!r} is no longer offered"))
        for name in sorted(set(old.tools) & set(new.tools)):
            o, n = old.tools[name], new.tools[name]
            if o.signature != n.signature:
                gone = sorted(set(o.params) - set(n.params))
                came = sorted(set(n.params) - set(o.params))
                retyped = sorted(p for p in set(o.params) & set(n.params)
                                 if o.params[p] != n.params[p])
                req = sorted(set(n.required) - set(o.required))
                out.append(Drift(kind="tool_signature_changed", unit=f"tool:{name}", tool=name,
                                 old={"params": o.params, "required": o.required},
                                 new={"params": n.params, "required": n.required},
                                 detail=f"tool {name!r} changed its arguments: removed {gone}, "
                                        f"added {came}, retyped {retyped}, newly required {req}"))
            if n.writes and not o.writes:
                out.append(Drift(kind="new_write_tool", unit=f"tool:{name}", tool=name,
                                 old={"read_only": o.read_only, "destructive": o.destructive},
                                 new={"read_only": n.read_only, "destructive": n.destructive},
                                 detail=f"tool {name!r} now declares it can change the source"))
    if old.resource_templates is not None and new.resource_templates is not None:
        for t in sorted(set(new.resource_templates) - set(old.resource_templates)):
            out.append(Drift(kind="tool_added", unit=f"resource_template:{t}", new=t,
                             detail=f"new resource template {t!r}"))
        for t in sorted(set(old.resource_templates) - set(new.resource_templates)):
            out.append(Drift(kind="tool_removed", unit=f"resource_template:{t}", old=t,
                             detail=f"resource template {t!r} is no longer offered"))
        # R-022 f2 #18: a template kept under the same URI template but whose metadata changed
        # (its mime type) is a changed signature, not "no change"
        for t in sorted(set(old.resource_templates) & set(new.resource_templates)):
            o_mime, n_mime = old.resource_templates[t], new.resource_templates[t]
            if o_mime != n_mime:
                out.append(Drift(kind="tool_signature_changed", unit=f"resource_template:{t}",
                                 old={"mime": o_mime}, new={"mime": n_mime},
                                 evidence={"what": "resource_template_mime"},
                                 detail=f"resource template {t!r} now serves "
                                        f"{n_mime or 'an unstated type'!r} (was "
                                        f"{o_mime or 'unstated'!r})"))
    if old.item_fields is not None and new.item_fields is not None:
        removed = {f: old.item_fields[f] for f in old.item_fields if f not in new.item_fields}
        added = {f: new.item_fields[f] for f in new.item_fields if f not in old.item_fields}
        for r, a, s in _pair_renames(removed, added, typed=False, threshold=rename_threshold):
            out.append(Drift(kind="renamed", unit=f"item_field:{a}", field=a, old=r, new=a,
                             candidate=True, confidence=s, evidence={"sample_overlap": s,
                                                                     "old_unit":
                                                                         f"item_field:{r}"},
                             detail=f"listing field {r!r} looks renamed to {a!r} ({s:.0%} of "
                                    f"sampled values in common) — a CANDIDATE"))
            removed.pop(r)
            added.pop(a)
        for f in sorted(added):
            out.append(Drift(kind="added_field", unit=f"item_field:{f}", field=f, new=f,
                             detail=f"listed items now carry field {f!r}"))
        for f in sorted(removed):
            out.append(Drift(kind="removed", unit=f"item_field:{f}", field=f, old=f,
                             detail=f"listed items no longer carry field {f!r}"))
    if old.tables is not None and new.tables is not None:
        out.extend(_diff_tables(old.tables, new.tables, rename_threshold))
    if old.file_types is not None and new.file_types is not None:
        for ext in sorted(set(new.file_types) - set(old.file_types)):
            out.append(Drift(kind="new_file_type", unit=f"file_type:{ext}", new=ext,
                             evidence={"items": new.file_types[ext]},
                             detail=f"{new.file_types[ext]} item(s) of a new file type {ext!r}"))
    if old.mimes is not None and new.mimes is not None:
        for m in sorted(set(new.mimes) - set(old.mimes)):
            out.append(Drift(kind="new_mime", unit=f"mime:{m}", new=m,
                             evidence={"items": new.mimes[m]},
                             detail=f"{new.mimes[m]} item(s) captured as a new mime type {m!r}"))
    return out


def subject_identifiers(vocab) -> dict[str, str]:
    """{identifier (lower-case) -> subject kind} over every declared subject kind."""
    out: dict[str, str] = {}
    for kind in sorted(getattr(vocab, "subject_kinds", {}) or {}):
        for ident in vocab.identifiers_for(kind):
            out.setdefault(str(ident).lower(), kind)
    return out


def _names_in(value: Any) -> list[str]:
    """Every string in a drift's old/new/evidence structure, and every key of a `columns`
    mapping — the column names a schema fragment mentions."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k == "columns" and isinstance(v, dict):
                out.extend(str(x) for x in v)
            out.extend(_names_in(v))
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            out.extend(_names_in(v))
    return out


def mark_subject(drifts: list[Drift], *, vocab, table_kinds: Optional[dict[str, str]] = None
                 ) -> list[Drift]:
    """Flag every drift that touches a pack SUBJECT (spine D4b): on a table mapped to — or proposed
    for — a subject kind (`table_kinds`: table ref -> kind), or on a column / field named like a
    declared subject identifier. Returns the same list, flags set in place."""
    if vocab is None:
        return drifts
    subjects = set(getattr(vocab, "subject_kinds", {}) or {})
    idents = subject_identifiers(vocab)
    kinds = table_kinds or {}
    for d in drifts:
        why = None
        k = kinds.get(d.table or "")
        if d.table and k in subjects:
            why = f"table {d.table!r} maps to subject kind {k!r}"
        names = [n for n in (d.column, d.field) if n]
        # R-022 f1 #17: every column name the drift carries — old AND new schemas (a removed
        # table's columns, primary-key lists, foreign-key structures), recursively
        for part in (d.old, d.new, d.evidence):
            names.extend(_names_in(part))
        for n in names:
            if str(n).lower() in idents and why is None:
                why = (f"{n!r} is a declared identifier of subject kind "
                       f"{idents[str(n).lower()]!r}")
        if why:
            d.subject_touching, d.subject_reason = True, why
    return drifts


__all__ = ["SourceShape", "ToolShape", "TableShape", "ColumnShape", "FieldShape", "Drift",
           "DRIFT_KINDS", "FACETS", "UNIT_FACET", "RENAME_MIN_SIMILARITY", "diff", "advance",
           "tool_shape", "table_shape", "fields_shape", "shape_from_run", "probe_shape",
           "probe_source", "profile_tables", "propose_plan_version", "mark_subject",
           "DriftProbeRefused", "DriftAdvanceRefused", "unapproved_in_units",
           "subject_identifiers", "value_digest",
           "extractor_exists", "ext_of_name"]
