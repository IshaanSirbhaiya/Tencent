"""The read path: plan needs -> `CapabilityRequest` -> `GroundedRows`.

This is the only place in the package that touches an adapter, and it touches it through the port
and nothing else — five verbs, no driver handle, no SQL, no credential. Everything above it works
on cells that already carry pointers, which is why `require_pointer_for_every_cell` is a property
of the architecture here rather than a check somebody has to remember to run.

**One read per entity, not one per column.** A plan naming four features over `covenant_test` and a
label over the same table produces one request whose projection is the union of what those five
need. That matters for more than cost: `read_hash` becomes `SnapshotIdentity.query_hash`, so
splitting the same logical read into five requests would mint five snapshots and five sets of
pointers for one fetch, and a later verification would have five different things to re-execute.

**The row identity comes from `describe()`, not from the plan.** A plan that named its own key
could name the wrong one, and a pointer cites the key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from databridge_adapter import (
    AdapterResult,
    CapabilityRequest,
    CompiledDecisionTable,
    GroundedRows,
    PolicyEngine,
    Principal,
    Projection,
    SourceDescription,
)
from databridge_core import GroundedValue

from .dataset import Cell


@dataclass(frozen=True)
class EntityRead:
    """One entity's rows, indexed for joining. Cells stay cells throughout."""

    entity: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    rows: tuple[Mapping[str, Cell], ...]
    result: AdapterResult
    decisions: Optional[CompiledDecisionTable] = None

    @property
    def snapshot(self):
        return self.result.rows.snapshot

    def value(self, row: Mapping[str, Cell], column: str) -> Any:
        """The value behind a cell, or `None`. Never used to decide whether a cell EXISTS —
        a withheld cell and an absent cell both answer `None` here and mean different things."""
        cell = row.get(column)
        return cell.value if isinstance(cell, GroundedValue) else None

    def row_id(self, row: Mapping[str, Cell]) -> Optional[str]:
        """The stable row identity, or `None` when a key column was not disclosed.

        `None` is a real answer: a principal who cannot see a key column cannot be given rows
        identified by it, and a synthesised surrogate would be an identifier this system invented
        for a subject, which is exactly the move the identity constraint forbids elsewhere.
        """
        parts = []
        for k in self.key:
            v = self.value(row, k)
            if v is None:
                return None
            parts.append(str(v))
        return "|".join(parts)


class EstateReader:
    """Reads one source through the five-verb port, on behalf of one principal.

    ``policy`` is optional and is used for exactly one thing: obtaining the *compiled decision
    table* for a projection before the read runs, so the O1b screen (a transformed disclosure the
    receipt cannot describe) refuses at compile time rather than after the rows have been fetched.
    Without it the screen still runs, at emission, against the pointers the read produced — later,
    and therefore worse, but never absent.
    """

    def __init__(self, adapter, principal: Principal, *, policy: Optional[PolicyEngine] = None,
                 capability: str = "row_read", limit: int = 100_000) -> None:
        self._adapter = adapter
        self._principal = principal
        self._policy = policy
        self._capability = capability
        self._limit = limit
        self._description: Optional[SourceDescription] = None
        self._cache: dict[tuple, EntityRead] = {}

    @property
    def source_id(self) -> str:
        return self._adapter.source_id

    @property
    def principal(self) -> Principal:
        return self._principal

    def describe(self) -> SourceDescription:
        if self._description is None:
            self._description = self._adapter.describe()
        return self._description

    def key_for(self, entity: str) -> tuple[str, ...]:
        for e in self.describe().entities:
            if e.name == entity:
                return e.key
        raise KeyError(
            f"source {self.source_id!r} does not describe entity {entity!r}; described: "
            f"{[e.name for e in self.describe().entities]}")

    def request(self, entity: str, fields: Iterable[str], *,
                order_by: Sequence[str] = ()) -> CapabilityRequest:
        key = self.key_for(entity)
        projected = tuple(dict.fromkeys([*fields, *key]))        # ordered, de-duplicated
        return CapabilityRequest(
            source_id=self.source_id,
            capability=self._capability,
            principal=self._principal,
            projection=Projection(entity=entity, fields=projected, key=key),
            order_by=tuple(order_by) or key,
            limit=self._limit,
        )

    def decision_table(self, req: CapabilityRequest) -> Optional[CompiledDecisionTable]:
        """The compiled table policy would apply to this read, without reading anything.

        `admit` is deterministic and performs no I/O, so asking for the table here and letting the
        Core ask again inside `resolve` costs a second evaluation and buys the O1b screen the
        chance to refuse *before* rows are fetched.
        """
        if self._policy is None:
            return None
        return self._policy.admit(req, description=self.describe()).decisions

    def read(self, entity: str, fields: Iterable[str], *,
             order_by: Sequence[str] = ()) -> EntityRead:
        req = self.request(entity, fields, order_by=order_by)
        cache_key = (entity, req.read_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]
        decisions = self.decision_table(req)
        result = self._adapter.resolve(req)
        rows = _as_dicts(result.rows)
        read = EntityRead(entity=entity, columns=result.rows.columns,
                          key=req.projection.key, rows=rows, result=result,
                          decisions=decisions)
        self._cache[cache_key] = read
        return read

    def verify(self, pointer) -> Any:
        return self._adapter.verify(pointer)


def _as_dicts(rows: GroundedRows) -> tuple[Mapping[str, Cell], ...]:
    return tuple({c: cell for c, cell in zip(rows.columns, row)} for row in rows.rows)


__all__ = ["EntityRead", "EstateReader"]
