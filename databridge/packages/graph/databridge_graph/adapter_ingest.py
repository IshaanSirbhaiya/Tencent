"""Read a pack's estate rows **through the adapter fleet**, so a graph pointer and an adapter
pointer are the same population.

## The gap this closes

`estate_seed.py` reads a pack's fixtures with its own four hand-written readers
(`sql_insert`/`csv`/`json_pages`/`fixed_width`), straight off disk. The adapter fleet — four
governed Cores in `packages/adapter`, each stamping per-field trust from a source contract and
enforcing `PolicyEngine.admit()` before a byte moves — sits beside the graph, unused for the one
job it exists to do: serve a read. That means a pointer the graph mints (`estate_seed._cell`,
kind `table_cell`/`doc_span`, trust from the DATASET's single `trust:` line) and a pointer a Core
mints (kind `query_cell`/`doc_span`/`derived`, trust from the CONTRACT's per-field declaration)
are two different populations that happen to look similar. `/evidence` impact fan-out over a
fleet-served value finds nothing for a graph-minted pointer, and a graph pointer's own `verify()`
answers `UNVERIFIABLE` — not because nothing changed, but because nothing on the graph side ever
asked a Core to check.

This module is the bridge. It does not replace a single existing reader — `sql_insert`, `csv`,
`json_pages` and `fixed_width` are untouched, and the pinned government build
(`test_the_government_graph_is_built_from_the_ontology.py`, 46,346 nodes / 52,747 edges) reads
exactly as it did before this file existed, because nothing here is on its call path. What this
module adds is a SECOND way to produce the `(row_key, {column: value})` shape `estate_seed`
already consumes — one where each value additionally carries the real `SourcePointer` a Core
minted for it — and `build_pack_graph`/`load_pack_graph` accept it as an explicit, opt-in override
per dataset (`adapter_rows=`). Additive and switchable, per the brief.

## What "the same population" means concretely

`AdapterRow.pointer_for(column)` returns the `SourcePointer` a Core attached to that cell's
`GroundedValue`, carried through unchanged — not a graph-side reconstruction of one.
`estate_seed._pointer()` prefers that pointer over the dataset-level `_cell()` stamp whenever a row
supplies one. So a node or edge built from an `AdapterRow` cites a `query_cell` (relational) or
`doc_span`/`derived` (document) pointer, addressed at the SAME source_id/entity/row/column a live
`core.verify(pointer)` call or an `/evidence` impact query already knows how to resolve — every
field on it (`table`, `column`, `row_id`, `snapshot`, `trust`) is exactly what the Core minted, not
a lookalike the graph constructed on its own.

## What this module deliberately does not do

* **No pack knowledge.** It knows nothing about which pack, which source, which purpose or which
  role reads which estate — that is `government_seed.py`'s job (and `finance_seed.py`'s), the same
  split `estate_seed.py` already draws with those two thin, four-line entry points. This module
  only knows how to turn `core.resolve(req)` into `AdapterRow`s.
* **No policy decision.** `core.resolve()` already goes through `PolicyEngine.admit()` before a row
  is read (every Core's own `_admit`/`self._policy.admit(...)`) — this module calls the Core and
  nothing else. A refusal (`PurposeNotBound`, an empty `allow`, `POLICY_PURPOSE_MISMATCH`) surfaces
  exactly as the Core raises or returns it; nothing here catches or reinterprets it.
* **No pagination beyond one bounded read.** `CapabilityRequest.limit` caps a single `resolve()`
  call (<=100,000 rows) and there is no keyset-cursor concept on the port to page past it. Fine for
  a bounded or a single-source build; a full incremental crawl of a source larger than one page is
  future work, named rather than silently truncated (`rows_from_core` does not cap `limit` itself —
  the caller's `req_factory` does, and a result at exactly `limit` rows is a caller's own signal
  that it did not see everything).
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, Mapping, Optional

from databridge_core import GroundedValue, SourcePointer


class AdapterRow(dict):
    """One row read through a Core. Looks exactly like the `{column: value}` mapping every other
    `estate_seed` reader yields — `_get`, `_render`, `_matches` and dict access all work unchanged
    — and additionally remembers the REAL per-field `SourcePointer` a Core minted for each cell
    that came back as a value, so the graph can cite THAT pointer instead of re-deriving one from
    the dataset's single `trust:` line (`estate_seed._pointer`).

    A column with no pointer (withheld by policy, absent from the source, or simply not requested)
    is present in the mapping with value `None`, exactly like a NULL cell from any other reader —
    `estate_seed`'s existing "None is an absence, never a default" handling applies unchanged.
    """

    def __init__(self, values: Mapping[str, Any], pointers: Mapping[str, SourcePointer]) -> None:
        super().__init__(values)
        self._pointers = dict(pointers)

    def pointer_for(self, column: str) -> Optional[SourcePointer]:
        return self._pointers.get(column)


def rows_from_core(core: Any, req_factory: Callable[[], Any]) -> Iterator[AdapterRow]:
    """Execute one `resolve()` against a Core and yield `AdapterRow`s, column-value pairs matched
    back up with the pointer (or withholding) each cell actually carried.

    `req_factory()` builds the `CapabilityRequest` — the caller owns the principal, the entity, the
    projected fields, the ordering and the row limit, exactly as `AdapterShell.propose`/`.validate`
    would for a model-planned read, or as a pack's own ingest wiring does for a fixed one
    (`government_seed.py`). This function does only the mechanical part every Core-backed dataset
    shares: call `core.resolve(req)`, and for each returned row pair `req.projection.fields` back
    up with the `Cell` at that position.

    A `Withheld` or `Absent` cell becomes `None` in the yielded row, carrying no pointer — the same
    "no citation, no claim" rule `estate_seed._build_node`/`_build_edge` already enforce for every
    other reader's NULL cell. Nothing here inspects WHY a cell came back that way (a policy denial
    and a genuinely missing field are different fixture-side facts, and this bridge is not the
    place either gets resolved) — a caller that needs to know can inspect `core.resolve(req)`
    directly rather than through this convenience wrapper.
    """
    req = req_factory()
    result = core.resolve(req)
    columns = result.rows.columns
    for row in result.rows.rows:
        values: dict[str, Any] = {}
        pointers: dict[str, SourcePointer] = {}
        for column, cell in zip(columns, row):
            if isinstance(cell, GroundedValue):
                values[column] = cell.value
                pointers[column] = cell.pointer
            else:
                values[column] = None
        yield AdapterRow(values, pointers)


__all__ = ["AdapterRow", "rows_from_core"]
