"""Two derivation rules this package needs, registered into the adapter's shared registry.

`databridge_adapter.grounding` holds one versioned registry of replayable rules, and
`assert_grounded` replays a `derived` pointer's rule over its cited parents before believing the
value. Registering here rather than inventing a private mechanism is the whole point: a chunk of
text and a count over prior rows are both derived values, and both should be checkable by the same
wall that checks a redaction.

* **`text_slice@v1`** — the chunker's rule. `parents[0][start:end]`. This is what makes the chunk
  re-slice check structural rather than a prompt instruction: a chunk whose stored text does not
  reproduce byte-identically from the cited offsets fails `assert_grounded`, and citation
  hallucination rates of 11–57% across commercially deployed models are why that has to be
  enforced rather than asked for (SYSTEM_DESIGN §6.6(c)).

* **`count_rows@v1`** — the aggregate's rule. Returns the number of parent values it was given.
  Trivial, and registered anyway, because the alternative is a `derived` pointer carrying prose
  that nobody can run — which is exactly what a `derived` pointer exists not to be. On the
  compressed path the parents are not materialised and the check is the predicate bundle instead
  (`verify_compressed_aggregate`), but the rule stays replayable so that `pointer_mode: full`
  verifies the same value through the same wall.

Re-registering the same `(name, version)` with the same function is a no-op, so importing this
module twice is safe; re-registering it with a *different* function is refused by the registry,
which is what stops a rule being swapped under stored pointers.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from databridge_adapter import DerivationError, canonical_json, register_derivation, sha256_of


def _text_slice_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    if len(parents) != 1:
        raise DerivationError(
            f"text_slice@v1 slices exactly one parent value, got {len(parents)}")
    text = parents[0]
    if not isinstance(text, str):
        raise DerivationError(
            f"text_slice@v1 slices text; the cited parent is a {type(text).__name__}. A chunk of "
            f"a non-text cell has no character offsets, so the offsets in the pointer would name "
            f"nothing")
    for name in ("start", "end"):
        if name not in args:
            raise DerivationError(
                f"text_slice@v1 requires `{name}`: without both offsets the slice is not "
                f"re-executable and the chunk is a claim rather than a citation")
    try:
        start, end = int(args["start"]), int(args["end"])
    except ValueError as err:
        raise DerivationError(f"text_slice@v1 offsets must be integers: {err}") from err
    if start < 0 or end < start:
        raise DerivationError(
            f"text_slice@v1 offsets [{start}, {end}) do not describe a forward range")
    if end > len(text):
        raise DerivationError(
            f"text_slice@v1 cites [{start}, {end}) of a {len(text)}-character parent: the "
            f"citation names a range the source does not have")
    return text[start:end]


register_derivation(
    "text_slice", 1, _text_slice_v1,
    doc="Return parent[start:end]. Late chunking: boundaries are computed on the ORIGINAL text, "
        "so the offsets stay exact and every chunk re-slices byte-identically.")


def _count_rows_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    if "entity" not in args:
        raise DerivationError(
            "count_rows@v1 requires `entity`: a count that does not say what it counted is not a "
            "measurement of anything")
    return len(list(parents))


register_derivation(
    "count_rows", 1, _count_rows_v1,
    doc="Count the cited parent rows. On the compressed path the parents are replaced by a "
        "predicate + row_count + rows_digest, re-executable against the pinned snapshot.")


def rows_digest(row_ids: Iterable[str]) -> str:
    """A digest over the contributing rows, ORDER-INDEPENDENT.

    Order-independent because "which rows went into this count" is a set fact: the same rows read
    in a different page order are the same rows, and a digest that said otherwise would report
    drift every time the source changed its physical layout.
    """
    return sha256_of(canonical_json(sorted(str(r) for r in row_ids)))


def content_digest(chunks: Iterable[str]) -> str:
    """A digest over an ORDERED sequence of rendered rows. Order matters here, deliberately: it is
    computed under the plan's declared `row_order`, and the order is part of the artefact."""
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk.encode("utf-8"))
        h.update(b"\x1e")                    # record separator: "ab"+"c" must not equal "a"+"bc"
    return h.hexdigest()


__all__ = ["rows_digest", "content_digest"]
