"""The typed contracts of delegated querying — frozen, closed (``extra="forbid"``).

    DelegationPlan = SubTasks (one per adapter agent read) + one CombineSpec
    SubTask        → a delegate → SubResult (rows of GroundedValues, coverage, produced_by, timings)
    SubResults     → the combine engine → a table of GroundedValues with derived pointers

A plan is DATA. The model may propose one; `planner.validate_plan` checks every name in it against
the catalogue, and nothing in a plan is executed as code. Periods are SYMBOLIC (``this_quarter``)
and resolved by `resolve_period` from an injected clock, so the model never does date arithmetic
either.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import GroundedValue, SourcePointer
from databridge_core.provenance import SnapshotIdentity

#: Field names a plan may use: identifiers only, so a name can never carry SQL.
FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
#: Names that address the delegate's own row metadata rather than a column.
TEXT_FIELD = "text"            # a passage's text (documents channel)
DATE_FIELD = "date"            # a document node's GROUNDED `date` attribute (documents channel);
                               # never its valid_from: an undated node has an unknown date
#: `EntityRef.via` of a documents row's OWN node (the row IS that entity; E2E N-02).
SELF_VIA = "self"
#: A tabular row's RECORD TIME (DSD): the instant the facts copy it was read from was extracted —
#: when the platform's copy of the row became valid. A metadata column, cited like any other cell
#: (a `query_cell` of the same statement); asked for with ``SubTask.record_time``.
RECORD_TIME_FIELD = "_record_as_of"
#: A symbolic filter value: the injected clock's date, resolved deterministically before any read
#: (`resolve_symbols`). The model never writes a date; "due before today" is ``due_on lt @today``.
TODAY_TOKEN = "@today"

FilterOp = Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "is_null", "not_null"]
AggOp = Literal["count", "sum", "avg", "min", "max"]
Axis = Literal["valid_time", "system_time"]
PERIODS = ("this_quarter", "last_quarter", "this_month", "last_month", "this_year", "last_year")
Period = Literal["this_quarter", "last_quarter", "this_month", "last_month", "this_year",
                 "last_year"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


def _field_name(name: str, what: str) -> str:
    if not FIELD_RE.match(name or ""):
        raise ValueError(f"{what} {name!r} is not a plain field name")
    return name


# ------------------------------------------------------------------------------------------------
# periods — deterministic, from an injected clock
# ------------------------------------------------------------------------------------------------
def _quarter_start(d: dt.date) -> dt.date:
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def resolve_period(period: str, today: dt.date) -> tuple[dt.date, dt.date]:
    """``[start, end)`` of a symbolic period relative to ``today``. End is exclusive."""
    if period in ("this_quarter", "last_quarter"):
        start = _quarter_start(today)
        if period == "last_quarter":
            start = _add_months(start, -3)
        return start, _add_months(start, 3)
    if period in ("this_month", "last_month"):
        start = dt.date(today.year, today.month, 1)
        if period == "last_month":
            start = _add_months(start, -1)
        return start, _add_months(start, 1)
    if period in ("this_year", "last_year"):
        y = today.year - (1 if period == "last_year" else 0)
        return dt.date(y, 1, 1), dt.date(y + 1, 1, 1)
    raise ValueError(f"unknown period {period!r}; expected one of {PERIODS}")


# ------------------------------------------------------------------------------------------------
# a sub-task
# ------------------------------------------------------------------------------------------------
class Filter(_Frozen):
    """``field op value``. ``contains`` over ``text`` is a case-insensitive substring test over
    passages (documents) or a column (tabular). Values are DATA — bound as SQL parameters, never
    spliced."""

    field: str
    op: FilterOp
    value: Any = None

    @model_validator(mode="after")
    def _shape(self) -> "Filter":
        _field_name(self.field, "filter field")
        if self.op in ("is_null", "not_null"):
            if self.value is not None:
                raise ValueError(f"{self.op} takes no value")
        elif self.op == "in":
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise ValueError("`in` takes a non-empty list")
        elif self.value is None:
            raise ValueError(f"filter {self.field} {self.op} needs a value")
        if isinstance(self.value, (dict,)):
            raise ValueError("a filter value is a scalar or a list of scalars")
        return self

    def describe(self) -> str:
        return f"{self.field} {self.op} {self.value!r}" if self.value is not None else \
            f"{self.field} {self.op}"


class TimeWindow(_Frozen):
    """A window on one time axis. ``valid_time`` + ``field`` filters a row's own date column (or,
    for documents, the node's own GROUNDED date attribute, ``date`` by default; never its
    valid_from: an undated node is excluded and counted); ``system_time`` reads the graph AS BELIEVED at the
    window's end (bi-temporal as-of). Either a symbolic ``period`` or explicit ``from``/``to``
    (end exclusive)."""

    axis: Axis = "valid_time"
    field: Optional[str] = None
    period: Optional[Period] = None
    from_: Optional[dt.date] = Field(default=None, alias="from")
    to: Optional[dt.date] = None

    @model_validator(mode="after")
    def _shape(self) -> "TimeWindow":
        if self.field is not None:
            _field_name(self.field, "time-window field")
        if self.period is None and (self.from_ is None or self.to is None):
            raise ValueError("a time window names a period, or both `from` and `to`")
        if self.period is not None and (self.from_ is not None or self.to is not None):
            raise ValueError("a time window names a period OR from/to, not both")
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("time window `from` must be before `to`")
        return self

    def bounds(self, today: dt.date) -> tuple[dt.date, dt.date]:
        if self.period is not None:
            return resolve_period(self.period, today)
        return self.from_, self.to                                   # type: ignore[return-value]

    def label(self) -> str:
        return self.period or f"{self.from_}..{self.to}"


class Aggregate(_Frozen):
    op: AggOp
    field: Optional[str] = None
    group_by: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "Aggregate":
        if self.op != "count" and not self.field:
            raise ValueError(f"{self.op} needs a field")
        if self.field:
            _field_name(self.field, "aggregate field")
        for g in self.group_by:
            _field_name(g, "group_by field")
        return self

    def label(self) -> str:
        return f"{self.op}_{self.field}" if self.field else "count"


class SubTask(_Frozen):
    """One read an adapter agent's delegate performs. Read-only: there is no write verb."""

    id: str = "t1"
    adapter_agent: str
    connection: Optional[str] = None          # pin one of the agent's connections (else chosen)
    entity_kinds: tuple[str, ...] = ()        # tabular: a facts table; documents: node kinds
    filters: tuple[Filter, ...] = ()
    fields: tuple[str, ...] = ()
    time_window: Optional[TimeWindow] = None
    aggregate: Optional[Aggregate] = None
    limit: int = Field(1000, ge=1, le=5000)
    freshness: Literal["stored", "live"] = "stored"
    #: documents only: which graph edges from a document node count as "mentions an entity"
    #: (empty ⇒ every edge kind the vocabulary declares from that node kind).
    link_edges: tuple[str, ...] = ()
    #: tabular rows only (DSD): also return each row's RECORD TIME (`RECORD_TIME_FIELD`), read in
    #: the same statement from the facts store's own extraction record. Time as a column, never a
    #: filter.
    record_time: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "SubTask":
        if not re.match(r"^[a-z][a-z0-9_-]{0,31}$", self.id):
            raise ValueError(f"subtask id {self.id!r} must be a short lowercase id")
        for f in self.fields:
            _field_name(f, "field")
        return self


# ------------------------------------------------------------------------------------------------
# a sub-result
# ------------------------------------------------------------------------------------------------
class EntityRef(_Frozen):
    """A graph entity a document row is about. ``pointer`` is the evidence for the association:
    the EDGE's pointer for a neighbour, the node's own pointer for the row's own node
    (``via == SELF_VIA``). ``path`` carries any further evidence the association rests on (the
    first edge of a co-mention, E2E N-02). A subject kind (a person) is only ever a CANDIDATE."""

    node_id: str
    kind: str
    label: str
    canonical_id: Optional[str] = None
    subject: bool = False
    via: str = ""
    pointer: SourcePointer
    path: tuple[SourcePointer, ...] = ()


class ResultRow(_Frozen):
    row_id: str
    cells: dict[str, GroundedValue]
    entities: tuple[EntityRef, ...] = ()


class SkippedRead(_Frozen):
    adapter_agent: Optional[str] = None
    connection: Optional[str] = None
    channel: Optional[str] = None
    reason: str


class DelegateCoverage(_Frozen):
    sources_read: tuple[str, ...] = ()
    skipped: tuple[SkippedRead, ...] = ()
    truncated: bool = False
    notes: tuple[str, ...] = ()
    #: rows/values that were read but could not be used (no pointer, unknown date, non-numeric,
    #: NULL, …) — each a plain sentence with its count. Surfaced as reduced coverage, never silent.
    excluded: tuple[str, ...] = ()
    #: False when the read was a ranked search (not an exhaustive scan): unreturned matches were
    #: never considered, so a cohort built from it may be incomplete.
    exhaustive: bool = True
    #: E2E3 P-01: a text filter value that matched no stored value exactly and was READ AS the one
    #: stored value containing all its words (each a plain sentence; shown as an assumption)
    resolved: tuple[str, ...] = ()
    #: why this delegate's (empty) result may not show that nothing qualifies — e.g. a filter
    #: value that several stored values contain. Surfaced as UNCERTAIN coverage, never silent.
    uncertain: tuple[str, ...] = ()


class SubResult(_Frozen):
    subtask_id: str
    adapter_agent: str
    connection: Optional[str] = None
    method: str = ""                   # facts_sql_pushdown | facts_sql_rows | graph_passages | live
    rows: tuple[ResultRow, ...] = ()
    coverage: DelegateCoverage = DelegateCoverage()
    produced_by: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, int] = Field(default_factory=dict)
    queries: int = 0                   # data queries this delegate issued (pushdown proof)
    snapshot: Optional[SnapshotIdentity] = None
    live: bool = False
    error: Optional[str] = None
    #: tabular only: the column holding a row's stable id (for sample_row_ids / rows_digest)
    key_field: Optional[str] = None


# ------------------------------------------------------------------------------------------------
# combining
# ------------------------------------------------------------------------------------------------
class LinkSpec(_Frozen):
    """Keep (``semi``) or drop (``anti``) base rows linked to a row of another sub-result.

    The link is EXACT, never fuzzy: base ``key`` equals the other row's cell ``other_field``, or
    the ``canonical_id`` of an entity (of ``entity_kind``) the other row mentions — directly
    (``via: canonical_id``) or through declared graph edges within ``max_hops`` (``via: graph``:
    the node holding exactly that canonical id must be reachable from the mentioned entity). A
    subject kind (a person) links only by exact identifier, and every row admitted through one is
    marked a CANDIDATE.

    A FIELD link (``other_field``) names ``key_kind``: the ontology kind the shared identifier
    denotes (validated against the vocabulary). Equal ``person_id`` columns are still only a
    candidate match, so a field link whose kind is a subject kind marks its rows candidates, and a
    field link whose kind cannot be established is not run."""

    subtask: str
    key: str
    entity_kind: Optional[str] = None
    other_field: Optional[str] = None
    key_kind: Optional[str] = None
    via: Literal["canonical_id", "graph"] = "canonical_id"
    max_hops: int = Field(0, ge=0, le=3)
    edge_kinds: tuple[str, ...] = ()
    mode: Literal["semi", "anti"] = "semi"
    #: a LOOKUP (E2E N-03): fields of the matched other row copied onto the base row, each with its
    #: own pointer — e.g. invoices.customer_id = customers.customer_id carrying account_owner. Only
    #: on a semi field link; a base row whose matches disagree on a carried value is excluded and
    #: counted, never resolved by picking one.
    carry: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "LinkSpec":
        _field_name(self.key, "link key")
        if self.other_field is not None:
            _field_name(self.other_field, "link other_field")
        for c in self.carry:
            _field_name(c, "carried field")
        if self.carry and (self.other_field is None or self.mode != "semi"):
            raise ValueError("only a semi field link (other_field) can carry fields onto the "
                             "base rows")
        if (self.entity_kind is None) == (self.other_field is None):
            raise ValueError("a link matches an entity_kind OR an other_field, exactly one")
        if self.via == "graph" and self.max_hops < 1:
            raise ValueError("a graph link needs max_hops >= 1")
        if self.via == "graph" and not self.edge_kinds:
            # an undeclared edge set would traverse EVERY relationship in the graph
            raise ValueError("a graph link names the edge kinds it may traverse (edge_kinds)")
        if self.other_field is not None and not self.key_kind:
            raise ValueError("a field link names key_kind: the ontology kind its identifier "
                             "denotes (a person identifier is only ever a candidate match)")
        if self.other_field is None and self.key_kind is not None:
            raise ValueError("key_kind belongs to a field link (other_field)")
        if self.via == "graph" and self.other_field is not None:
            raise ValueError("a graph link reaches an entity_kind, not an other_field")
        if self.via == "canonical_id" and self.max_hops:
            raise ValueError("a canonical_id link is direct (max_hops 0)")
        return self


class CompareSpec(_Frozen):
    """Compare the aggregate across periods of ``field`` (a date on the base rows)."""

    axis: Axis = "valid_time"
    field: str
    periods: tuple[Period, ...] = ("this_quarter", "last_quarter")

    @model_validator(mode="after")
    def _shape(self) -> "CompareSpec":
        _field_name(self.field, "compare field")
        if len(self.periods) < 2 or len(set(self.periods)) != len(self.periods):
            raise ValueError("compare names at least two distinct periods")
        return self


class CombineSpec(_Frozen):
    base: str
    links: tuple[LinkSpec, ...] = ()
    filters: tuple[Filter, ...] = ()
    group_by: tuple[str, ...] = ()
    aggregate: Optional[Aggregate] = None
    compare: Optional[CompareSpec] = None
    order_by: Optional[str] = None
    descending: bool = True
    top_k: Optional[int] = Field(None, ge=1, le=1000)

    @model_validator(mode="after")
    def _shape(self) -> "CombineSpec":
        for g in self.group_by:
            _field_name(g, "group_by field")
        if self.order_by is not None:
            _field_name(self.order_by.split(":", 1)[0], "order_by")
        if self.compare is not None and self.aggregate is None:
            raise ValueError("a period comparison compares an aggregate; name one")
        if self.aggregate is not None and self.aggregate.group_by and                 tuple(self.aggregate.group_by) != tuple(self.group_by):
            # one grouping, one place: an ignored aggregate.group_by would silently change the answer
            raise ValueError("put the grouping in combine.group_by (aggregate.group_by differs "
                             "from it)")
        return self


class DelegationPlan(_Frozen):
    """What the planner returns. ``questions`` non-empty ⇒ nothing runs: the user is asked."""

    subtasks: tuple[SubTask, ...] = ()
    combine: Optional[CombineSpec] = None
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "DelegationPlan":
        ids = [s.id for s in self.subtasks]
        if len(ids) != len(set(ids)):
            raise ValueError("subtask ids must be unique")
        if self.combine is not None:
            known = set(ids)
            if self.combine.base not in known:
                raise ValueError(f"combine.base {self.combine.base!r} is not a subtask id")
            for link in self.combine.links:
                if link.subtask not in known or link.subtask == self.combine.base:
                    raise ValueError(f"link subtask {link.subtask!r} must be another subtask")
        return self


def resolve_value(value: Any, today: dt.date) -> Any:
    """``@today`` → the clock's ISO date (also inside an ``in`` list); anything else unchanged."""
    if isinstance(value, str) and value.strip().lower() == TODAY_TOKEN:
        return today.isoformat()
    if isinstance(value, (list, tuple)):
        return type(value)(resolve_value(v, today) for v in value)
    return value


def resolve_symbols(plan: "DelegationPlan", today: dt.date) -> "DelegationPlan":
    """Every symbolic filter value resolved from the injected clock (no model date arithmetic)."""
    def fix(filters: tuple[Filter, ...]) -> tuple[Filter, ...]:
        return tuple(f.model_copy(update={"value": resolve_value(f.value, today)})
                     for f in filters)
    subtasks = tuple(t.model_copy(update={"filters": fix(t.filters)}) for t in plan.subtasks)
    combine = (plan.combine.model_copy(update={"filters": fix(plan.combine.filters)})
               if plan.combine is not None else None)
    return plan.model_copy(update={"subtasks": subtasks, "combine": combine})


__all__ = ["FIELD_RE", "RECORD_TIME_FIELD", "TEXT_FIELD", "DATE_FIELD", "SELF_VIA", "TODAY_TOKEN", "resolve_value",
           "resolve_symbols", "PERIODS", "resolve_period", "Filter",
           "TimeWindow", "Aggregate", "SubTask", "EntityRef", "ResultRow", "SkippedRead",
           "DelegateCoverage", "SubResult", "LinkSpec", "CompareSpec", "CombineSpec",
           "DelegationPlan"]
