"""**A pointer proves citation, not grounding** (`ADAPTER_AGENT_SPEC` §4a).

`GroundedRows`' validator checks that every cell is *accounted for*. It does not - and nothing
that only inspects the pointer object ever could - establish that `cell.value` is what the cited
location actually contains. A Core can emit ``"eligible"`` with a perfectly well-formed
``query_cell`` pointer and pass every structural check in the package.

So grounding is a **verification step against the bytes the read returned**, not a wrapper type.
This is the same rule `packages/convert/.../extract/grounding.py` already applies to extracted
document values, moved down to the adapter boundary so it holds for every source rather than only
for documents. It is deliberately a separate, stricter function: the document wall fuzzy-matches
names at 0.90 token-set ratio because OCR and layout make exactness unreasonable there, while a
database cell either is the value or is not.

## What review R-3A found, and what changed

The first version of this module had one matcher for every verbatim kind, and it was the loose
one. Three consequences, each of which lets a fabricated value pass:

* ``"eligible"`` was accepted against fetched ``"ineligible"`` - substring containment applied to
  an exact database cell;
* ``"A  B"`` was accepted against ``"A B"`` - whitespace collapsing, which this module's own
  docstring says requires a `derived` pointer;
* when ``fetched`` was a whole row, the value could match **any** column, not the one the pointer
  names - so a pointer citing ``party.legal_name`` was satisfied by the contents of
  ``party.notes``.

The fix is that verbatim kinds split in two, because they make two different claims:

| Group | Kinds | The claim | The test |
|---|---|---|---|
| **exact** | ``table_cell``, ``query_cell``, ``workbook_cell`` | *this cell IS this value* | equality, and a row is indexed BY THE POINTER'S COLUMN |
| **span** | ``doc_span``, ``transcript_span``, ``email_part``, ``ocr_bbox``, ``code_node`` | *this range of that artefact CONTAINS this value* | the cited offsets are sliced first, and only what has no offsets falls back to containment |

`derived` gets its own section below, because it had no value-level check at all.

**The gap that remains, stated plainly.** ``assert_grounded(cell, fetched)`` cannot establish that
``fetched`` came from ``cell.pointer``: it is given bytes by its caller, and a caller that hands it
accommodating bytes from somewhere else will pass. That is unfixable from inside this function -
resolving the pointer would require a driver, and this package has none. What closes it is
`assert_cell_grounded_in_row`, which the Core uses: the Core holds one raw row from one query and
indexes it by the pointer's own column, so the bytes and the citation come from the same fetch by
construction. The residual risk is a Core that lies to itself, which is a Core bug, and
`verify()` re-reading the pointer from the source is the independent check on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Optional, Sequence

from databridge_core import GroundedValue, SourcePointer

from .errors import UngroundedValue

#: Kinds whose contract is "the cited cell IS this value". Equality, nothing looser.
EXACT_KINDS = frozenset({"table_cell", "query_cell", "workbook_cell"})

#: Kinds whose contract is "the cited RANGE of that artefact contains this value". A span cites a
#: slice of a larger block, so containment is the honest test - but only after the slice named by
#: the pointer's own offsets has been taken, where the pointer carries offsets.
SPAN_KINDS = frozenset({"doc_span", "transcript_span", "ocr_bbox", "email_part", "code_node"})

#: Pointer kinds `assert_grounded` can check verbatim. The check is mandatory for all of them.
VERBATIM_KINDS = EXACT_KINDS | SPAN_KINDS

#: Kinds whose contract is "this value was COMPUTED from cited locations". Verbatim equality is
#: the wrong question; the right one is whether the stated derivation, replayed over the cited
#: parents, produces exactly this value. See `replay_derivation`.
DERIVED_KINDS = frozenset({"derived"})

#: Model output. Never verbatim, never trusted, and never emitted by an adapter Core - a Core has
#: no model. Present in the map so that an attempt to smuggle one through the read path is a
#: named refusal rather than a silent pass.
GENERATED_KINDS = frozenset({"generated_claim"})

_WS = re.compile(r"\s+")


def _norm_text(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _as_decimal(value: Any) -> "Decimal | None":
    if isinstance(value, bool):
        return None                       # bool is an int in Python; do NOT let True match 1
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    return None


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:                                # pragma: no cover - defensive
            return value
    return value


# ---------------------------------------------------------------------------------------
# exact kinds
# ---------------------------------------------------------------------------------------


def exact_match(value: Any, fetched: Any) -> bool:
    """Is ``value`` *the* content of the cited cell? Equality, deliberately unforgiving.

    Rules, in order, and each one exists because its absence let something through:

    1. ``None`` matches only ``None``. A null is a fact about the source; matching it against a
       value would let an adapter invent content for an empty cell.
    2. ``bool`` matches only the same ``bool``. Python's ``True == 1`` is exactly the coincidence
       that makes a fabrication look grounded.
    3. two numbers compare by value across ``int``/``float``/``Decimal``, because which of those
       a driver hands back for one column is a driver detail, not a fabrication. A number against
       a **string** is refused: rendering a number as text is a transformation, and
       transformations take a `derived` pointer.
    4. dates and datetimes compare by equality only. Their ISO text is a rendering, so it is
       refused here for the same reason as (3) - `verbatim_match` still accepts it for spans,
       where the value genuinely appears as text inside a document.
    5. text compares by **exact equality**. No whitespace collapsing, no case folding, no
       containment. The module docstring says whitespace collapsing needs a `derived` pointer;
       this is that sentence enforced instead of stated.
    """
    value, fetched = _decode(value), _decode(fetched)
    if value is None or fetched is None:
        return value is None and fetched is None
    if isinstance(value, bool) or isinstance(fetched, bool):
        return isinstance(value, bool) and isinstance(fetched, bool) and value is fetched
    if isinstance(value, (datetime, date)) or isinstance(fetched, (datetime, date)):
        return type(value) is type(fetched) and value == fetched
    dv, df = _as_decimal(value), _as_decimal(fetched)
    if dv is not None and df is not None:
        return dv == df
    if (dv is None) != (df is None):
        return False            # number against text: a rendering, not a citation
    if isinstance(value, str) and isinstance(fetched, str):
        return value == fetched
    return type(value) is type(fetched) and value == fetched


# ---------------------------------------------------------------------------------------
# span kinds
# ---------------------------------------------------------------------------------------


def _cited_slice(pointer: SourcePointer, fetched: Any) -> Any:
    """Narrow ``fetched`` to the range the pointer actually cites, when it names one.

    This is what stops ``"eligible"`` grounding against a document that happens to contain
    ``"ineligible"`` somewhere: the pointer said characters 4120-4180, so those are the only
    characters that may vouch for it.
    """
    text = _decode(fetched)
    if not isinstance(text, str):
        return fetched
    start, end = pointer.char_start, pointer.char_end
    if start is None or end is None:
        return text
    if start < 0 or end > len(text) or end < start:
        # The offsets do not fit the block. Return a sentinel that matches nothing rather than
        # silently widening to the whole document, which would turn a broken locator into a
        # generous one.
        return _OUT_OF_RANGE
    return text[start:end]


class _OutOfRange:
    def __repr__(self) -> str:                                   # pragma: no cover - display
        return "<cited range does not fit the fetched block>"


_OUT_OF_RANGE = _OutOfRange()


def verbatim_match(value: Any, fetched: Any) -> bool:
    """Is ``value`` present in ``fetched``? The **span** test: containment, whitespace-tolerant.

    Used for the span kinds and nothing else. `assert_grounded` slices the cited range out of
    ``fetched`` before calling this wherever the pointer carries offsets, so containment is
    scoped to the range that was actually cited rather than to the whole artefact.

    ``fetched`` may be a scalar, a mapping or a list - some sources return a value only as part of
    a structure, and forcing the caller to index it first would move the unchecked step somewhere
    this function cannot see. For an EXACT kind that leniency is a hole, which is why exact kinds
    do not come here.
    """
    if value is None:
        return fetched is None
    if fetched is _OUT_OF_RANGE:
        return False
    if isinstance(fetched, Mapping):
        return any(verbatim_match(value, v) for v in fetched.values())
    if isinstance(fetched, (list, tuple, set, frozenset)) and not isinstance(fetched, (str, bytes)):
        return any(verbatim_match(value, v) for v in fetched)
    if fetched is None:
        return False

    if isinstance(value, bool) or isinstance(fetched, bool):
        return isinstance(value, bool) and isinstance(fetched, bool) and value is fetched

    if isinstance(value, (datetime, date)) or isinstance(fetched, (datetime, date)):
        if type(value) is type(fetched):
            return value == fetched
        iso = value.isoformat() if isinstance(value, (datetime, date)) else str(value)
        other = (fetched.isoformat() if isinstance(fetched, (datetime, date)) else str(fetched))
        return _norm_text(iso) in _norm_text(other) or _norm_text(other) in _norm_text(iso)

    dv, df = _as_decimal(value), _as_decimal(fetched)
    if dv is None and isinstance(value, str):
        try:
            dv = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            dv = None
    if df is None and isinstance(fetched, str):
        try:
            df = Decimal(fetched.strip())
        except (InvalidOperation, ValueError):
            df = None
    if dv is not None and df is not None:
        return dv == df

    sv, sf = _norm_text(str(_decode(value))), _norm_text(str(_decode(fetched)))
    if not sv:
        # An empty emitted value cannot be "found" anywhere: every string contains "". Treat it as
        # grounded only against an equally empty cell, which is what the source actually said.
        return sf == ""
    return sv == sf or sv in sf


# ---------------------------------------------------------------------------------------
# derived kinds — the replayable derivation registry
# ---------------------------------------------------------------------------------------
#
# R-3A finding 2: a `derived` pointer previously passed the wall on the strength of a non-blank
# `derivation` string. Any value could be emitted with `derivation="redact"` and a plausible
# parent, and nothing checked that the derivation, applied to the parent, produced that value.
# "Provenance structurally present" was being reported as "derivation verified".
#
# So a derivation is now a CALL into a closed, versioned registry, written in one canonical form:
#
#     redact_pii@v1(column=email)
#     redact_pii@v1(column=email) — RFC5322 local-part and domain replaced
#
# `assert_grounded` parses it, looks the rule up, replays it over the cited parent values, and
# requires the result to equal the emitted value exactly. A derivation that names no registered
# rule is refused: a derivation nobody can replay is a claim, and a claim is what a `derived`
# pointer exists not to be.

_DERIVATION_CALL = re.compile(
    r"^\s*(?P<name>[a-z][a-z0-9_]*)@v(?P<version>\d+)\s*\((?P<args>[^()]*)\)\s*"
    r"(?:[-—]{1,2}\s*(?P<note>.*))?$", re.DOTALL)


@dataclass(frozen=True)
class DerivationCall:
    """A parsed `derivation` string: which rule, which version, which arguments."""

    name: str
    version: int
    args: Mapping[str, str]
    note: Optional[str] = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.name, self.version)

    def render(self) -> str:
        args = ",".join(f"{k}={self.args[k]}" for k in sorted(self.args))
        return f"{self.name}@v{self.version}({args})"


#: ``(name, version) -> (fn, doc)``. ``fn(parents, args) -> Any``. A rule is registered ONCE and
#: never mutated: replaying an old pointer under a changed rule would silently re-certify a value
#: the old rule never produced, so a change is a new version.
_DERIVATIONS: dict[tuple[str, int], tuple[Callable[[Sequence[Any], Mapping[str, str]], Any], str]]
_DERIVATIONS = {}


class DerivationError(UngroundedValue):
    """A derivation cannot be parsed, is not registered, or does not replay to the emitted value.

    A subclass of `UngroundedValue` because that is what it means: the value is not established by
    what the pointer cites. Its own type so a Core can distinguish "the rule is missing from this
    process" from "the value is wrong", which are different operator responses.
    """


def register_derivation(name: str, version: int,
                        fn: Callable[[Sequence[Any], Mapping[str, str]], Any],
                        *, doc: str = "") -> None:
    """Register a deterministic, replayable derivation rule.

    Re-registering the same ``(name, version)`` with a different function is refused. The whole
    value of a versioned registry is that replaying a five-year-old pointer runs the rule that
    produced it; a rule that can be swapped in place would re-certify values it never computed.
    """
    key = (name, int(version))
    existing = _DERIVATIONS.get(key)
    if existing is not None and existing[0] is not fn:
        raise DerivationError(
            f"derivation {name}@v{version} is already registered with a different function. A "
            f"changed rule is a NEW VERSION: replaying a stored pointer must run the rule that "
            f"produced it, or the replay certifies a value the old rule never computed")
    _DERIVATIONS[key] = (fn, doc)


def registered_derivations() -> tuple[str, ...]:
    return tuple(sorted(f"{n}@v{v}" for n, v in _DERIVATIONS))


def parse_derivation(text: str) -> DerivationCall:
    """Parse a canonical derivation string, or refuse with the form it should have taken."""
    if not (text or "").strip():
        raise DerivationError(
            "a derived pointer must state its derivation: without it the emitted value is "
            "unrelated to the cited parents as far as any later reader can tell")
    m = _DERIVATION_CALL.match(text)
    if not m:
        raise DerivationError(
            f"derivation {text!r} is not a replayable call. The canonical form is "
            f"'<name>@v<n>(<k>=<v>,...)', optionally followed by ' - <prose>'. Registered rules: "
            f"{', '.join(registered_derivations()) or '(none)'}. Free prose is refused because a "
            f"derivation nobody can replay is a claim, and a `derived` pointer exists precisely "
            f"not to be one (R-3A finding 2)")
    args: dict[str, str] = {}
    body = (m.group("args") or "").strip()
    if body:
        for part in body.split(","):
            if "=" not in part:
                raise DerivationError(
                    f"derivation {text!r}: argument {part.strip()!r} is not '<key>=<value>'")
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                raise DerivationError(f"derivation {text!r}: blank argument name")
            if k in args:
                raise DerivationError(f"derivation {text!r}: duplicate argument {k!r}")
            args[k] = v
    note = (m.group("note") or "").strip() or None
    return DerivationCall(name=m.group("name"), version=int(m.group("version")), args=args,
                          note=note)


def replay_derivation(call: DerivationCall, parents: Sequence[Any]) -> Any:
    """Run a registered rule over the cited parent values and return what it produces."""
    entry = _DERIVATIONS.get(call.key)
    if entry is None:
        raise DerivationError(
            f"derivation {call.name}@v{call.version} is not registered in this process. "
            f"Registered: {', '.join(registered_derivations()) or '(none)'}. Import the module "
            f"that owns the rule before verifying a pointer that cites it - failing closed here "
            f"is deliberate, because the alternative is accepting a derivation nobody can run")
    fn, _doc = entry
    try:
        return fn(list(parents), dict(call.args))
    except DerivationError:
        raise
    except Exception as exc:
        raise DerivationError(
            f"derivation {call.render()} raised while replaying over {len(parents)} parent "
            f"value(s): {exc}") from exc


def assert_provenance_present(cell: GroundedValue) -> None:
    """The **weaker** check: the pointer's provenance is structurally complete.

    Named at length so it can never be mistaken for the wall. It establishes that a `derived`
    pointer states a derivation and cites parents. It establishes **nothing** about whether the
    emitted value is what that derivation produces - that is `assert_grounded`, and it is the
    distinction R-3A finding 2 asked for. Use this only where the parent values genuinely cannot
    be produced (an aggregation compressed to a predicate over a snapshot), and expect the caller
    to re-run the predicate instead.
    """
    ptr = cell.pointer
    if ptr.kind not in DERIVED_KINDS:
        raise UngroundedValue(f"assert_provenance_present is for derived pointers, got {ptr.kind!r}")
    parse_derivation(ptr.derivation or "")
    if not (ptr.derived_from or ptr.predicate):
        raise UngroundedValue(
            "a derived pointer must cite its parents (`derived_from`) or carry a re-runnable "
            "predicate bundle; without either, the emitted value has no inputs")


# ---------------------------------------------------------------------------------------
# the wall
# ---------------------------------------------------------------------------------------


def assert_grounded(cell: GroundedValue, fetched: Any) -> None:
    """The wall, run against the bytes the read actually returned.

    Call this at EMISSION, inside the adapter Core, before the cell joins a `GroundedRows` - not
    in a test, and not in review. `ADAPTER_AGENT_SPEC` §10 is explicit that an ungrounded cell is
    caught at the boundary and refuses the whole result.

    ``fetched`` is what the read returned for this cell: the raw scalar, or - for an exact kind -
    the raw row, which is then indexed by the pointer's own ``column``. For a `derived` pointer it
    is the PARENT value (or a sequence of them), because that is what the derivation is replayed
    over.

    Raises `UngroundedValue`. Never returns a boolean: a caller that can ignore the answer will.
    """
    kind = cell.pointer.kind
    if kind in GENERATED_KINDS:
        raise UngroundedValue(
            f"a {kind!r} pointer has no place in an adapter read: a Core is deterministic and has "
            f"no model, so a generated claim reaching this path means a value was produced rather "
            f"than fetched (§0, D1)")

    if kind in DERIVED_KINDS:
        call = parse_derivation(cell.pointer.derivation or "")
        parents = (list(fetched) if isinstance(fetched, (list, tuple))
                   and not isinstance(fetched, (str, bytes)) else [fetched])
        produced = replay_derivation(call, parents)
        if not exact_match(cell.value, produced):
            raise DerivationError(
                f"derivation {call.render()} replayed over the cited parent(s) produces "
                f"{produced!r}, but the cell emits {cell.value!r}. A derived pointer is a promise "
                f"that this exact rule, applied to those exact parents, yields this value; when "
                f"it does not, the value is unsourced with a plausible label (§4a)")
        return

    if kind in EXACT_KINDS:
        raw = fetched
        if isinstance(fetched, Mapping):
            column = cell.pointer.column
            if not column:
                raise UngroundedValue(
                    f"a {kind!r} pointer with no `column` was checked against a whole row: there "
                    f"is no way to say WHICH cell is supposed to contain the value, and searching "
                    f"every column would let a pointer citing one field be vouched for by another")
            if column not in fetched:
                raise UngroundedValue(
                    f"{kind} cites column {column!r}, which the fetched row does not contain "
                    f"(row has: {', '.join(map(str, fetched))}). The citation names a location "
                    f"the read did not return")
            raw = fetched[column]
        if not exact_match(cell.value, raw):
            raise UngroundedValue(
                f"{kind} cites a location whose content is {raw!r}, not {cell.value!r} - the "
                f"pointer is well-formed and the value is fabricated. If this value was "
                f"TRANSFORMED (redaction, normalisation, unit conversion, rendering a number as "
                f"text), emit it with a `derived` pointer carrying a registered `derivation` and "
                f"`derived_from=[<the raw pointer>]` instead (§4a)")
        return

    if kind in SPAN_KINDS:
        if not verbatim_match(cell.value, _cited_slice(cell.pointer, fetched)):
            raise UngroundedValue(
                f"{kind} cites a range whose content does not contain {cell.value!r} - the "
                f"pointer is well-formed and the value is fabricated. If this value was "
                f"TRANSFORMED, emit it with a `derived` pointer carrying a registered "
                f"`derivation` and `derived_from=[<the raw pointer>]` instead (§4a)")
        return

    raise UngroundedValue(                                       # pragma: no cover - defensive
        f"unknown pointer kind {kind!r}: grounding cannot be checked for a kind this wall does "
        f"not classify, and passing it would make the wall optional")


def assert_derived_from(pointer: SourcePointer, raw: SourcePointer) -> None:
    """A transformed cell must still cite the raw location it was computed from.

    Convenience for the redaction path in §4a, and a check that the chain was not simply dropped:
    a `derived` pointer whose `derived_from` does not include the raw cell has quietly become an
    unsourced value with a plausible label.
    """
    if pointer.kind != "derived":
        raise UngroundedValue(
            f"a transformed value needs a 'derived' pointer, got {pointer.kind!r}")
    parents = list(pointer.derived_from or ())
    if raw not in parents:
        raise UngroundedValue(
            "a derived pointer must list the raw pointer it was computed from in `derived_from`; "
            "without it the raw cell is no longer resolvable by a principal entitled to see it, "
            "and the redaction has laundered a value into an unsourced one")


def assert_cell_grounded_in_row(cell: GroundedValue, row: Mapping[str, Any]) -> None:
    """Ground one cell against **the row the read returned**, indexed by the pointer's column.

    This is the form a Core must use, and the reason is the gap named in the module docstring:
    `assert_grounded` is handed bytes and cannot know they came from the cited location. Here the
    row is the one the query produced and the column is the pointer's own, so the evidence and the
    citation come from the same fetch by construction rather than by the caller's good intentions.

    A `derived` cell is grounded against its RAW parent value, which is read out of the same row
    using the first parent pointer's column - so the replay runs over the cell the derivation
    claims to have transformed, not over something else in the row.
    """
    ptr = cell.pointer
    if ptr.kind in DERIVED_KINDS:
        parents = list(ptr.derived_from or ())
        if not parents:
            raise UngroundedValue(
                "a derived cell emitted from a row read must cite the raw cell it transformed")
        columns = [p.column for p in parents]
        if any(c is None for c in columns):
            raise UngroundedValue(
                "a derived cell's parent pointer names no column, so the raw value it was "
                "computed from cannot be found in the row that was read")
        missing = [c for c in columns if c not in row]
        if missing:
            raise UngroundedValue(
                f"derived cell cites parent column(s) {missing} the fetched row does not contain")
        assert_grounded(cell, [row[c] for c in columns])
        return
    assert_grounded(cell, row)


def assert_cells_grounded(cells: Sequence[GroundedValue], fetched: Sequence[Any]) -> None:
    """`assert_grounded` over a row. Arity-checked, because a silent zip() truncation would skip
    exactly the trailing cells nobody reviews."""
    if len(cells) != len(fetched):
        raise UngroundedValue(
            f"grounding check arity mismatch: {len(cells)} cells against {len(fetched)} fetched "
            f"values - a zip() here would silently skip the tail")
    for cell, raw in zip(cells, fetched):
        assert_grounded(cell, raw)
