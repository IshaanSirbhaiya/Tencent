"""Aggregate / derived pointers — built ONLY from the closed `SourcePointer` vocabulary.

Every combined value is a ``derived`` pointer (no new pointer kind is invented):

* ``derived_from``  — EXACTLY the input cells that contributed (for a sum: the amount cells of the
  rows in that group and period; for a comparison: the two aggregate pointers it subtracts);
* ``derivation``    — the rule, e.g. ``SUM(amount)``;
* ``predicate``     — the re-runnable selection in words (filters, links, group key);
* ``agg_window``    — the period and axis the value covers;
* ``row_count`` / ``rows_digest`` (sha256 over the sorted contributing row ids) /
  ``sample_row_ids`` (≤ 20, sorted) — so /evidence can check the row set without re-reading it.

Above `MAX_DERIVED_FROM` inputs the pointer uses §17.1 compression: no per-row pointers, but the
facts snapshot + predicate + row_count + rows_digest, which the validator requires to stay
verifiable. Trust is JOINED from the parents (never promoted): TRUSTED only when every parent
reads back TRUSTED.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Sequence

from databridge_core import SourcePointer
from databridge_core.provenance import SnapshotIdentity, Trust, effective_trust

#: Per-row pointers kept on one derived pointer before compressing (§17.1).
MAX_DERIVED_FROM = 2000


def rows_digest(row_ids: Iterable[str]) -> str:
    return "sha256:" + hashlib.sha256("\n".join(sorted(str(r) for r in row_ids))
                                      .encode("utf-8")).hexdigest()


def joined_trust(parents: Sequence[SourcePointer]) -> Trust:
    """TRUSTED only when every parent reads back TRUSTED; an empty set is UNTRUSTED (fail
    closed — a value with no parents has nothing to inherit trust from)."""
    if parents and all(effective_trust(p) is Trust.TRUSTED for p in parents):
        return Trust.TRUSTED
    return Trust.UNTRUSTED


def derived_pointer(*, source_id: str, derivation: str, inputs: Sequence[SourcePointer],
                    row_ids: Sequence[str], predicate: Optional[str] = None,
                    agg_window: Optional[str] = None,
                    snapshot: Optional[SnapshotIdentity] = None) -> Optional[SourcePointer]:
    """A ``derived`` pointer over ``inputs``; ``None`` when it cannot be made verifiable (no
    inputs and no snapshot to compress against) — the caller then emits no value."""
    ids = sorted({str(r) for r in row_ids})
    if inputs and len(inputs) <= MAX_DERIVED_FROM:
        return SourcePointer(source_id=source_id, kind="derived", derivation=derivation,
                             predicate=predicate, agg_window=agg_window, row_count=len(ids),
                             rows_digest=rows_digest(ids), sample_row_ids=ids[:20] or None,
                             derived_from=list(inputs), trust=joined_trust(inputs))
    if snapshot is None or predicate is None:
        return None
    return SourcePointer(source_id=source_id, kind="derived", derivation=derivation,
                         predicate=predicate, agg_window=agg_window, row_count=len(ids),
                         rows_digest=rows_digest(ids), sample_row_ids=ids[:20] or None,
                         snapshot=snapshot, trust=Trust.UNTRUSTED)


def expand(pointer: SourcePointer) -> list[SourcePointer]:
    """The leaf input pointers a derived pointer rests on (transitively)."""
    if pointer.kind != "derived" or not pointer.derived_from:
        return [pointer]
    out: list[SourcePointer] = []
    for p in pointer.derived_from:
        out.extend(expand(p))
    return out


__all__ = ["MAX_DERIVED_FROM", "rows_digest", "joined_trust", "derived_pointer", "expand"]
