"""Canonicalisation — the bytes `read_hash` is computed over (`ADAPTER_AGENT_SPEC` §2, §2a).

`read_hash` becomes `SnapshotIdentity.query_hash`, which is how a pointer is verified by
*re-execution* rather than by trust. That makes this module load-bearing in an unusual way: it is
not merely a helper, it is the definition of pointer identity. If it changes, every stored pointer
in the ledger silently stops verifying.

Three rules follow from that, and each one is a deviation from the obvious implementation.

**1. The payload is built by hand, not by `model_dump()`.** `ADAPTER_AGENT_SPEC` §2's example uses
``self.model_dump(mode="json", exclude={...})``, and §2a immediately worries that "a Pydantic
upgrade, a dict-ordering change or a float-formatting difference would otherwise silently
invalidate every stored pointer". Building the payload by hand is the stronger form of that same
intent: a Pydantic release that changes how it renders a `datetime`, or that adds a computed field
to the dump, cannot reach these bytes at all. The golden-hash test pins the result; this keeps the
number of things that could move it small enough to enumerate.

**2. Scalars are TYPE-TAGGED.** ``{"limit": 1}`` and ``{"limit": "1"}`` are different reads and
must not share an identity. JSON erases that distinction for anything it renders the same way, so
every parameter value is wrapped as ``{"t": <tag>, "v": <text>}``. `bool` is checked before `int`
because `bool` *is* an `int` in Python and would otherwise tag as one.

**3. Floats are rendered with `repr`.** CPython's shortest-round-trip repr is stable across 3.x,
and `json.dumps` uses it — but going through `repr` explicitly makes it a decision rather than an
inherited default. ``allow_nan=False``: NaN has no canonical form (and ``NaN != NaN`` would make
the request unequal to itself), so it is refused rather than hashed.

`scheme_version` covers all of this. Bumping it is how a deliberate change to canonicalisation is
made *visible*: old pointers keep verifying under the old scheme, and a request that names a scheme
this code does not implement is refused rather than re-derived under a scheme it never meant.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Union

from databridge_core import SourcePointer
from databridge_core.provenance import _REQUIRED_FIELDS

from .errors import PredicateGrammarNotDefined

#: The canonicalisation scheme this module implements. A `CapabilityRequest` naming any other
#: value is refused - fail closed, never re-derive under a scheme the request did not mean.
#:
#: **v1 -> v2 (workstream 3B, after review R-3A).** Four defects moved the bytes, so the version
#: moved with them rather than the goldens being edited in place:
#:
#: 1. `Decimal` was rendered through `normalize()`, which rounds under the AMBIENT decimal
#:    context - a per-thread, per-process setting. The same `Decimal` could hash differently in
#:    two processes, which is the one thing this module exists to prevent. v2 renders the value
#:    exactly and refuses non-finite Decimals.
#: 2. That same `normalize()` collapsed `Decimal("1.0")` and `Decimal("1")` into one read. Scale
#:    is observable to a source and to a named query, so v2 keeps them distinct.
#: 3. `pointer_digest` went through `model_dump(mode="json")`, reintroducing exactly the Pydantic
#:    serialisation dependence the hand-built payload was written to avoid. v2 walks the pointer's
#:    declared fields in a fixed order through `canonical_scalar`.
#: 4. The pointer rendering is now DEEP and taken at request construction, so a later mutation of
#:    a pointer's `facts`/`derived_from` list cannot change an identity that has already been
#:    issued.
#:
#: **v2 -> v3 (same workstream, one day later).** `packages/core` added `trust` and
#: `capture_digest` to `SourcePointer` while closing the trust-laundering paths. Both are correct
#: changes, and both moved every digest in this module — because v2 rendered every non-`None`
#: field. The lesson is not "re-pin the goldens": it is that **adding any field to `SourcePointer`
#: invalidated every stored pointer identity in the system**, silently, with a golden test as the
#: only signal. v3 renders an ALLOWLIST of locator fields instead, so a field added tomorrow
#: cannot move an identity issued yesterday. See `canonical_pointer`.
#: v4 (2026-08-16): `email_part` gained `char_start`/`char_end` as locator fields,
#: closing a collision where two spans of one part shared an identity.
SCHEME_VERSION = 4

#: What may be bound as a predicate/named-query parameter. Deliberately narrow: no containers,
#: because a nested structure has no single obvious canonical form and would be the first place a
#: hash quietly stopped being deterministic.
Scalar = Union[None, bool, int, float, str, datetime, date, Decimal]


def canonical_json(obj: Any) -> str:
    """The one JSON rendering used for every digest in this package."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    """A timezone-aware instant, normalised to UTC, as text.

    A naive datetime is refused rather than assumed to be UTC: "2026-08-16T09:00" means a
    different instant in Singapore than in London, and a pointer identity that depends on the
    reader's timezone is not an identity.
    """
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "as_of must be timezone-aware: a naive instant means a different moment in every "
            "deployment, and pointer identity cannot depend on the reader's clock")
    return value.astimezone(timezone.utc).isoformat()


def canonical_scalar(value: Scalar) -> dict[str, str]:
    """One parameter value, type-tagged. See rule 2 in the module docstring."""
    if value is None:
        return {"t": "null", "v": ""}
    if isinstance(value, bool):                 # BEFORE int - bool is a subclass of int
        return {"t": "bool", "v": "true" if value else "false"}
    if isinstance(value, int):
        return {"t": "int", "v": str(value)}
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(
                f"parameter value {value!r} has no canonical form: NaN is not equal to itself, "
                "so a request carrying one would not be equal to itself either")
        return {"t": "float", "v": repr(value)}
    if isinstance(value, Decimal):
        # NOT normalised, and not rendered through any context-sensitive operation.
        #
        # v1 called `value.normalize()`, which does two harmful things at once. It rounds
        # according to the AMBIENT `decimal` context - a per-thread setting a library can change -
        # so the same `Decimal` could hash differently in two processes, defeating the entire
        # point of this module. And it collapsed `Decimal("1.0")` into `Decimal("1")`, which are
        # two different reads: scale is observable to a source (a NUMERIC(18,2) column) and to a
        # named query that renders its parameter as text.
        #
        # `as_tuple()` is exact, allocation-free of context, and includes the exponent, so the
        # scale survives. The sign is carried separately because `Decimal("-0")` and `Decimal("0")`
        # have the same digits.
        if not value.is_finite():
            raise ValueError(
                f"parameter value Decimal({value!r}) is not finite: NaN and Infinity have no "
                "canonical form (a signalling NaN raises under some contexts and renders as text "
                "under others), so a request carrying one is refused rather than hashed")
        sign, digits, exponent = value.as_tuple()
        return {"t": "decimal", "v": f"{'-' if sign else '+'}{''.join(map(str, digits))}E{exponent}"}
    if isinstance(value, datetime):
        return {"t": "datetime", "v": iso_utc(value) or ""}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, str):
        return {"t": "str", "v": value}
    raise TypeError(
        f"parameter values must be scalars (None/bool/int/float/Decimal/date/datetime/str), got "
        f"{type(value).__name__}: a container has no single canonical form, which is exactly "
        f"where a 'deterministic' hash stops being deterministic")


def canonical_params(params: Iterable[tuple[str, Scalar]]) -> dict[str, dict[str, str]]:
    """Bound parameters, canonicalised. Order-independent by construction (a mapping)."""
    out: dict[str, dict[str, str]] = {}
    for name, value in params:
        if name in out:
            raise ValueError(f"duplicate parameter binding {name!r}")
        out[name] = canonical_scalar(value)
    return out


def canonical_ast(predicate: Optional[str],
                  params: Iterable[tuple[str, Scalar]]) -> dict[str, Any]:
    """The predicate in canonical AST form, plus its bound parameters.

    **The AST half is not implemented, and refusing is the honest state.**
    `ADAPTER_AGENT_SPEC` §6 and Appendix A name the predicate grammar as the highest-risk
    undefined component in the whole design: it needs a closed grammar, an identifier allowlist
    bound to the projection, a literal-free expression form, a type check against pack-declared
    types, a deterministic AST re-serialisation, and per-dialect conformance tests. Until that
    exists, §6's own instruction is that an adapter accepts ``predicate=None`` and a fixed set of
    pack-declared named queries - so a non-None predicate is refused here, at the contract, rather
    than passed to a compiler that would have to guess.

    Parameters are canonicalised now because named queries need them and they are well defined.
    """
    if predicate is not None:
        raise PredicateGrammarNotDefined(
            "predicate= is not accepted: the portable predicate grammar (closed grammar, "
            "identifier allowlist bound to the projection, literal-free + bound params, type "
            "check, canonical AST, per-dialect conformance tests) is not built - see "
            "ADAPTER_AGENT_SPEC §6 and Appendix A. Use a pack-declared named query as the "
            "`capability` and bind its arguments in `params`.")
    return {"predicate": None, "params": canonical_params(params)}


#: Fields of `SourcePointer` that hold a nested pointer (or a list of them). Walked recursively so
#: the canonical form is DEEP - see `canonical_pointer` on why shallow is not enough.
_POINTER_CHILD_FIELDS: tuple[str, ...] = ("child",)
_POINTER_LIST_FIELDS: tuple[str, ...] = ("facts", "derived_from")

#: Locator fields a kind needs BEYOND `SourcePointer._REQUIRED_FIELDS`. See `canonical_pointer`.
_LOCATOR_EXTRAS: Mapping[str, tuple[str, ...]] = {
    # `row_id` is the STABLE identity a `query_cell` cites (§7: "the pointer cites the PRIMARY
    # KEY, with the ordinal as a hint only"), and `table` is what lets `verify()` find the row
    # again. Neither is in the required set, and both are pure locator.
    "query_cell": ("row_id", "table"),
    # A derived value's location IS its parents plus the rule. `_REQUIRED_FIELDS["derived"]` is
    # only `derivation`, which would make every redaction of every column one identity.
    "derived": ("derived_from", "predicate", "agg_window"),
    "generated_claim": ("facts",),
    # `char_start`/`char_end` are LOCATOR fields here, and their absence was a live collision:
    # two different spans of one email part produced an identical canonical form, so two distinct
    # citations shared one pointer identity — and, because `read_hash` is built from the locator
    # set, one cache entry. Reproduced before being fixed.
    #
    # They are optional on `email_part` (unlike `doc_span`, where `_REQUIRED_FIELDS` already
    # carries them), so nothing forced them into the locator set. Optional does not mean
    # non-locating: when a pointer carries them they are precisely what distinguishes one span of
    # a body from another.
    #
    # The mailbox adapter worked around this by encoding the span inside `part` (`body#s:e`).
    # That remains valid — a pointer with no offsets is unaffected — but the workaround is no
    # longer load-bearing.
    "email_part": ("child", "char_start", "char_end"),
    "workbook_cell": ("cell", "cell_range"),
}

#: Required-by-kind fields that are NOT part of the location, with the reason each is excluded.
#:
#: * ``row_ordinal`` — §7 calls it "a hint, not identity". Two reads that return the same row at
#:   different pagination offsets cite the same cell, and an identity that moved with the offset
#:   would defeat the very instability `order_by` was made total to remove.
#: * ``ocr_conf`` — a measurement of how well the value was READ, not of where it is. The same
#:   bounding box on the same page is the same location at 0.71 and at 0.99.
_NON_LOCATOR_REQUIRED: frozenset[str] = frozenset({"row_ordinal", "ocr_conf"})

#: The two `SnapshotIdentity` fields that name a LOCATION. See `canonical_pointer` rule 3.
_SNAPSHOT_LOCATOR_FIELDS: tuple[str, ...] = ("source_id", "query_hash")


def _canonical_snapshot(snapshot: Any) -> dict[str, Any]:
    return {name: canonical_scalar(getattr(snapshot, name, None))
            for name in _SNAPSHOT_LOCATOR_FIELDS}


def canonical_pointer(pointer: SourcePointer) -> dict[str, Any]:
    """One pointer, rendered into its **locator identity**: *where did this value come from?*

    Deep, order-stable, Pydantic-independent, and — the part that took two reviews to get right —
    **an allowlist, not a dump.**

    ## Why an allowlist (scheme v3)

    v2 rendered every non-``None`` field. The day `packages/core` added `trust` and
    `capture_digest` to `SourcePointer`, every `read_hash`, `cache_key` and pointer-set digest in
    the system moved. The golden tests caught it, which is what they are for — but the shape of
    the failure is the real finding: **adding any field to `SourcePointer` silently invalidated
    every stored pointer identity**, and the only visible symptom was a golden test that a hurried
    engineer would "fix" by re-pinning. That is a trap with a very sharp edge and a long fuse.

    So the digest covers the locator and nothing else. A field added tomorrow is not in the
    allowlist, so it cannot move an identity that was issued yesterday; adding it to the allowlist
    is a deliberate act that comes with a `SCHEME_VERSION` bump.

    ## What counts as the locator, and the three judgement calls

    Base: ``source_id``, ``kind``, and that kind's entries in
    ``SourcePointer._REQUIRED_FIELDS`` — the fields without which the pointer does not construct,
    because they are precisely the ones that address something. Plus `_LOCATOR_EXTRAS`, minus
    `_NON_LOCATOR_REQUIRED`. Each of the three calls that is not mechanical:

    1. **``trust`` is EXCLUDED.** It is a statement about who could WRITE the bytes, declared in
       the source contract and re-derivable at read time (`databridge_core.effective_trust`). Two
       pointers to the same cell denote the same location however the deployment labels it, and a
       citation whose identity moved when a data owner reclassified a column would break every
       stored pointer into that column for a reason that has nothing to do with where the value
       came from.

    2. **``capture_digest`` is INCLUDED**, and it is the one that could go either way. It names
       *which bytes* — a `email_part` or `ocr_bbox` pointer resolves against the content-addressed
       Capture Store, and two pointers with the same ``message_id`` and ``part`` but different
       capture digests address two different captured objects. That is a location, not a label.
       It is also safe to include for the reason ``content_hash`` is not: a capture digest is
       immutable once written, so it can never make an identity drift under a value that changed.

    3. **``snapshot`` contributes only ``source_id`` and ``query_hash``.** Its ``content_hash`` is
       a digest OF THE VALUE — it is the thing `verify()` compares — so folding it in would make a
       pointer's identity change every time the cell it cites changed, which is precisely backwards.
       ``as_of`` and ``schema_hash`` are excluded for the same reason: they describe the read, not
       the address.

    ## Why DEEP

    `SourcePointer` holds `facts` and `derived_from` as LISTS and Pydantic does not deep-freeze
    them, so a pointer already hashed into a request can have a parent appended afterwards. If the
    request recomputed its digest lazily, a citation's identity would change after it was issued,
    and `__hash__` would change after the object was used as a dict key. Rendering deeply gives
    the caller something it can freeze once; `CapabilityRequest` does exactly that at construction.
    """
    kind = pointer.kind
    fields: list[str] = ["source_id", "kind", "capture_digest"]
    fields.extend(f for f in _REQUIRED_FIELDS.get(kind, ())
                  if f not in _NON_LOCATOR_REQUIRED)
    fields.extend(_LOCATOR_EXTRAS.get(kind, ()))

    out: dict[str, Any] = {}
    for name in sorted(set(fields)):
        value = getattr(pointer, name, None)
        if value is None:
            continue
        if name in _POINTER_CHILD_FIELDS:
            out[name] = canonical_pointer(value)
        elif name in _POINTER_LIST_FIELDS:
            # Order is preserved: `derived_from=[a, b]` and `[b, a]` are different derivations
            # (a difference of two operands is not commutative), so they must not share a digest.
            out[name] = [canonical_pointer(p) for p in value]
        elif name == "snapshot":
            out[name] = _canonical_snapshot(value)
        else:
            out[name] = canonical_scalar(value)
    return out


def pointer_digest(pointer: SourcePointer) -> str:
    """The locator identity of one pointer, digested. See `canonical_pointer` on what it covers."""
    return sha256_of(canonical_json(canonical_pointer(pointer)))


def pointer_set_digest(pointers: Iterable[SourcePointer]) -> str:
    """A digest of the pointer SET being re-resolved (`ADAPTER_AGENT_SPEC` §2).

    Order-independent - the per-pointer digests are sorted - because asking for the same three
    pointers in a different order is the same read, and a query-plan or iteration-order change
    must not read as a different one. Duplicates are kept: a set of three pointers and a set of
    the same pointer three times are different requests.
    """
    return sha256_of(canonical_json(sorted(pointer_digest(p) for p in pointers)))


def digest_mapping(mapping: Mapping[str, Any]) -> str:
    """A digest over a flat mapping of strings - used for entitlement hashing."""
    return sha256_of(canonical_json({str(k): str(v) for k, v in mapping.items()}))
