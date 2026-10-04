"""The deterministic combine engine — NO model arithmetic, ever.

    base rows ─ links (exact: same canonical id, or declared graph edges within N hops) ─ filters
              ─ period split (one date field, symbolic periods from an injected clock)
              ─ group_by ─ aggregate (Decimal-exact) ─ comparison ─ order ─ top-k

Every output value is a `GroundedValue` whose pointer is ``derived`` (see `pointers.py`):
``derived_from`` lists EXACTLY the input cells that contributed. A group key's pointer lists the
key cells of the rows in the group; a comparison's lists the two aggregate pointers it subtracts.
When links or combine filters selected the rows, every value (a selected cell, a group key, an
aggregate) also derives from ONE ``ELIGIBILITY`` pointer whose inputs are exactly the evidence
that made those rows eligible: the link evidence and the filtered cells (R-021 r2 #8).
Rows admitted through a subject-kind (person) link are marked CANDIDATES: the link is an exact
identifier match, and who someone is remains a person's call (CLAUDE.md hard constraint 1). For
the same reason an ANTI-link never drops a row on a candidate match: that would decide who someone
is not, so the combine refuses (R-021 r2 #2).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional

from databridge_core import GroundedValue, SourcePointer

from .catalogue import Catalogue
from .contracts import CombineSpec, Filter, LinkSpec, ResultRow, SubResult, resolve_period
from .delegates import to_decimal
from .pointers import derived_pointer


class CombineError(ValueError):
    """The combine spec cannot be applied to these results."""


@dataclass
class TableRow:
    cells: dict[str, Optional[GroundedValue]]
    candidate: bool = False
    link_evidence: list[SourcePointer] = field(default_factory=list)
    row_ids: list[str] = field(default_factory=list)


@dataclass
class CombinedTable:
    columns: list[str]
    rows: list[TableRow]
    steps: list[dict]
    absent: list[str] = field(default_factory=list)
    #: inputs that were read but could not contribute (NULL, non-numeric, unknown date) - each a
    #: plain sentence; surfaced as reduced coverage, never silent
    excluded: list[str] = field(default_factory=list)
    #: why this table may be emptier than the truth (E2E N-02): a link that matched no row, a link
    #: whose other side offered no identifier, found entities with no identifier to link by.
    #: Each a plain sentence; the answer's coverage is then UNCERTAIN, never "complete".
    uncertain: list[str] = field(default_factory=list)
    #: E2E4 Q-06: facts about coverage that do NOT reduce it (an unlinked entity PROVEN unable
    #: to hold any row the link left out). Shown with the coverage, never silent.
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        def cell(gv: Optional[GroundedValue]) -> Optional[dict]:
            if gv is None:
                return None
            v = gv.value
            return {"value": (str(v) if isinstance(v, Decimal) else v),
                    "pointer": gv.pointer.model_dump(mode="json", exclude_none=True)}
        return {"columns": list(self.columns),
                "rows": [{"cells": {c: cell(r.cells.get(c)) for c in self.columns},
                          "candidate": r.candidate,
                          **({"requires_human_selection": True} if r.candidate else {}),
                          "link_evidence": [p.model_dump(mode="json", exclude_none=True)
                                            for p in r.link_evidence]}
                         for r in self.rows],
                "absent": list(self.absent), "excluded": list(self.excluded),
                "uncertain": list(self.uncertain), "notes": list(self.notes),
                "steps": list(self.steps)}


# ------------------------------------------------------------------------------------------------
def _value(row: ResultRow, name: str) -> Any:
    gv = row.cells.get(name)
    return None if gv is None else gv.value


def _date(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _matches(row: ResultRow, f: Filter) -> bool:
    v = _value(row, f.field)
    if f.op == "is_null":
        return v is None
    if f.op == "not_null":
        return v is not None
    if v is None:
        return False
    if f.op == "contains":
        return str(f.value).lower() in str(v).lower()
    if f.op == "in":
        return str(v) in {str(x) for x in f.value}
    a, b = to_decimal(v), to_decimal(f.value)
    if a is None or b is None:
        a, b = str(v), str(f.value)
    return {"eq": a == b, "ne": a != b, "lt": a < b, "le": a <= b, "gt": a > b,
            "ge": a >= b}[f.op]


Admits = Callable[[str], bool]


def _admitted(sources: Any, admits: Admits) -> Optional[SourcePointer]:
    for p in sources or ():
        if admits(p.source_id):
            return p
    return None


def _touching_sql(n_kinds: int) -> str:
    """The open edges of the given kinds touching one node, through `ix_edges_src` /
    `ix_edges_dst`, oldest first — a self-loop twice, as a whole-graph adjacency list held it."""
    ks = ", ".join("?" for _ in range(n_kinds))
    return (f"SELECT * FROM edges WHERE system_to IS NULL AND src_id = ? AND kind IN ({ks}) "
            f"UNION ALL "
            f"SELECT * FROM edges WHERE system_to IS NULL AND dst_id = ? AND kind IN ({ks}) "
            f"ORDER BY rowid_pk")


def _graph_reach(store: Any, start: str, *, hops: int, edge_kinds: set[str], kind: str,
                 admits: Admits, cache: Optional[dict] = None
                 ) -> list[tuple[Any, list[SourcePointer], list[str]]]:
    """Nodes of ``kind`` reachable from ``start`` within ``hops`` over the DECLARED edge kinds
    only, each with the edge pointers along the path (the link's evidence) and the kinds of EVERY
    node on the path. Only nodes and edges whose provenance the caller may read are traversed: a
    path that needs an unadmitted node or edge does not exist for this caller.

    E2E run 5, F-05/F-07: the edges of each node the walk reaches are read by index, when it
    reaches it — not every open edge of the graph once per row (201 s for 21 rows on the finance
    graph). ``cache`` (one per link) keeps nodes and adjacency across the rows of one link."""
    from databridge_graph.store import row_to_edge, row_to_node  # noqa: PLC0415

    if not edge_kinds:                        # an undeclared edge set traverses nothing
        return []
    cache = cache if cache is not None else {}
    visible: dict[str, Any] = cache.setdefault(("nodes",), {})
    adj: dict[str, list[tuple[Any, SourcePointer]]] = cache.setdefault(
        ("adj", tuple(sorted(edge_kinds))), {})
    edge_list = sorted(edge_kinds)

    def node(nid: str) -> Any:
        if nid not in visible:
            row = store.open_node_row(nid)
            n = row_to_node(store, row) if row is not None else None
            visible[nid] = n if (n is not None and _admitted(n.sources, admits)) else None
        return visible[nid]

    def touching(nid: str) -> list[tuple[Any, SourcePointer]]:
        if nid not in adj:
            out = []
            for r in store.fetchall(_touching_sql(len(edge_list)),
                                    (nid, *edge_list, nid, *edge_list)):
                e = row_to_edge(store, r)
                ptr = _admitted(e.sources, admits)
                if ptr is not None:
                    out.append((e, ptr))
            adj[nid] = out
        return adj[nid]

    first = node(start)
    if first is None:
        return []
    seen: dict[str, tuple[list[SourcePointer], list[str]]] = {
        start: ([], [str(getattr(first.kind, "value", first.kind))])}
    q = deque([(start, 0)])
    while q:
        nid, d = q.popleft()
        if d >= hops:
            continue
        for e, ptr in touching(nid):
            o = e.dst_id if e.src_id == nid else e.src_id
            if o in seen:
                continue
            on = node(o)
            if on is None:
                continue
            path, kinds = seen[nid]
            seen[o] = (path + [ptr], kinds + [str(getattr(on.kind, "value", on.kind))])
            q.append((o, d + 1))
    out = []
    for nid, (path, kinds) in seen.items():
        if nid == start:
            continue
        if kinds[-1] == kind:
            out.append((node(nid), path, kinds))
    return out


def _link_index(link: LinkSpec, other: SubResult, *, store: Any, catalogue: Catalogue,
                admits: Optional[Admits] = None
                ) -> dict[str, list[tuple[list[SourcePointer], bool]]]:
    """identifier -> [(evidence pointers, candidate?)]. Exact strings only.

    * a FIELD link is a candidate link when the identifier's declared kind (``key_kind``) is a
      subject kind: two ``person_id`` columns being equal says nothing about who someone is;
    * a ``canonical_id`` link is a candidate link when the mentioned entity is a subject kind;
    * a ``graph`` link accepts an identifier ONLY from an actual path over the declared edges
      (no direct-mention shortcut), and is a candidate link when ANY node on the path (start,
      intermediate or target) is a subject kind.

    ``admits`` is the caller's read scope over graph provenance; without one only the other
    sub-result's own connection is visible (fail closed)."""
    idx: dict[str, list[tuple[list[SourcePointer], bool]]] = {}
    scope: Admits = admits if admits is not None else (lambda sid: sid == other.connection)
    field_subject = link.key_kind is not None and catalogue.is_subject(link.key_kind)
    reach_cache: dict = {}                    # one link's graph reads, shared across its rows
    for row in other.rows:
        if link.other_field is not None:
            gv = row.cells.get(link.other_field)
            if gv is not None and gv.value is not None:
                idx.setdefault(str(gv.value), []).append(([gv.pointer], field_subject))
            continue
        base_evidence = [gv.pointer for gv in row.cells.values()][:1]
        for ent in row.entities:
            if link.via == "canonical_id":
                if ent.kind == link.entity_kind and ent.canonical_id:
                    idx.setdefault(str(ent.canonical_id), []).append(
                        (base_evidence + [*ent.path, ent.pointer],
                         catalogue.is_subject(ent.kind)))
                continue
            if store is None:
                raise CombineError("a graph link needs the graph store")
            for node, path, kinds in _graph_reach(store, ent.node_id, hops=link.max_hops,
                                                  edge_kinds=set(link.edge_kinds),
                                                  kind=str(link.entity_kind), admits=scope,
                                                  cache=reach_cache):
                if node.canonical_id:
                    through_subject = any(catalogue.is_subject(k) for k in kinds)
                    idx.setdefault(str(node.canonical_id), []).append(
                        (base_evidence + [*ent.path, ent.pointer, *path], through_subject))
    return idx


def _unidentified(link: LinkSpec, other: SubResult) -> list[str]:
    """Labels of the other side's entities of the link's kind that carry NO canonical id: they
    were found, but cannot be linked by identifier (and are never linked by name)."""
    if link.other_field is not None or link.via != "canonical_id":
        return []
    seen: dict[str, str] = {}
    for row in other.rows:
        for ent in row.entities:
            if ent.kind == link.entity_kind and not ent.canonical_id:
                seen.setdefault(ent.node_id, ent.label)
    return sorted(seen.values())


_WORD = re.compile(r"[^\W_]+")


def _words(text: Any) -> set[str]:
    return set(_WORD.findall(str(text or "").casefold()))


def _resembles(label: str, name: str) -> bool:
    """True when ``label`` COULD be another wording of ``name``: they share at least half of the
    words of the shorter one (case-folded; "Corriedale" ~ "Corriedale Grocers Co-operative",
    "Pellucid Ltd" ~ "Pellucid Freightways Ltd"). A name with no words resembles everything.
    Used only to PROVE that an entity is NOT a record — never to link one by name."""
    a, b = _words(label), _words(name)
    if not a or not b:
        return True
    return 2 * len(a & b) >= min(len(a), len(b))


def _unholders(link: LinkSpec, unidentified: list[str], unmatched: set[str], *, store: Any,
               catalogue: Catalogue, admits: Admits) -> tuple[list[str], dict[str, str]]:
    """E2E4 Q-06: which of the link's UNIDENTIFIED entities (no canonical id, so the link's match
    rule cannot apply to them) are PROVEN unable to change this link's outcome, and the labels of
    the ``unmatched`` identifiers' holders they were compared against.

    Only the rows whose identifier the link did NOT match could be changed by a missed mention.
    Each such identifier's holder is read from the graph (the open ``entity_kind`` node with that
    canonical id, visible in the caller's scope). An unidentified entity is proven unrelated when
    every holder is known and its label resembles none of them. Never for a subject kind (a
    person): who someone is, or is not, is a person's call. Anything unknown proves nothing."""
    if not unidentified or link.via != "canonical_id" or link.other_field is not None \
            or catalogue.is_subject(str(link.entity_kind)):
        return [], {}
    holders: dict[str, str] = {}
    if unmatched:
        if store is None:
            return [], {}
        from databridge_graph.store import row_to_node  # noqa: PLC0415

        for key in sorted(unmatched):
            labels = []
            for r in store.fetchall("SELECT * FROM nodes WHERE kind = ? AND canonical_id = ? "
                                    "AND system_to IS NULL", (str(link.entity_kind), key)):
                n = row_to_node(store, r)
                if n is not None and _admitted(n.sources, admits) is not None and n.label:
                    labels.append(str(n.label))
            if not labels:
                return [], {}                          # a holder we cannot name proves nothing
            holders[key] = " / ".join(sorted(set(labels)))
    proven = [u for u in unidentified
              if not any(_resembles(u, name) for name in holders.values())]
    return proven, holders


def _kind_word(key: str, entity_kind: str) -> str:
    """What the base key's holders are called, from the key's own name (``customer_id`` ->
    ``customer``); the entity kind when the key names no holder."""
    m = re.fullmatch(r"(.+?)(?:[_\s-]+[iI][dD]|(?<=[a-z])Id)", key or "")
    return m.group(1).replace("_", " ") if m and m.group(1) else f"{entity_kind} holding a {key}"


def _carry_index(link: LinkSpec, other: SubResult) -> dict[str, list[ResultRow]]:
    """identifier -> the other side's rows holding it (a lookup link's matches)."""
    out: dict[str, list[ResultRow]] = {}
    for row in other.rows:
        gv = row.cells.get(str(link.other_field))
        if gv is not None and gv.value is not None:
            out.setdefault(str(gv.value), []).append(row)
    return out


def _carried(link: LinkSpec, matches: list[ResultRow]
             ) -> tuple[Optional[dict[str, GroundedValue]], Optional[str]]:
    """The carried cells for one base row, or (None, why) when its matches disagree. A lookup is a
    function of the key: two different values for one key is ambiguous, so the row is excluded
    rather than resolved by picking one."""
    out: dict[str, GroundedValue] = {}
    for f in link.carry:
        vals = {repr(m.cells[f].value): m.cells[f] for m in matches if f in m.cells}
        if not vals:
            return None, f"the matched row has no field {f!r}"
        if len(vals) > 1:
            return None, f"{len(vals)} different {f!r} values for one key"
        out[f] = next(iter(vals.values()))
    return out, None


def _aggregate(op: str, rows: list[ResultRow], fld: Optional[str], key_field: str
               ) -> tuple[Any, list[SourcePointer], list[ResultRow], list[str]]:
    """(value, the exact input pointers, the contributing rows, notes). Decimal-exact.

    * ``count`` (no field) counts rows; ``count(field)`` counts rows whose field is not NULL (the
      SQL definition, the same one the pushed-down path uses);
    * a NULL input does not contribute and is NOTED;
    * a non-NULL input that is not a number makes the value UNKNOWN (``None``) with a note: a
      total over the numeric values only would look complete and is not."""
    notes: list[str] = []
    if op == "count" and not fld:
        ptrs = [r.cells[key_field].pointer for r in rows if key_field in r.cells]
        if len(ptrs) != len(rows):
            notes.append(f"{len(rows) - len(ptrs)} row(s) have no citable {key_field!r}; the "
                         f"count is not given")
            return None, [], rows, notes
        return len(rows), ptrs, rows, notes
    name = fld or ""
    present = [r for r in rows if r.cells.get(name) is not None and r.cells[name].value is not None]
    nulls = len(rows) - len(present)
    if nulls:
        notes.append(f"{nulls} row(s) have NULL {name} and did not contribute to {op}({name})")
    if op == "count":
        return len(present), [r.cells[name].pointer for r in present], present, notes
    vals: list[Decimal] = []
    bad: list[ResultRow] = []
    for r in present:
        d = to_decimal(r.cells[name].value)
        if d is None:
            bad.append(r)
        else:
            vals.append(d)
    if bad:
        notes.append(f"{len(bad)} non-numeric {name} value(s) (e.g. row {bad[0].row_id!r} = "
                     f"{bad[0].cells[name].value!r}); no {op} is given rather than one computed "
                     f"over the numeric values only")
        return None, [], present, notes
    if not vals:
        return None, [], [], notes
    ptrs = [r.cells[name].pointer for r in present]
    if op == "sum":
        return sum(vals, Decimal(0)), ptrs, present, notes
    if op == "avg":
        return sum(vals, Decimal(0)) / Decimal(len(vals)), ptrs, present, notes
    return (min(vals) if op == "min" else max(vals)), ptrs, present, notes


def _order(rows: list, value_of: Callable[[Any], Any], *, descending: bool) -> list:
    """Order by ONE column: numerically when every non-NULL value is a number, otherwise as text
    (never a silent fall-back to another column); NULLs last either way. Stable."""
    have = [r for r in rows if value_of(r) is not None]
    nulls = [r for r in rows if value_of(r) is None]
    nums = [to_decimal(value_of(r)) for r in have]
    if all(n is not None for n in nums):
        have = [r for r, _n in sorted(zip(have, nums), key=lambda x: x[1], reverse=descending)]
    else:
        have = sorted(have, key=lambda r: str(value_of(r)), reverse=descending)
    return have + nulls


def combine(spec: CombineSpec, results: dict[str, SubResult], *, today: dt.date,
            catalogue: Catalogue, store: Any = None,
            admits: Optional[Admits] = None) -> CombinedTable:
    """``admits`` is the CALLER's read scope over graph provenance (graph links traverse only
    what it admits)."""
    base = results.get(spec.base)
    if base is None or base.error:
        raise CombineError(f"the base sub-task {spec.base!r} returned nothing usable"
                           + (f": {base.error}" if base is not None and base.error else ""))
    steps: list[dict] = []
    rows = list(base.rows)
    key_field = base.key_field or ""
    source_id = base.connection or base.adapter_agent
    evidence: dict[str, list[SourcePointer]] = {r.row_id: [] for r in rows}
    candidate: dict[str, bool] = {r.row_id: False for r in rows}
    predicate: list[str] = [f"{base.adapter_agent}/{base.connection} sub-task {spec.base}"]

    # a field the rows were never given would silently empty (semi) or widen (anti) a link, or
    # turn a filter/group into NULLs: refuse instead (R-021 r2 #4)
    needed = [*(link.key for link in spec.links), *spec.group_by,
              *(f.field for f in spec.filters),
              *([spec.aggregate.field] if spec.aggregate and spec.aggregate.field else []),
              *([spec.compare.field] if spec.compare else [])]
    carried = {c for link in spec.links for c in link.carry}
    clash = sorted(carried & {c for r in rows for c in r.cells})
    if clash:
        raise CombineError(f"carried field(s) {clash} are already base fields; a lookup never "
                           f"overwrites a base value")
    _require_fields(rows, [n for n in needed if n not in carried], f"base sub-task {spec.base!r}")
    uncertain: list[str] = []
    #: E2E3 P-01: a SEMI link that matched nothing proves nothing about the cohort, so no
    #: aggregate over what it left is stated as an exact zero (each entry says why)
    unproven: list[str] = []
    lookup_excluded: list[str] = []
    coverage_notes: list[str] = []

    def scope_of(link: LinkSpec, other: SubResult) -> Admits:
        return admits if admits is not None else (lambda sid: sid == other.connection)

    for link in spec.links:
        other = results.get(link.subtask)
        if other is None or other.error:
            raise CombineError(f"linked sub-task {link.subtask!r} returned nothing usable; the "
                               f"link cannot be evaluated, so no answer is computed from it")
        if link.other_field is not None:
            _require_fields(list(other.rows), [link.other_field],
                            f"linked sub-task {link.subtask!r}")
        if link.carry:
            _require_fields(list(other.rows), list(link.carry),
                            f"linked sub-task {link.subtask!r}")
        idx = _link_index(link, other, store=store, catalogue=catalogue, admits=admits)
        lookup = _carry_index(link, other) if link.carry else {}
        ambiguous = 0
        kept = []
        for r in rows:
            v = _value(r, link.key)
            hits = idx.get(str(v), []) if v is not None else []
            if link.mode == "anti" and any(subj for _ev, subj in hits):
                raise CombineError(
                    f"the anti-link on {link.key!r} would drop rows because a PERSON appears to "
                    f"match (a candidate identity); who someone is, or is not, is confirmed by a "
                    f"person, so no row is excluded on a candidate match and no answer is "
                    f"computed from it. Ask for the matching rows as candidates instead.")
            if (link.mode == "semi") == bool(hits):
                if link.carry:
                    extra, _why = _carried(link, lookup.get(str(v), []))
                    if extra is None:
                        ambiguous += 1
                        continue
                    r = r.model_copy(update={"cells": {**r.cells, **extra}})
                kept.append(r)
                for ev, subj in hits:
                    evidence[r.row_id].extend(ev)
                    candidate[r.row_id] = candidate[r.row_id] or subj
        target = link.other_field or f"{link.entity_kind}.canonical_id"
        on = f"{spec.base}.{link.key} = {link.subtask}.{target}"
        unidentified = _unidentified(link, other)
        steps.append({"step": "link", "mode": link.mode, "via": link.via, "hops": link.max_hops,
                      "on": on, "rows_in": len(rows), "rows_out": len(kept),
                      "identifiers_on_other_side": len(idx),
                      **({"carry": list(link.carry), "ambiguous_lookups": ambiguous}
                         if link.carry else {}),
                      **({"entities_without_identifier": len(unidentified)}
                         if unidentified else {})})
        if ambiguous:
            lookup_excluded.append(f"{ambiguous} row(s) matched {link.subtask!r} rows that "
                                   f"disagree on {list(link.carry)} for one {link.key}; the "
                                   f"lookup is ambiguous, so they were excluded")
        # E2E N-02: a link that matched nothing is REPORTED, never shown as a true empty answer
        before = len(uncertain)
        if not idx and other.rows:
            uncertain.append(
                f"link {on} ({link.mode}): {link.subtask!r} returned {len(other.rows)} row(s) "
                f"but none carries a {target} identifier, so the link could not match anything; "
                f"an empty result here does not show that nothing qualifies")
        elif not idx:
            uncertain.append(f"link {on} ({link.mode}): {link.subtask!r} returned no rows, so "
                             f"the link had nothing to match")
        elif link.mode == "semi" and rows and not kept:
            sample_base = sorted({str(_value(r, link.key)) for r in rows})[:5]
            sample_ids = sorted(idx)[:5]
            uncertain.append(
                f"link {on} (semi) matched none of {len(rows)} row(s): {len(idx)} identifier(s) "
                f"on {link.subtask!r} (e.g. {sample_ids}) equal no {link.key} (e.g. "
                f"{sample_base}); a different identifier format would look exactly like this")
        if link.mode == "semi" and not kept and len(uncertain) > before:
            unproven.append(uncertain[-1])
        if unidentified:
            unmatched = {str(v) for v in (_value(r, link.key) for r in rows)
                         if v is not None and not idx.get(str(v))}
            proven, holders = _unholders(link, unidentified, unmatched, store=store,
                                         catalogue=catalogue, admits=scope_of(link, other))
            rest = [u for u in unidentified if u not in proven]
            if proven:
                kind = _kind_word(link.key, str(link.entity_kind))
                why = (f"its name resembles none of the {len(holders)} {link.entity_kind}(s) "
                       f"holding a {spec.base}.{link.key} this link did not match ("
                       f"{', '.join(repr(h) for h in list(holders.values())[:5])})"
                       if holders else f"this link matched every {spec.base}.{link.key}")
                for u in proven:
                    coverage_notes.append(
                        f"{u!r} (found by {link.subtask!r}, no canonical id) is an unlinked "
                        f"{link.entity_kind}, not a {kind} in this result: {why}, so it cannot "
                        f"hold a row this link left out; "
                        f"coverage is not reduced by it")
            if rest:
                shown = ", ".join(repr(x) for x in rest[:5])
                uncertain.append(
                    f"{len(rest)} {link.entity_kind} entity(ies) found by {link.subtask!r} "
                    f"carry no canonical id ({shown}{', ...' if len(rest) > 5 else ''}) and "
                    f"could not be linked to {spec.base}.{link.key}; nothing is linked by name")
        predicate.append(f"{'⋉' if link.mode == 'semi' else '▷'} {link.subtask} on "
                         f"{link.key} = {target} ({link.via}"
                         f"{f', ≤{link.max_hops} hops' if link.max_hops else ''})")
        rows = kept
    if carried:
        _require_fields(rows, [n for n in needed if n in carried], f"base sub-task {spec.base!r}")
    for f in spec.filters:
        before = len(rows)
        rows = [r for r in rows if _matches(r, f)]
        steps.append({"step": "filter", "filter": f.describe(), "rows_in": before,
                      "rows_out": len(rows)})
        predicate.append(f"where {f.describe()}")

    selected = bool(spec.links or spec.filters)

    def eligibility(members: list[ResultRow]) -> Optional[SourcePointer]:
        """ONE derived pointer over exactly the evidence that made ``members`` eligible: their
        link evidence and their filtered cells. None when nothing selected them (the base
        sub-task's own read is the whole story) or no member has citable evidence (an anti-link
        is eligibility by ABSENCE, which the predicate states)."""
        if not selected or not members:
            return None
        ev: list[SourcePointer] = []
        for r in members:
            ev.extend(evidence[r.row_id])
            for f in spec.filters:
                gv = r.cells.get(f.field)
                if gv is not None:
                    ev.append(gv.pointer)
        ev = _dedupe(ev)
        if not ev:
            return None
        return derived_pointer(source_id=source_id,
                               derivation="ELIGIBILITY: " + "; ".join(predicate[1:]),
                               inputs=ev, row_ids=[r.row_id for r in members],
                               predicate="; ".join(predicate))

    if spec.aggregate is None:
        cols = list(dict.fromkeys(c for r in rows for c in r.cells))
        out = []
        for r in rows:
            cells: dict[str, Optional[GroundedValue]] = dict(r.cells)
            elig = eligibility([r])
            if selected:
                # a selected row's values are DERIVED: the cell, plus why the row is here
                for c, gv in r.cells.items():
                    dp = derived_pointer(source_id=source_id, derivation=f"SELECTED {c}",
                                         inputs=[gv.pointer, *([elig] if elig else [])],
                                         row_ids=[r.row_id], predicate="; ".join(predicate))
                    cells[c] = GroundedValue(value=gv.value, pointer=dp) if dp is not None \
                        else None
            out.append(TableRow(cells=cells, candidate=candidate[r.row_id],
                                link_evidence=evidence[r.row_id], row_ids=[r.row_id]))
        if spec.order_by is not None:
            if spec.order_by not in cols and base.method == "facts_sql_pushdown":
                cols.append(spec.order_by)       # every pushed-down value is unknown: NULLs
            if spec.order_by not in cols:
                raise CombineError(f"cannot order by {spec.order_by!r}: the rows have no such "
                                   f"column ({cols})")
            ob = spec.order_by
            out = _order(out, lambda t: (t.cells.get(ob).value if t.cells.get(ob) is not None
                                         else None), descending=spec.descending)
            steps.append({"step": "order", "by": ob, "descending": spec.descending})
        if spec.top_k:
            out = out[:spec.top_k]
        steps.append({"step": "select", "rows_out": len(out)})
        return CombinedTable(columns=cols, rows=out, steps=steps,
                             excluded=lookup_excluded, uncertain=uncertain,
                             notes=coverage_notes)

    agg = spec.aggregate
    periods: list[tuple[str, Optional[tuple[dt.date, dt.date]]]] = [("", None)]
    if spec.compare is not None:
        periods = [(p, resolve_period(p, today)) for p in spec.compare.periods]

    def period_of(r: ResultRow) -> Optional[str]:
        if spec.compare is None:
            return ""
        d = _date(_value(r, spec.compare.field))
        for label, (lo, hi) in periods:                       # type: ignore[misc]
            if d is not None and lo <= d < hi:
                return label
        return None

    groups: dict[tuple, dict[str, list[ResultRow]]] = {}
    dropped = 0
    undated = 0
    excluded: list[str] = list(lookup_excluded)
    for r in rows:
        p = period_of(r)
        if p is None:
            if spec.compare is not None and _date(_value(r, spec.compare.field)) is None:
                undated += 1
            else:
                dropped += 1
            continue
        g = tuple(_value(r, k) for k in spec.group_by)
        groups.setdefault(g, {}).setdefault(p, []).append(r)
    if not spec.group_by and not groups:
        # an ungrouped aggregate over an EMPTY cohort is still one row: count 0, sum 0, and an
        # explicitly undefined avg/min/max (R-021 r2 #6)
        groups[()] = {}
    if spec.compare is not None:
        steps.append({"step": "period_split", "axis": spec.compare.axis,
                      "field": spec.compare.field,
                      "periods": {lbl: [str(b[0]), str(b[1])] for lbl, b in periods if b},
                      "rows_outside_periods": dropped, "rows_with_unknown_date": undated})
        if undated:
            excluded.append(f"{undated} row(s) have no readable {spec.compare.field} date and are "
                            f"in no period (an unknown date is never guessed)")
    steps.append({"step": "group", "by": list(spec.group_by), "groups": len(groups)})

    label = agg.label()
    val_cols = [f"{label}:{p}" if p else label for p, _ in periods]
    delta_col = f"change:{periods[0][0]}-vs-{periods[1][0]}" if spec.compare else None
    columns = list(spec.group_by) + val_cols + ([delta_col] if delta_col else [])
    table: list[TableRow] = []
    absent: list[str] = []
    for g, by_period in groups.items():
        members = [r for rs in by_period.values() for r in rs]
        cells: dict[str, Optional[GroundedValue]] = {}
        gdesc = ", ".join(f"{k} = {v!r}" for k, v in zip(spec.group_by, g)) or "all rows"
        group_elig = eligibility(members)
        for k, v in zip(spec.group_by, g):
            key_ptrs = [r.cells[k].pointer for r in members if k in r.cells]
            ptr = derived_pointer(source_id=source_id, derivation=f"GROUP KEY {k}",
                                  inputs=key_ptrs + ([group_elig] if group_elig else []),
                                  row_ids=[r.row_id for r in members],
                                  predicate="; ".join(predicate + [f"group {gdesc}"]))
            cells[k] = GroundedValue(value=v, pointer=ptr) if ptr is not None else None
        agg_ptrs: list[Optional[GroundedValue]] = []
        for (p, bounds), col in zip(periods, val_cols):
            prs = by_period.get(p, [])
            value, inputs, contrib, notes = _aggregate(agg.op, prs, agg.field, key_field)
            excluded.extend(f"{gdesc} / {col}: {n}" for n in notes)
            window = (f"{p} [{bounds[0]}, {bounds[1]}) on {spec.compare.axis}:"
                      f"{spec.compare.field}" if bounds and spec.compare else None)
            deriv = f"{agg.op.upper()}({agg.field or '*'})"
            pred = "; ".join(predicate + [f"group {gdesc}"])
            ptr = None
            if value is not None and inputs:
                elig = eligibility(contrib)
                ptr = derived_pointer(source_id=source_id, derivation=deriv,
                                      inputs=inputs + ([elig] if elig else []),
                                      row_ids=[r.row_id for r in contrib], agg_window=window,
                                      predicate=pred, snapshot=base.snapshot)
            elif not prs and agg.op in ("count", "sum") and base.snapshot is not None and \
                    not unproven:
                # no row in this group and period: an exact zero, verifiable against the snapshot
                value = 0 if agg.op == "count" else Decimal(0)
                ptr = derived_pointer(source_id=source_id, derivation=deriv, inputs=[],
                                      row_ids=[], agg_window=window, predicate=pred,
                                      snapshot=base.snapshot)
            gv = GroundedValue(value=value, pointer=ptr) if (ptr is not None and
                                                              value is not None) else None
            if gv is None and not prs and unproven:
                absent.append(f"{gdesc} / {col}: not found, so no value is stated (a zero would "
                              f"claim that nothing qualifies): {unproven[0]}")
            elif gv is None:
                why = "; ".join(notes) if notes else (
                    f"no rows: {agg.op} of an empty set is not defined" if not prs and
                    agg.op in ("avg", "min", "max") else "no contributing rows")
                absent.append(f"{gdesc} / {col}: {why}")
            cells[col] = gv
            agg_ptrs.append(gv)
        if delta_col:
            a, b = agg_ptrs[0], agg_ptrs[1]
            if a is not None and b is not None and isinstance(a.value, (int, Decimal)) and \
                    isinstance(b.value, (int, Decimal)):
                ptr = derived_pointer(
                    source_id=source_id,
                    derivation=f"{val_cols[0]} - {val_cols[1]}",
                    inputs=[a.pointer, b.pointer],
                    row_ids=[r.row_id for r in members],
                    predicate="; ".join(predicate + [f"group {gdesc}"]))
                cells[delta_col] = GroundedValue(value=a.value - b.value, pointer=ptr) \
                    if ptr is not None else None
            else:
                cells[delta_col] = None
                absent.append(f"{gdesc} / {delta_col}: a period has no value")
        ev: list[SourcePointer] = []
        for r in members:
            ev.extend(evidence[r.row_id])
        table.append(TableRow(cells=cells, candidate=any(candidate[r.row_id] for r in members),
                              link_evidence=_dedupe(ev), row_ids=[r.row_id for r in members]))
    if spec.order_by is not None and spec.order_by not in columns:
        raise CombineError(f"cannot order by {spec.order_by!r}: the result's columns are "
                           f"{columns}")
    order_col = spec.order_by or val_cols[0]
    table.sort(key=lambda t: tuple(str(t.cells[k].value) if t.cells.get(k) else ""
                                   for k in spec.group_by))
    table = _order(table, lambda t: (t.cells[order_col].value if t.cells.get(order_col)
                                     is not None else None), descending=spec.descending)
    if spec.top_k:
        table = table[:spec.top_k]
    steps.append({"step": "aggregate", "op": agg.op, "field": agg.field, "rows_out": len(table),
                  "arithmetic": "decimal (Python Decimal), no model"})
    return CombinedTable(columns=columns, rows=table, steps=steps, absent=absent,
                         excluded=excluded, uncertain=uncertain, notes=coverage_notes)


def _require_fields(rows: list[ResultRow], fields: list[str], where: str) -> None:
    """Each field must be CARRIED by the rows (a cell, possibly NULL) when there are rows."""
    if not rows:
        return
    have = {c for r in rows for c in r.cells}
    missing = [f for f in dict.fromkeys(fields) if f not in have]
    if missing:
        raise CombineError(f"{where} returned no field {', '.join(repr(m) for m in missing)}; it "
                           f"was not selected, so the combine cannot use it (nothing is treated "
                           f"as NULL or as a non-match in its place)")


def _dedupe(ptrs: list[SourcePointer]) -> list[SourcePointer]:
    seen, out = set(), []
    for p in ptrs:
        k = p.model_dump_json()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def table_from_result(result: SubResult) -> CombinedTable:
    """A single sub-result (e.g. a pushed-down aggregate) as the answer table, unchanged."""
    cols = list(dict.fromkeys(c for r in result.rows for c in r.cells))
    return CombinedTable(columns=cols, rows=[TableRow(cells=dict(r.cells), row_ids=[r.row_id])
                                             for r in result.rows],
                         steps=[{"step": "single_source", "method": result.method,
                                 "rows_out": len(result.rows)}])


__all__ = ["CombineError", "CombinedTable", "TableRow", "combine", "table_from_result"]
