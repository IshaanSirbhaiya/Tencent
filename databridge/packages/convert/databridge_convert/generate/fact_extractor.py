"""Fact slots (C17, 09_FINAL_PRD §12.1).

Source rows / aggregates → ``[F1..Fn]`` ``Fact``s, each a ``GroundedValue`` whose ``value`` is
the **canonical rendered string** (via ``formatters``). These are the ONLY spellings of each value
the generated text may use; ``claim_check`` matches output tokens against ``fact.rendered``.

A scalar fact points at its source cell/span; an aggregate (sum/count/avg over rows) points at the
range it was computed from with a ``derived`` pointer carrying the predicate (so the number is
traceable to the rows it summarises, not invented).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Optional

from databridge_core import GroundedValue, SourcePointer

from . import formatters

# fact kind → formatter dispatch
RenderKind = str  # "money" | "date" | "name" | "percent" | "int" | "decimal" | "text"


def render_fact(value: Any, kind: RenderKind, *, currency: str = "USD",
                places: Optional[int] = None, as_fraction: bool = False) -> str:
    """Render a raw value to its canonical fact string (the form claim_check matches)."""
    if kind == "money":
        return formatters.render_money(value, currency)
    if kind == "date":
        return formatters.render_date(value)
    if kind == "percent":
        return formatters.render_percent(value, 1 if places is None else places,
                                         as_fraction=as_fraction)
    if kind == "int":
        return formatters.render_int(value)
    if kind == "decimal":
        return formatters.render_decimal(value, 2 if places is None else places)
    # name / text
    return formatters.render_name(value)


@dataclass(frozen=True)
class Fact:
    """One grounded fact slot. ``rendered`` is canonical; ``value`` is the raw source value."""
    fid: str                              # "F1", "F2", …
    label: str                            # the field / aggregate name
    kind: RenderKind
    rendered: str                         # canonical form — claim_check matches THIS
    value: Any                            # raw value
    pointer: SourcePointer
    confidence: float = 1.0

    @property
    def grounded(self) -> GroundedValue:
        # the EMITTED value is the rendered string (provenance is over the canonical fact).
        return GroundedValue(value=self.rendered, pointer=self.pointer, confidence=self.confidence)


def extract_facts(record: dict, *, kinds: dict[str, RenderKind],
                  pointers: dict[str, SourcePointer], source_id: str = "facts",
                  currency: str = "USD", start: int = 1) -> list[Fact]:
    """One source record → a list of Facts (deterministic order = ``kinds`` insertion order).

    ``kinds`` maps field → render kind; ``pointers`` maps field → its SourcePointer. A field
    present in ``kinds`` but absent/None in the record is SKIPPED (no fact = nothing to cite —
    the absent-fact fallback path in the synthesiser handles "data not available")."""
    facts: list[Fact] = []
    i = start
    for field, kind in kinds.items():
        if field not in record or record[field] is None:
            continue
        ptr = pointers.get(field)
        if ptr is None:
            raise ValueError(f"fact {field!r} has no SourcePointer — every fact must be grounded (P4)")
        rendered = render_fact(record[field], kind, currency=currency)
        facts.append(Fact(fid=f"F{i}", label=field, kind=kind, rendered=rendered,
                          value=record[field], pointer=ptr))
        i += 1
    return facts


def aggregate_fact(fid: str, label: str, value: Any, kind: RenderKind, *,
                   derived_from: list[SourcePointer], predicate: str,
                   currency: str = "USD", places: Optional[int] = None) -> Fact:
    """An aggregate (sum/count/avg over rows) → a Fact whose ``derived`` pointer carries the
    per-row source pointers (``derived_from``) AND the human-readable rule (``derivation``), so the
    summary number traces to the exact rows it summarises — never invented (§12.1 / §17.1)."""
    if not derived_from:
        raise ValueError("aggregate_fact requires derived_from (the grounded rows) — P4")
    ptr = SourcePointer(source_id=derived_from[0].source_id, kind="derived",
                        derived_from=list(derived_from), derivation=predicate)
    rendered = render_fact(value, kind, currency=currency, places=places)
    return Fact(fid=fid, label=label, kind=kind, rendered=rendered, value=value, pointer=ptr)


def sum_fact(fid: str, label: str, rows: list[dict], field: str, pointers: list[SourcePointer],
             kind: RenderKind = "money", *, currency: str = "USD") -> Fact:
    """Convenience: SUM(field) over rows → an aggregate Fact, grounded to each row's ``pointers``
    (parallel to ``rows``). The common report case ("total disputed amount = sum of these rows")."""
    if len(pointers) != len(rows):
        raise ValueError("sum_fact: pointers must be parallel to rows (one per row, P4)")
    total = sum((Decimal(str(r[field])) for r in rows if r.get(field) is not None), Decimal(0))
    return aggregate_fact(fid, label, total, kind, derived_from=pointers,
                          predicate=f"SUM({field})", currency=currency)


def facts_by_id(facts: list[Fact]) -> dict[str, Fact]:
    return {f.fid: f for f in facts}
