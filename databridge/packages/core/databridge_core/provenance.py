"""Provenance — the legal system of the codebase.

**The universal claim, stated precisely** (it was previously stated too broadly, and review
R-003 finding 5 was right to call that out):

    Every value DataBridge **discloses** is a ``GroundedValue`` carrying a ``SourcePointer``
    back to where it came from.

A cell that discloses no value is not a counter-example to that claim — it is the *absence* of
a disclosure, and it is grounded in a different thing. ``Withheld`` carries a
``WithholdingRecord``: the *decision* that produced the non-answer (policy object, policy
version, principal, purpose, decision event). ``Absent`` carries the ``SnapshotIdentity`` the
question was asked of. So the honest formulation is: **a disclosure is grounded in a source; a
non-answer is grounded in a decision or a snapshot; nothing is ungrounded.** A pointer *to the
withheld value* is exactly what these types must never carry, because it would vouch for
something the consumer was never shown.

**A disclosure need not be verbatim** (spine O1b, closed 2026-08-16). ``Transformed`` is the
fourth cell state: a masked, redacted, generalised or bucketed value — a *partial* disclosure,
which is a first-class outcome of a policy decision rather than a degenerate ``Withheld``. It
is grounded in **both** kinds of proof at once, and that is what makes it its own state: a
``derived`` ``SourcePointer`` for the bytes actually handed over, plus a ``TransformRecord`` for
the decision that narrowed them. The claim above still holds without amendment — the emitted
value carries a pointer, and that pointer says out loud, by its very kind, that what was emitted
is a named function of what was cited rather than a copy of it. What ``Transformed``
deliberately is **not** is a subclass of ``GroundedValue``: a mask must not be readable as a
full disclosure by anything that pattern-matches on cell type.

P4: an untraceable value is a bug, not a feature. Making provenance a wrapper TYPE (not a
side-channel log) means the type checker enforces it — a converter that returns a raw ``str``
instead of a ``GroundedValue`` fails review.

This is the EXTENDED v2 ``SourcePointer`` (02_TRD §A.3 core kinds + 09_FINAL_PRD §17.1
additive kinds: query_cell, workbook_cell, ocr_bbox, email_part, generated_claim, and
the derived-aggregation compression). Built v2 directly per PROMPT 1 because every later
package depends on it and a v1→v2 migration would touch everything.

All models here derive from ``_Sealed``: frozen, ``extra="forbid"``,
``revalidate_instances="always"``, ``model_construct`` refused, and ``model_copy`` re-validated.
Frozen alone was not enough. ``trust`` is an information-flow-control label, and a security
control with a one-line supported-API bypass is not a control — see ``_Sealed`` for why each of
those four is load-bearing and what is still left open.

``extra="forbid"``: passing a field the model does not declare must fail at the call site, not
silently vanish and resurface as an ``AttributeError`` three layers away. That failure mode is
why ``trust`` and ``capture_digest`` had to land in the *same* change as the ``forbid``
(ADAPTER_AGENT_SPEC preamble).

Consult the ``databridge-ir-conventions`` and ``databridge-provenance-check`` skills
before changing this file.
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, model_validator

# The closed set of pointer kinds. Core (02_TRD §A.3) + additive v2 (09_FINAL_PRD §17.1).
SourcePointerKind = Literal[
    "table_cell",
    "doc_span",
    "transcript_span",
    "code_node",
    "derived",
    "query_cell",
    "workbook_cell",
    "ocr_bbox",
    "email_part",
    "generated_claim",
]

# Per-kind required locator fields. A pointer is valid iff every listed field is non-None.
# ``workbook_cell`` and ``derived`` carry extra "one-of" rules checked in the validator.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "table_cell": ("table", "column", "row_id"),
    "doc_span": ("document_id", "char_start", "char_end"),
    "transcript_span": ("document_id", "char_start", "char_end", "timestamp_ms", "segment_idx"),
    "code_node": ("file_path", "ast_node_id"),
    "query_cell": ("snapshot", "query_hash", "row_ordinal", "column"),
    "ocr_bbox": ("media_id", "page", "x0", "y0", "x1", "y1", "ocr_conf"),
    "email_part": ("message_id", "part"),
    "generated_claim": ("claim_id", "facts"),
    "workbook_cell": ("workbook_sha", "sheet"),
    "derived": ("derivation",),
}


class _Sealed(BaseModel):
    """Frozen was not enough: the four supported Pydantic APIs that walked around it, closed.

    ``SourcePointer.trust`` is an information-flow-control label (spine D3). Everything else in
    this file is a *correctness* invariant, where "``model_construct`` skips validation, as
    documented" is an honest limit to record and move on from — that is what
    ``test_grounding_wall_cannot_be_walked_around.py`` does, correctly. A **security** label is
    different: a one-line laundering path is not a limit, it is the absence of a control. Review
    R-003 finding 1 named four such paths, all of them public, supported and one line long:

    1. ``ptr.model_copy(update={"trust": Trust.TRUSTED})`` — writes straight into ``__dict__``;
       Pydantic's own docstring says "the data is not validated". Overridden below to re-validate,
       which also closes ``copy.replace()`` / ``__replace__``, since Pydantic implements those
       *as* ``model_copy(update=...)``.
    2. ``SourcePointer.model_construct(...)`` — its entire purpose is to skip validation. Refused
       outright rather than quietly validated: a caller who wants a validated pointer should say
       ``model_validate``, and one who wants an unvalidated one should not get it here.
    3. ``SourcePointer.model_validate(forged_instance)`` — by default Pydantic returns an existing
       instance *without* re-running validators, so anything forged by (1) or (2) crossed an
       apparent validation boundary untouched. ``revalidate_instances="always"`` closes it, and
       does so **transitively**: a forged pointer nested as ``derived_from``/``facts``/``child``,
       or handed to ``GroundedValue(pointer=...)``, is re-validated at that moment and raises.
    4. ``extra`` smuggling through the above — all three now land in a validated path.

    **What is still open, stated plainly** (do not read past this):

    * ``object.__setattr__(ptr, "trust", Trust.TRUSTED)`` still writes the field. Nothing in
      Python can stop a caller reaching behind the type. What it *cannot* do is survive being
      read: ``effective_trust`` re-derives the label from ancestry at read time, so a forged
      label on a pointer that has untrusted parents reads back ``UNTRUSTED`` — and re-validation
      (3) raises the moment that pointer is nested anywhere. See
      ``test_trust_lattice_cannot_be_laundered.py`` for the exact residue.
    * ``pickle``/``__setstate__`` restores ``__dict__`` without validation. Loading an untrusted
      pickle is already arbitrary code execution, so this is not a distinct hole.
    * A **root** pointer minted with ``trust=TRUSTED`` over bytes that were in fact third-party
      writable is not detectable here at all. Trust on a root comes from the adapter's source
      contract, and this module has no access to source contracts. That is the honest boundary of
      what a type can enforce, and it is why D3 says the label is *stamped by the adapter Core*.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    @classmethod
    def model_construct(cls, _fields_set=None, **values):   # type: ignore[override]
        """Refused. ``model_construct`` exists to skip validation; these types cannot afford that."""
        raise TypeError(
            f"{cls.__name__}.model_construct() is REFUSED: it exists to bypass validation, and "
            "these types carry the trust label and the grounding invariants that validation "
            "enforces. Use model_validate() if the input needs checking, or construct normally.")

    def model_copy(self, *, update=None, deep: bool = False):    # type: ignore[override]
        """Copy, then RE-VALIDATE. Pydantic's default writes ``update`` into ``__dict__`` unchecked.

        Also reached by ``copy.replace()`` and ``dataclasses``-style ``__replace__``, both of which
        Pydantic implements in terms of this method.
        """
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(dict(copied.__dict__))


class Trust(str, Enum):
    """Whether a value derived from this pointer may influence CONTROL FLOW (spine D3).

    An untrusted value may be stored, cited, displayed, materialised and ledgered. It may
    never select an action, a target, a tool, an adapter, a branch, a recipient or a loop
    bound. This is the CaMeL information-flow rule expressed as one enum on a type that is
    already threaded through every emitted value.

    **Not a confidence score.** A byte-exact, perfectly extracted quotation from an email
    body is ``UNTRUSTED`` — because the email is attacker-writable, and its *accuracy* has
    nothing to do with whether it may steer the system.

    **Not self-asserted.** The label is stamped by an adapter Core from its source
    contract, per field (who could write the bytes), never per source and never inferred
    by a model — which would let the thing being contained decide whether it is contained.
    """

    TRUSTED = "trusted"        # operator-controlled: config, pack YAML, staff-entered columns
    UNTRUSTED = "untrusted"    # third-party writable: documents, mail, chat, OCR, model output


def _parents_of(pointer: "SourcePointer") -> list["SourcePointer"]:
    """Every pointer this one is built on top of, across all three nesting fields.

    Collected regardless of ``kind``: ``derived_from`` legally appears on any kind (a
    ``doc_span`` inside an email attachment cites the ``email_part`` it came out of), so
    keying this off ``kind`` would leave a hole exactly where the chains are longest.
    """
    parents: list["SourcePointer"] = []
    parents.extend(pointer.derived_from or ())
    parents.extend(pointer.facts or ())
    if pointer.child is not None:
        parents.append(pointer.child)
    return parents


#: Kinds that are, by definition, BUILT FROM something else. A pointer of one of these kinds
#: with no parents has not compressed its provenance, it has dropped it — so it may never claim
#: TRUSTED (R-003 finding 2). Root kinds are absent from this set on purpose: a `table_cell` into
#: a staff-only registry column legitimately has no ancestry, and its trust comes from the
#: adapter's source contract.
_KINDS_THAT_IMPLY_ANCESTRY = frozenset({"derived", "generated_claim"})


def effective_trust(pointer: "SourcePointer") -> Optional[Trust]:
    """Re-derive the trust label from ancestry, at READ time. The runtime backstop.

    The stored ``pointer.trust`` field and this function agree for every validly constructed
    pointer that carries a label — ``_check_trust_is_consistent`` is what makes them agree. The
    case this exists for is the one where they do not: a label written by something that skipped
    validation. **A laundered label does not survive being read.** (There is a second, benign
    divergence, described at the bottom of this docstring.)

    Three demotions, never a promotion. This function can only ever make a label *more*
    restrictive, so it is safe to call anywhere and can never itself be the thing that grants
    trust:

    1. a stored ``UNTRUSTED`` stays ``UNTRUSTED``;
    2. any parent that does not itself read back ``TRUSTED`` demotes the child — recursively, so
       depth does not dilute taint even if an intermediate label was forged;
    3. a ``generated_claim`` reads back ``UNTRUSTED`` whatever is stored on it, and a parentless
       pointer of an ancestry-implying kind reads back ``UNTRUSTED`` rather than trusted.

    ``None`` (unlabelled, the in-flight D3 migration state) is preserved **on a root**, rather
    than promoted to ``UNTRUSTED``: "never considered" and "deliberately untrusted" must stay
    distinguishable, and every *consumer* of a label already treats ``None`` as untrusted via
    ``trust_from_parents``. An unlabelled pointer *over untrusted ancestry* still reads
    ``UNTRUSTED``, because rule 2 is a demotion and demotions do not need a label to start from.

    So this function and the stored field agree for every validly constructed pointer that
    carries a label, and can differ in exactly two safe directions: the unlabelled-over-untrusted
    case just described, and a forged label, which reads back as whatever the ancestry says.
    Neither difference can make anything more trusted than the stored field claims.
    """
    stored = pointer.trust
    if stored is Trust.UNTRUSTED:
        return Trust.UNTRUSTED
    if pointer.kind == "generated_claim":
        return Trust.UNTRUSTED
    parents = _parents_of(pointer)
    if parents:
        if any(effective_trust(parent) is not Trust.TRUSTED for parent in parents):
            return Trust.UNTRUSTED
    elif stored is Trust.TRUSTED and pointer.kind in _KINDS_THAT_IMPLY_ANCESTRY:
        return Trust.UNTRUSTED
    return stored


def trust_from_parents(items: Iterable[Union["SourcePointer", Trust, None]]) -> Trust:
    """Least upper bound over the lattice ``{TRUSTED < UNTRUSTED}``, for trust PROPAGATION.

    Named for what it is for, not for the algebra (R-003 finding 9): the mathematical LUB of the
    empty set is the lattice bottom ``TRUSTED``, and returning that from a propagation helper
    would fail open. This function raises instead. ``trust_lub`` is kept as an alias because
    call sites outside this package import it by that name.

    Accepts pointers, bare labels, or a mix. The join of a set containing any untrusted member is
    untrusted — this is the single rule that makes laundering-by-aggregation impossible rather
    than merely discouraged. Pointer members join by their ``effective_trust``, not by their
    stored field, so a forged parent cannot launder a child through this helper either.

    An **unlabelled** member (``trust is None``, i.e. a pointer written before the D3 migration
    finished) joins as ``UNTRUSTED``. "Never considered" is not evidence of safety, and treating
    it as ``TRUSTED`` would be the exact fail-open the required-field migration exists to prevent.

    An **empty** input raises. Trust on a root pointer comes from the source contract, never
    from this function.
    """
    labels: list[Optional[Trust]] = []
    for item in items:
        if isinstance(item, SourcePointer):
            labels.append(effective_trust(item))
        elif item is None or isinstance(item, Trust):
            labels.append(item)
        else:
            raise TypeError(
                "trust_from_parents accepts SourcePointer | Trust | None, got "
                f"{type(item).__name__}")
    if not labels:
        raise ValueError(
            "trust_from_parents() over an empty set is not a trust label — a pointer with no "
            "parents takes its trust from the source contract, not from a least upper bound")
    return Trust.TRUSTED if all(label is Trust.TRUSTED for label in labels) else Trust.UNTRUSTED


#: Historical name. Same function; see ``trust_from_parents`` for why the name is misleading.
trust_lub = trust_from_parents


class SnapshotIdentity(_Sealed):
    """Identity of the exact source snapshot a value was read from (09_FINAL_PRD §17.2).

    Every IR carries one; artifacts pin ``schema_hash`` and declare snapshot requirements.

    Sealed like everything else here: it was the one model without ``extra="forbid"``, so a
    misspelled nested key vanished silently while the enclosing pointer's own extras were
    refused (R-003 finding 7).
    """

    source_id: str
    as_of: datetime
    content_hash: str
    schema_hash: str
    query_hash: Optional[str] = None


class SourcePointer(_Sealed):
    """Where a value came from. Exactly one locator family is set per ``kind``.

    The ``@model_validator`` enforces, per kind, that the required locator fields are
    present — so constructing a pointer of the wrong shape fails LOUDLY at construction
    time (the property test in tests/ exercises every kind).

    Read ``_Sealed`` before touching ``trust``: it is the reason the ordinary Pydantic escape
    hatches are closed on this class, and it names the two that are not.
    """

    source_id: str
    kind: SourcePointerKind

    # --- trust (spine D3) ---
    # Optional ONLY for the duration of the migration. The end state is a required field
    # with no default; the intermediate step is this plus a source scan
    # (``databridge_core.trust_gate``) that fails the build on a construction site which
    # omits it. A default would be a fail-open: every unmigrated call site would silently
    # acquire a label nobody chose, and afterwards "deliberately untrusted" and "never
    # considered" become indistinguishable forever.
    trust: Optional[Trust] = None

    # --- capture binding (spine D6) ---
    # Content address of the captured bytes this pointer was parsed out of. Mandatory in
    # practice for T3 sources and for anything ledger-bound: `email_part` and `ocr_bbox`
    # resolve against the Capture Store (content-addressed, WORM), not the live source.
    # Without it the capture store is decorative — nobody can prove WHICH bytes were read.
    # Not yet enforced per-kind, because existing pointers predate the Capture Store.
    capture_digest: Optional[str] = None

    # --- table_cell ---
    table: Optional[str] = None
    column: Optional[str] = None            # also used by query_cell
    row_id: Optional[str] = None

    # --- doc_span / transcript_span ---
    document_id: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    timestamp_ms: Optional[int] = None      # transcripts
    segment_idx: Optional[int] = None       # transcript_span (§17.1 addition)

    # --- code_node ---
    file_path: Optional[str] = None
    ast_node_id: Optional[str] = None

    # --- query_cell (§17.1) ---
    snapshot: Optional[SnapshotIdentity] = None   # also used by derived-compression
    query_hash: Optional[str] = None
    row_ordinal: Optional[int] = None

    # --- workbook_cell (§14.19, §17.1) ---
    workbook_sha: Optional[str] = None
    sheet: Optional[str] = None
    cell: Optional[str] = None              # "B7"
    cell_range: Optional[str] = None        # spec key "range", e.g. "A2:D90"

    # --- ocr_bbox (§17.1) ---
    media_id: Optional[str] = None
    page: Optional[int] = None
    x0: Optional[float] = None
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    ocr_conf: Optional[float] = None

    # --- email_part (§17.1) — provenance chains email→attachment→page→span ---
    message_id: Optional[str] = None
    part: Optional[str] = None              # "body" | "header:<name>" | "attachment:<idx>"
    child: Optional["SourcePointer"] = None

    # --- generated_claim (§17.1) ---
    claim_id: Optional[str] = None
    facts: Optional[list["SourcePointer"]] = None   # the cited fact pointers

    # --- derived (core + §17.1 aggregation compression) ---
    derived_from: Optional[list["SourcePointer"]] = None
    derivation: Optional[str] = None        # human-readable rule, e.g. "SUM over 7d window"
    agg_window: Optional[str] = None
    predicate: Optional[str] = None         # compression: the re-runnable predicate
    row_count: Optional[int] = None
    sample_row_ids: Optional[list[str]] = None     # ≤20
    rows_digest: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_for_kind(self) -> "SourcePointer":
        required = _REQUIRED_FIELDS.get(self.kind, ())
        missing = [f for f in required if getattr(self, f) is None]
        if missing:
            raise ValueError(
                f"SourcePointer(kind={self.kind!r}) missing required field(s): "
                f"{', '.join(missing)}"
            )
        # one-of rules
        if self.kind == "workbook_cell" and self.cell is None and self.cell_range is None:
            raise ValueError("workbook_cell requires one of `cell` or `cell_range`")
        if self.kind == "generated_claim" and not self.facts:
            raise ValueError("generated_claim requires a non-empty `facts` list")
        if self.kind == "derived":
            if not self.derived_from and self.predicate is None:
                raise ValueError(
                    "derived requires either `derived_from` (pointers) or `predicate` "
                    "(aggregation compression)"
                )
            if not self.derived_from and self.predicate is not None:
                # Compression path (§17.1) must be VERIFIABLE — re-run predicate against
                # the snapshot and compare the digest — so the full bundle is mandatory.
                missing = [f for f in ("snapshot", "row_count", "rows_digest")
                           if getattr(self, f) is None]
                if missing:
                    raise ValueError(
                        "derived compression (predicate set, no per-row pointers) requires "
                        f"{', '.join(missing)} to stay verifiable"
                    )
        if self.sample_row_ids is not None and len(self.sample_row_ids) > 20:
            raise ValueError("derived compression `sample_row_ids` is capped at 20 (§17.1)")
        return self

    @model_validator(mode="after")
    def _check_trust_is_consistent(self) -> "SourcePointer":
        """The three rules that make ``trust`` load-bearing instead of decorative (D3)."""
        parents = _parents_of(self)

        # (a) Model output is untrusted BY CONSTRUCTION, and "by construction" has to mean
        #     EXACTLY UNTRUSTED — not merely "not TRUSTED" (R-003 finding 3). Leaving `None`
        #     acceptable made the claim false in the two places it matters most: legacy JSON
        #     that predates the field, and `model_validate` on a dict that simply omits it.
        #     Both produced an unlabelled generated_claim, which every consumer then has to
        #     remember to treat as untrusted. The migration exemption does not apply here,
        #     because a generated_claim has no source contract to be migrated from: its label
        #     is knowable from its kind alone.
        if self.kind == "generated_claim" and self.trust is not Trust.UNTRUSTED:
            got = "unlabelled" if self.trust is None else self.trust.value
            raise ValueError(
                f"generated_claim is UNTRUSTED by construction (got {got}): model output may "
                "be stored, cited and displayed, never trusted. Stamp trust=Trust.UNTRUSTED "
                "explicitly — this kind has no source contract from which any other label "
                "could come")

        if self.trust is Trust.TRUSTED:
            # (b) A kind that is BUILT FROM something must SAY what from, or it cannot be
            #     trusted (R-003 finding 2). The validator used to skip the join whenever the
            #     parent list was empty, which made omitting ancestry strictly safer for a
            #     laundering caller than declaring it. The worst case was the §17.1 aggregation
            #     compression path, where `predicate` + `snapshot` + `rows_digest` and NO
            #     per-row pointers is legal: that compresses the per-row citations, and a
            #     caller could read it as licence to drop provenance entirely and mint
            #     `derived(trust=TRUSTED)` over rows nobody can characterise. Compression may
            #     drop the per-row pointers; it may not drop the trust-bearing root.
            if not parents and self.kind in _KINDS_THAT_IMPLY_ANCESTRY:
                raise ValueError(
                    f"trust laundering REFUSED (kind={self.kind!r}): a {self.kind!r} pointer is "
                    "by definition built from something, and this one names no parent — no "
                    "`derived_from`, no `facts`, no `child`. There is nothing for the trust join "
                    "to propagate from, so TRUSTED here is self-asserted. Aggregation "
                    "compression (predicate + snapshot + rows_digest) may drop the PER-ROW "
                    "pointers; it may not drop the trust-bearing root — put the row set's own "
                    "pointer in `derived_from`. An untrusted or unlabelled result is still "
                    "permitted with no parents; only TRUSTED is refused")
            # (c) Trust flows UP the lattice, never down. Any pointer built over an untrusted —
            #     or unlabelled — ancestor cannot itself claim TRUSTED. Without this, laundering
            #     is one aggregation away: wrap the untrusted value in a `derived` pointer and
            #     declare the result trusted. The join reads each parent's `effective_trust`, so
            #     a parent whose own label was forged is seen through rather than believed.
            if parents and trust_from_parents(parents) is not Trust.TRUSTED:
                unlabelled = sum(1 for p in parents if p.trust is None)
                detail = (
                    f"{unlabelled} of {len(parents)} parent pointer(s) carry no trust label; "
                    "an unlabelled ancestor joins as UNTRUSTED"
                ) if unlabelled else "at least one parent pointer is UNTRUSTED"
                raise ValueError(
                    f"trust laundering REFUSED (kind={self.kind!r}): {detail}. The least "
                    "upper bound over {trusted < untrusted} is UNTRUSTED, so this pointer "
                    "cannot be TRUSTED")
        return self

    @property
    def effective_trust(self) -> Optional[Trust]:
        """The trust label RE-DERIVED from ancestry — read this, not the stored field.

        Equal to ``self.trust`` for every validly constructed pointer that carries a label. It
        differs where the stored label was written by something that skipped validation (and
        then it is the correct one), and where an UNLABELLED pointer sits over untrusted
        ancestry (and then it demotes, which the validator deliberately does not do — it must
        not invent a label on a field the migration left blank). See ``effective_trust()`` at
        module scope.
        """
        return effective_trust(self)

    @model_validator(mode="after")
    def _check_capture_digest_is_an_address(self) -> "SourcePointer":
        """A blank capture binding is worse than none — it looks like proof and is not.

        The *format* is deliberately unconstrained (adapters differ on whether they emit a
        bare hex digest or an ``algo:hex`` address); only emptiness is refused, because an
        empty string would satisfy a truthiness check somewhere downstream.
        """
        if self.capture_digest is not None and not self.capture_digest.strip():
            raise ValueError(
                "capture_digest must be a content address, not an empty string — a blank "
                "digest cannot bind a pointer to any bytes")
        return self


class GroundedValue(_Sealed):
    """A value that knows where it came from. The universal currency of DataBridge.

    ``confidence`` 1.0 = directly copied; <1.0 = inferred (drives P6 confidence routing).

    ``extra="forbid"`` for the same reason as on ``SourcePointer``, and for one more:
    ``trust`` is a read-only PROPERTY here, so without ``forbid`` a call site could pass
    ``trust=Trust.TRUSTED`` beside an untrusted pointer, have it silently dropped, and
    believe it had relabelled the value. Laundering would then need no aggregation at all.
    """

    value: Any
    pointer: SourcePointer
    confidence: float = 1.0

    @property
    def trust(self) -> Optional[Trust]:
        """The trust label of the origin. ``None`` while the D3 migration is in flight.

        Read it, never write it: trust is a property of where the value came from, so it
        lives on the pointer and cannot be overridden on the wrapper.

        Returns the pointer's ``effective_trust`` — re-derived from ancestry — rather than its
        stored field. This is the one read path every downstream consumer already goes through
        (the taint map registers values by it), so putting the backstop here is what makes a
        forged label unable to survive being *used*, not merely unable to be constructed.
        """
        return self.pointer.effective_trust


# ---------------------------------------------------------------------------------------
# Withholding — the third cell state (spine D4)
# ---------------------------------------------------------------------------------------
# "Not present in the source" and "present in the source, not permitted to you" are
# different facts. Collapsing them is a correctness bug in the credibility-critical path,
# and downstream it opens a missingness covert channel in any materialised dataset.


class MissingReason(str, Enum):
    """Why a nullable field is null. These three are NOT interchangeable (D4)."""

    NOT_IN_SOURCE = "not_in_source"           # never present. Genuinely informative to a model.
    GROUNDING_DROPPED = "grounding_dropped"   # extracted, failed the grounding wall. Says
                                              # something about extraction quality, NOT the world.
    POLICY_SUPPRESSED = "policy_suppressed"   # grounded, withheld from THIS principal.
                                              # Missing-not-at-random; must never be modelled
                                              # as ordinary absence.


#: Reason-code tokens that assert something about the withheld value itself. A denial reason of
#: "sensitive value present" defeats the whole guarantee in four words (R-003 finding 4), and it
#: is the natural thing for a policy author to write. Refused on the RESPONSE plane (``Denial``),
#: not on the audit record: "the value looked like an injected instruction" is exactly what an
#: auditor needs to read, and exactly what the requester must not be told.
_EXISTENCE_ASSERTING_TOKENS = frozenset({
    "present", "exists", "existent", "exist", "null", "nonnull", "notnull",
    "hasvalue", "has_value", "populated", "nonempty", "non_empty", "notempty", "not_empty",
    "value", "content", "shaped", "matched", "detected", "sensitive",
})


def _existence_asserting_tokens(reason_code: str) -> list[str]:
    tokens = [t for t in re.split(r"[^a-z0-9]+", reason_code.lower()) if t]
    return sorted({t for t in tokens if t in _EXISTENCE_ASSERTING_TOKENS})


class Denial(_Sealed):
    """The RESPONSE-plane half of a withholding: what the requester is allowed to be told.

    ``WithholdingRecord`` is an *audit* record — it names the principal, the purpose, the policy
    version and the moment. Handing all of that back to the requester alongside the denial makes
    several existence channels available at once (R-003 finding 4): timing is value-sensitive if
    the policy read the value before deciding, and a reason code drawn from the policy's own
    vocabulary is value-sensitive whenever the policy author wrote an honest one.

    So the two planes are separate types. This one carries the four things a UI genuinely needs
    to render *"you asked and policy said no, here is who to ask"* — and its validator refuses a
    reason code that talks about the value. ``decided_at``, ``principal``, ``purpose`` and
    ``policy_version`` are deliberately absent, so they cannot be leaked by forgetting.

    **The honest limit.** This closes the channels that live in the record's SHAPE. It cannot
    close the channel that lives in the policy's *behaviour*: if the decision function reads the
    cell before deciding, then whether a denial is produced at all is value-dependent, and no
    type in this module can see that. That obligation sits with the policy engine (D4a: the PEP
    is at pointer resolution), and the pack-level answer for the object-level version of the same
    problem is spine O1b — a pack may declare withheld and absent byte-identical.
    """

    field_path: str
    policy_id: str
    reason_code: str
    appealable: Optional[bool] = None

    @model_validator(mode="after")
    def _check_the_reason_does_not_describe_the_value(self) -> "Denial":
        leaking = _existence_asserting_tokens(self.reason_code)
        if leaking:
            raise ValueError(
                f"reason_code {self.reason_code!r} asserts something about the withheld value "
                f"({', '.join(leaking)}): a denial that explains itself by describing what it is "
                "hiding has disclosed that there is something to hide. Give the policy a public "
                "reason code ('not_in_grant', 'purpose_not_permitted') and keep the diagnostic "
                "one on the WithholdingRecord, where the auditor - not the requester - reads it")
        return self


class WithholdingRecord(_Sealed):
    """A requested field was DENIED to this principal (spine D4). The AUDIT-plane record.

    Read the negative space, because it is the whole design:

    * **It carries no value.** Obviously.
    * **It carries no ``SourcePointer``.** Withholding is not a citation. A pointer beside
      a withheld field would vouch for something the consumer was never shown, which is the
      precise failure the grounding wall exists to stop, one level down.
    * **It does not assert that a value exists behind the denial.** The source may simply
      hold ``NULL``. Claiming existence would itself be a disclosure — the record says
      *"you asked and policy said no"*, never *"there is something here you may not see"*.
    * **``principal`` is a string, and therefore an assertion by whatever code built the record —
      never proof that a named human attested to anything.** It must not be read as a confirmed
      identity determination; that needs an attestation object, and this is not one (R-003 #5).

    ``extra="forbid"`` is what makes those three properties structural: ``value=`` or
    ``pointer=`` on this model raises at the call site rather than being dropped.

    **This record is not what you return to the requester.** Project it with ``public_denial()``
    first — see ``Denial``. This one is for the ledger and the auditor, and its ``reason_code``
    may say things (why the policy fired) that the requester must not be told.

    **What grounds it.** It carries no ``SourcePointer``, and that is not a hole in the universal
    provenance claim (R-003 finding 5) — a denial is not a disclosure, so a *source* pointer is
    the wrong kind of proof for it. What it must be re-executable from is the DECISION:
    ``policy_id`` + ``policy_version`` (which bundle), ``principal`` + ``purpose`` (which inputs),
    ``decision_id`` (which evaluation event in the ledger) and ``decided_at`` (when). Those five
    are the denial's provenance. Read the module docstring for the restated claim.

    Per-cell, not per-column, because row-level policy is the common case.
    """

    field_path: str
    policy_id: str            # the policy OBJECT that decided; policies have identity
    reason_code: str          # the machine-readable denial reason, from the policy's vocabulary
    principal: str            # WHO was denied - a denial is principal-specific, never global.
                              # A STRING, and therefore an assertion by whatever code built this
                              # record, never proof that a named human attested to anything. It
                              # must never be read as a confirmed identity determination - that
                              # is the composition root's job and it requires an attestation
                              # object, not a non-blank string (R-003 finding 5).

    # Optional today because the objects that would populate them land in Phase 3
    # (Principal, the compiled policy bundle). Declared here so the shape does not change
    # under stored records later.
    policy_version: Optional[str] = None   # content hash of the policy bundle in force
    purpose: Optional[str] = None          # the purpose the request carried (D5)
    decided_at: Optional[datetime] = None
    decision_id: Optional[str] = None      # the policy-EVALUATION event this denial came out of,
                                           # so the decision can be re-executed rather than
                                           # merely described. Optional only until the PEP at
                                           # pointer resolution (D4a) exists to mint one.
    appealable: Optional[bool] = None      # could a different principal/purpose see it?
                                           # This is what lets a UI say "ask the data owner"
                                           # rather than "nobody can see this".

    @model_validator(mode="after")
    def _check_identifiers_are_answerable(self) -> "WithholdingRecord":
        blank = [name for name in ("field_path", "policy_id", "reason_code", "principal")
                 if not getattr(self, name).strip()]
        if blank:
            raise ValueError(
                f"WithholdingRecord requires non-blank {', '.join(blank)}: a denial nobody "
                "can attribute to a policy and a principal is not an audit record")
        return self

    def public_denial(self) -> Denial:
        """The projection safe to hand back to the requester. Raises on a leaky reason code.

        Everything principal-, purpose-, version- and time-specific is dropped, so two denials
        that differ only in audit detail project to equal ``Denial`` objects - which is the
        observational-identity property finding 4 asked for, for the part of it a type can hold.
        """
        return Denial(field_path=self.field_path, policy_id=self.policy_id,
                      reason_code=self.reason_code, appealable=self.appealable)


class Withheld(_Sealed):
    """Cell state: the field was requested and access was denied (D4).

    Carries the audit record. Anything rendering a cell to a principal projects it through
    ``record.public_denial()`` rather than serialising the record itself.
    """

    record: WithholdingRecord

    def public_denial(self) -> Denial:
        return self.record.public_denial()


class Absent(_Sealed):
    """Cell state: not in the source. Names the question that was asked and left unanswered.

    ``snapshot`` is the absence's provenance (R-003 finding 5): "this field is not in the source"
    is a claim ABOUT a source, and a claim about a source that does not say *which* source, as of
    *when*, is not re-executable. With it, the claim is checkable - re-read that snapshot and the
    field is still not there. Optional only because the callers that predate it have no snapshot
    to hand; a cell destined for the ledger should carry one.

    It is not a ``SourcePointer``, and deliberately so: a pointer locates bytes, and the whole
    content of this state is that there are none to locate.
    """

    field_path: str
    snapshot: Optional[SnapshotIdentity] = None



# ---------------------------------------------------------------------------------------
# Transformation — the fourth cell state (spine O1b)
# ---------------------------------------------------------------------------------------
# "Denied to you" and "disclosed to you, but not verbatim" are different facts, and the gap
# between them was found twice independently: the government pack could not honestly describe a
# masked value, and the finance pack's `egress_ceiling` could not express "may read, but only in
# transformed form" - so two roles reached nothing at all, refusal being the only safe thing the
# vocabulary could say. Over-refusal and mis-description are one missing concept.
#
# A masked or generalised value is none of the three states above. It cannot carry a verbatim
# pointer (the emitted bytes are not the cited bytes); it was not absent; and calling it
# `Withheld` throws away the fact that a PARTIAL DISCLOSURE occurred.


#: The minimal shape of a replayable transformation call. Deliberately LOOSER than the parser that
#: actually replays it (``databridge_adapter.grounding.parse_derivation``), and loose on purpose:
#: core is the dependency root and must not own the derivation grammar, so it checks only the
#: prefix that makes the string a CALL rather than prose. Anything that parser accepts, this
#: accepts; the reverse is not promised, and that asymmetry is the safe direction - core never
#: refuses a transformation the replayer would have run.
_TRANSFORMATION_CALL = re.compile(r"^\s*[a-z][a-z0-9_]*@v\d+\s*\(")


class PartialDisclosure(_Sealed):
    """The RESPONSE-plane half of a transformation: what the requester is allowed to be told.

    Exactly parallel to ``Denial``, and for the same reason (R-003 finding 4): ``TransformRecord``
    is an *audit* record naming the principal, the purpose, the policy version and the moment, and
    handing that back opens channels that have nothing to do with the value. Dropped here, so they
    cannot be leaked by forgetting - this type has no field they could be re-added to.

    **Where this deliberately differs from ``Denial``, and why.** ``Denial`` refuses a reason code
    that describes the withheld value, because a denial that explains itself by describing what it
    is hiding has disclosed that there is something to hide. That argument does not transfer: a
    partial disclosure has *already* disclosed that the cell exists and has handed over a function
    of its value. There is admittedly something here - by design, since the requester is holding
    part of it. Running the existence-token filter over this type would refuse honest codes
    (``generalised_quasi_identifier``, ``masked_sensitive_column``) while protecting nothing that
    ``value`` itself does not already give away. The asymmetry is deliberate and is asserted in
    ``test_transformed_disclosure.py`` so it cannot later be read as an oversight.

    What is NOT relaxed: ``value`` here is the *transformed* value, never the underlying one, and
    the underlying locator is not on this plane at all - it stays on the ``Transformed`` cell,
    where reaching it goes through the PEP at pointer resolution (D4a).
    """

    field_path: str
    policy_id: str
    transformation: str
    value: Any
    reason_code: Optional[str] = None
    appealable: Optional[bool] = None      # could a different principal/purpose see it verbatim?


class TransformRecord(_Sealed):
    """A requested field was disclosed **only in transformed form** (spine O1b). AUDIT plane.

    Parallel to ``WithholdingRecord`` in every structural respect, because a narrowing is a policy
    decision exactly as a denial is, and an unattributable narrowing is not an audit record either.
    Read the negative space here too:

    * **It carries no underlying value.** ``extra="forbid"`` makes that structural rather than
      conventional - ``raw=`` or ``underlying_value=`` raises at the call site.
    * **It carries a ``transformation``, not a description of one.** The canonical form is
      ``<name>@v<n>(<k>=<v>,...)`` - a call into the versioned, replayable derivation registry.
      Free prose is refused for the reason a ``derived`` pointer refuses it: a transformation
      nobody can replay is a claim, and the whole content of this state is that a *specific,
      re-runnable* function stands between the source bytes and the emitted ones.
    * **``principal`` is a string**, therefore an assertion by whatever code built the record, and
      never proof that a named human attested to anything (the same note as
      ``WithholdingRecord.principal``, and it is not an identity determination).

    **What grounds it.** The disclosed value is grounded in a ``derived`` ``SourcePointer`` - that
    lives on the ``Transformed`` cell, not here. What THIS record grounds is the *decision*: which
    policy (``policy_id`` + ``policy_version``), over which inputs (``principal`` + ``purpose``),
    in which evaluation event (``decision_id``), when (``decided_at``). The same five as a denial,
    because it is the same kind of fact.

    **Not what you return to the requester.** Project it with ``public_disclosure()`` first.
    """

    field_path: str
    policy_id: str            # the policy OBJECT that decided to narrow rather than deny
    transformation: str       # the replayable rule, e.g. "bucket@v1(width=10000)"
    principal: str            # WHO the narrowed disclosure was made to. A STRING - see above.
    reason_code: Optional[str] = None      # why the narrowing, from the policy's vocabulary

    # Optional today for the same reason as on WithholdingRecord: the objects that populate them
    # (Principal, the compiled policy bundle, the PEP at pointer resolution) land in Phase 3.
    # Declared now so the shape does not change under stored records later.
    policy_version: Optional[str] = None
    purpose: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_id: Optional[str] = None
    appealable: Optional[bool] = None

    @model_validator(mode="after")
    def _check_identifiers_are_answerable(self) -> "TransformRecord":
        blank = [name for name in ("field_path", "policy_id", "transformation", "principal")
                 if not getattr(self, name).strip()]
        if blank:
            raise ValueError(
                f"TransformRecord requires non-blank {', '.join(blank)}: a narrowing nobody can "
                "attribute to a policy, a principal and a named transformation is not an audit "
                "record - it is an unexplained value with extra steps")
        return self

    @model_validator(mode="after")
    def _check_the_transformation_is_a_replayable_call(self) -> "TransformRecord":
        if not _TRANSFORMATION_CALL.match(self.transformation):
            raise ValueError(
                f"transformation {self.transformation!r} is not a replayable call. The canonical "
                "form is '<name>@v<n>(<k>=<v>,...)', optionally followed by a prose note. Prose "
                "alone is refused: a partial disclosure whose transformation cannot be re-run is "
                "indistinguishable from a fabricated value, and the point of this state is that "
                "the emitted bytes are a KNOWN function of the cited ones")
        return self

    def public_disclosure(self, value: Any) -> PartialDisclosure:
        """The projection safe to hand back to the requester, carrying the transformed value.

        Everything principal-, purpose-, version- and time-specific is dropped, so two narrowings
        that differ only in audit detail project to equal objects - the observational-identity
        property, for the part of it a type can hold.

        ``value`` is passed in rather than held on the record because the record is the DECISION,
        and a decision does not hold values; the value lives on the ``Transformed`` cell beside its
        pointer. ``Transformed.public_disclosure()`` is the call you actually want.
        """
        return PartialDisclosure(
            field_path=self.field_path, policy_id=self.policy_id,
            transformation=self.transformation, value=value,
            reason_code=self.reason_code, appealable=self.appealable)


class Transformed(_Sealed):
    """Cell state: the field was disclosed, but **not verbatim** (spine O1b, closed 2026-08-16).

    The fourth state. A masked, redacted, generalised, bucketed, rounded or top-coded value - a
    partial disclosure, which is a first-class outcome of a policy decision and not a degenerate
    form of either of its neighbours.

    **The shape, and the alternative that was rejected.** The spine left it open between (a) a
    distinct type carrying the policy reference and the transformation, and (b) a ``GroundedValue``
    whose pointer is ``derived``. (b) is rejected *as the discriminator*, because ``derived`` is
    already the honest pointer kind for many FULL disclosures - unit conversions, normalisations,
    the 17.1 aggregation compression, the registered ``identity`` rule. Overloading it to also mean
    "partial" would make the two indistinguishable at exactly the place downstream code looks, and
    silently: every ``isinstance(cell, GroundedValue)`` in the tree would read a mask as a full
    disclosure. It also has nowhere to put the policy reference - a ``SourcePointer`` says where
    bytes came from, never who permitted them, and a policy id inside a citation is a category
    error.

    What (b) had RIGHT is kept: the ``derived`` pointer genuinely is the honest citation for
    transformed bytes (4a - if the emitted bytes differ from the cited bytes, the kind is
    ``derived`` or the value is ungrounded), it replays, and it carries the trust ancestry. So this
    type WRAPS such a ``GroundedValue`` rather than replacing it. The discriminator is the wrapper;
    the citation is the pointer. Each half does the job it is good at.

    **The five questions the spine required it to answer**, and where each is answered:

    ===========================================  =============================================
    question                                     field
    ===========================================  =============================================
    what was disclosed                           ``disclosed.value``, with its own citation
    what transformation produced it              ``record.transformation``, checked equal to
                                                 ``disclosed.pointer.derivation``
    under what policy                            ``record.policy_id`` / ``policy_version`` /
                                                 ``decision_id``
    what the underlying cell was                 ``underlying`` - a locator, resolvable only
                                                 through the PEP (D4a)
    that this was PARTIAL, not full              the type itself
    ===========================================  =============================================

    **Trust is not laundered by a transformation** (D3), and the mechanism is worth naming because
    it is reuse rather than new machinery: requiring ``underlying`` to appear among
    ``disclosed.pointer``'s parents is what puts the transformation inside the existing lattice.
    ``SourcePointer._check_trust_is_consistent`` then refuses a TRUSTED ``derived`` pointer over an
    untrusted or unlabelled parent, and ``effective_trust`` re-derives the same demotion at read
    time even if the stored label was forged. A mask over attacker-writable bytes stays UNTRUSTED,
    which is correct: redacting an injected instruction does not make the remainder safe to steer
    on.

    **It does not assert more existence than it already has.** Holding ``underlying`` is not an
    extra disclosure - a ``SourcePointer`` carries no value, and the transformed value in hand
    already reveals that a cell was there. That is the whole difference from ``Withheld``, which
    must never imply a value exists behind it. Reaching the underlying bytes goes through
    ``resolve()``, where the PEP sits (D4a); this type is a locator, not an entitlement.

    **Two honest limits, stated rather than implied:**

    * A transformation of ``identity`` would make this a full disclosure wearing a partial label.
      Core does not special-case rule names - that would be exactly the hardcoded per-rule
      branching the repo refuses - so what stops it is that the record says so plainly: a policy
      that authorised ``identity@v1`` and called the result narrowed has a misconfiguration
      visible in its own audit trail, rather than a lie in the type.
    * ``underlying`` is normally a ``table_cell``. If a caller passes a ``derived`` pointer whose
      ``predicate`` embeds a literal, that predicate is as readable as any other pointer field.
      That is a pre-existing property of ``derived`` pointers, not something this state
      introduces, and it is not fixed here.
    """

    #: The value actually handed over, with its own citation. The pointer must be ``derived``:
    #: a verbatim kind here would claim the emitted bytes are the source bytes, which is the
    #: precise lie this state exists to stop telling.
    disclosed: GroundedValue

    #: Where the untransformed cell lives. A locator, never a value. Must appear among the
    #: disclosed pointer's parents, which is what makes the trust join apply to it.
    underlying: SourcePointer

    #: The policy decision that narrowed rather than denied. AUDIT plane.
    record: TransformRecord

    @model_validator(mode="after")
    def _check_the_citation_admits_it_is_not_verbatim(self) -> "Transformed":
        ptr = self.disclosed.pointer
        if ptr.kind != "derived":
            raise ValueError(
                f"a transformed disclosure must be cited with a `derived` pointer, got "
                f"{ptr.kind!r}. The emitted bytes are not the cited bytes, so a verbatim kind "
                "would vouch for a value the source never held (4a: if what is emitted differs "
                "from what is cited, the kind is `derived` or the value is ungrounded)")
        return self

    @model_validator(mode="after")
    def _check_the_underlying_cell_is_a_declared_parent(self) -> "Transformed":
        parents = _parents_of(self.disclosed.pointer)
        if not any(p is self.underlying or p == self.underlying for p in parents):
            raise ValueError(
                "the underlying pointer must appear among the disclosed pointer's parents "
                "(`derived_from`/`facts`/`child`). Two things break without it: the "
                "transformation has nothing to replay over, so the disclosed value is not "
                "established by anything cited; and the trust join has no ancestor to demote "
                "from, which would make a transformation a laundering step - wrap an untrusted "
                "cell, declare the result trusted, steer on it")
        return self

    @model_validator(mode="after")
    def _check_the_policy_authorised_the_transformation_that_ran(self) -> "Transformed":
        """The record is what policy DECIDED; the pointer is how the value is CITED. If the two
        disagree, the emitted value was not produced by the transformation that was authorised -
        the one bug in this state worth catching at construction."""
        cited = (self.disclosed.pointer.derivation or "").strip()
        authorised = self.record.transformation.strip()
        if cited != authorised:
            raise ValueError(
                f"the disclosed pointer replays {cited!r} but policy authorised {authorised!r}. "
                "A partial disclosure is honest only if the function standing between the source "
                "bytes and the emitted ones is the one the policy permitted. Core compares the "
                "two strings verbatim and parses neither - copy the authorised call into both, "
                "prose note included")
        return self

    @model_validator(mode="after")
    def _check_something_was_actually_disclosed(self) -> "Transformed":
        """A transformation that discloses nothing is a DENIAL, and must be encoded as one.

        Not pedantry - a security rule. ``Withheld`` routes through ``Denial``, whose validator
        refuses a reason code that describes the withheld value. If a null-valued ``Transformed``
        were legal, that hygiene could be walked around by calling the denial a transformation:
        ``PartialDisclosure`` deliberately does not run the token filter, and its justification -
        "the value is already partly disclosed" - is false when there is no value. Suppression to
        null is a denial; encode it as ``Withheld`` so it stays attributable and appealable.
        """
        if self.disclosed.value is None:
            raise ValueError(
                "a transformed disclosure that discloses None is a denial: nothing reached the "
                "requester, so nothing was partially disclosed. Use Withheld, which carries an "
                "attributable, appealable WithholdingRecord and projects through Denial's "
                "reason-code hygiene - this state must not become a route around it")
        return self

    @property
    def trust(self) -> Optional[Trust]:
        """The trust of the ORIGIN, re-derived through the transformation's ancestry.

        A transformation is not a promotion. This reads the disclosed pointer's
        ``effective_trust``, which joins over ``underlying`` because the validator above requires
        it to be a parent - so a mask over an untrusted cell reads back ``UNTRUSTED``.
        """
        return self.disclosed.trust

    def public_disclosure(self) -> PartialDisclosure:
        """What the requester may be handed: the transformed value and its non-leaky attribution."""
        return self.record.public_disclosure(self.disclosed.value)


#: A cell in any grounded result set. Exactly one of FOUR states, never a degenerate
#: ``GroundedValue`` - ``pointer`` is non-Optional, so ``GroundedValue(value=None,
#: pointer=None)`` cannot be constructed, and should not be.
#:
#: Two of the four DISCLOSE, and both of those carry a ``SourcePointer``: ``GroundedValue``
#: verbatim, ``Transformed`` under a named, replayable function (its citation is a ``derived``
#: pointer, which is how it says out loud that the emitted bytes are not the cited bytes). The
#: other two are non-answers and are grounded in the thing that actually produced them - a policy
#: decision (``Withheld.record``) or a snapshot the question was asked of (``Absent.snapshot``).
#: See the module docstring for the claim stated precisely; the version that said "every value
#: carries a pointer" was reading the two non-answers as counter-examples when they are not
#: values at all (R-003 finding 5).
#:
#: **Order matters if you pattern-match.** ``Transformed`` is deliberately NOT a ``GroundedValue``
#: subclass, so ``isinstance(cell, GroundedValue)`` is false for it and a mask cannot be read as a
#: full disclosure by anything that already existed. Code that wants "any disclosure" must say
#: ``isinstance(cell, (GroundedValue, Transformed))`` and thereby acknowledge the difference.
#: Consumers with their own narrower union (``databridge_adapter.results.Cell``) fail closed on a
#: ``Transformed`` until they opt in, which is the right default.
Cell = Union[GroundedValue, Transformed, Withheld, Absent]

#: The states that put a value in the caller's hands. Neither is a non-answer; exactly one of them
#: is verbatim. Named so that a consumer asking "did I get anything?" cannot answer it with a bare
#: ``isinstance(cell, GroundedValue)``, which silently drops every partial disclosure.
DISCLOSING_CELL_TYPES: tuple[type, ...] = (GroundedValue, Transformed)


def assert_withheld_disjoint(
    attrs: dict[str, Any],
    attr_sources: dict[str, SourcePointer],
    attr_withheld: dict[str, WithholdingRecord],
    requested: Optional[Iterable[str]] = None,
    attr_transformed: Optional[dict[str, TransformRecord]] = None,
) -> None:
    """SYSTEM_DESIGN §1.6 rules 4 and 5, in one place so every consumer enforces the same ones.

    Lives in core rather than in the graph schema because the adapter layer, the graph and
    any record type all carry the same parallel-dict shape and must agree on its meaning.

    * **rule 4** - a key may not appear in both ``attr_sources`` and ``attr_withheld``.
      Withholding means the value did not reach this consumer; a citation beside it would
      vouch for something the consumer was not shown.
    * **rule 5** - every key in ``attr_withheld`` must exist in ``attrs`` with value
      ``None``. Otherwise policy has invented a field the record never had.

    ``requested`` is **the requested SCHEMA keys**, and supplying it is what makes rule 5 safe
    rather than dangerous (R-003 finding 4). Rule 5 requires a withheld key to be present in
    ``attrs``; if the caller built ``attrs`` from *what the source happened to hold*, then the
    key set itself encodes source presence, and the appearance of a ``Withheld`` proves the field
    exists - the exact disclosure ``WithholdingRecord`` refuses to make in its own shape. When
    ``requested`` is given this function requires ``attrs`` to contain **every** requested key
    regardless of source presence (missing ones as ``None``), which is what makes the two cases
    indistinguishable, and refuses a withheld key that was never requested.

    Omitting ``requested`` runs the weaker legacy check and leaves that obligation with the
    caller. It is optional only because the callers predate it; pass it.

    ``attr_transformed`` extends the same rules to the fourth cell state (spine O1b), in the
    parallel-dict shape. A transformed key is a **disclosure**, so the rules invert relative to a
    withheld one and the inversions are the point:

    * it may **not** also be withheld - denied and partially disclosed at once is not a decision;
    * it **must** carry a source pointer, and that pointer's kind must be ``derived``. This is the
      mirror of rule 4: withholding is not a citation, but a partial disclosure *is* one, of
      bytes the source never held. A verbatim kind here would claim the emitted value is the
      stored value;
    * the pointer's ``derivation`` must equal the record's ``transformation``, so a masked column
      cannot cite one function while its audit trail names another;
    * it **must** hold a non-``None`` value in ``attrs`` - the exact mirror of rule 5. A null
      "transformed" cell is a denial that has escaped ``Denial``'s reason-code hygiene;
    * when ``requested`` is given, it must have been requested. A narrowed value for a field
      nobody asked about announces the field just as a denial for one does.

    Callers additionally owe rule 6: withheld keys are **removed** from the caller's unresolved
    list - and so are transformed keys, which are resolved, merely not verbatim. That one cannot
    be checked here because that list is derived per consumer.
    """
    # Materialised ONCE. ``requested`` is typed as an Iterable, so a caller may legitimately pass
    # a generator or a ``dict.keys()`` view; consuming it twice (the withheld rules below, then
    # the transformed ones) would silently see an empty set the second time and report every
    # transformed key as unrequested. A false positive on a disclosure rule is a real bug.
    asked = None if requested is None else set(requested)

    # Checked FIRST, ahead of rule 4, because a key that is both withheld and transformed also
    # trips rule 4 (a transformed key legitimately carries a pointer) and would be reported as
    # "withholding is not a citation" — true, but the wrong diagnosis for the wrong bug.
    if attr_transformed:
        contradictory = sorted(set(attr_transformed) & set(attr_withheld))
        if contradictory:
            raise ValueError(
                f"keys both withheld and transformed: {', '.join(contradictory)}. Policy either "
                "denied the field or narrowed it; claiming both means the audit trail cannot say "
                "whether anything reached the requester")

    both = sorted(set(attr_sources) & set(attr_withheld))
    if both:
        raise ValueError(
            f"withheld keys carry a source pointer: {', '.join(both)}. Withholding is not a "
            "citation - the value did not reach this consumer, so nothing may vouch for it")
    missing = sorted(k for k in attr_withheld if k not in attrs)
    if missing:
        raise ValueError(
            f"withheld keys absent from attrs: {', '.join(missing)}. A withholding record "
            "may not invent a field the record never had")
    non_null = sorted(k for k in attr_withheld if attrs.get(k) is not None)
    if non_null:
        raise ValueError(
            f"withheld keys hold a non-None value: {', '.join(non_null)}. A withheld cell is "
            "None plus a policy reference; a value beside the denial defeats the denial")
    if asked is not None:
        absent_keys = sorted(asked - set(attrs))
        if absent_keys:
            raise ValueError(
                f"requested keys missing from attrs: {', '.join(absent_keys)}. Every requested "
                "schema key must be present (as None if the source held nothing), or the key "
                "set leaks which fields the source actually has - and a Withheld among keys "
                "that only appear when the source has them proves the value exists")
        unrequested = sorted(set(attr_withheld) - asked)
        if unrequested:
            raise ValueError(
                f"withheld keys that were never requested: {', '.join(unrequested)}. A denial "
                "for a field nobody asked about announces that the field is there")

    if attr_transformed:
        uncited = sorted(k for k in attr_transformed if k not in attr_sources)
        if uncited:
            raise ValueError(
                f"transformed keys carry no source pointer: {', '.join(uncited)}. Unlike a "
                "withholding, a partial disclosure IS a citation - of bytes the source never "
                "held - so it must cite the `derived` pointer that replays the transformation")
        verbatim = sorted(k for k in attr_transformed if attr_sources[k].kind != "derived")
        if verbatim:
            raise ValueError(
                f"transformed keys cited with a verbatim pointer kind: {', '.join(verbatim)}. "
                "The emitted bytes are not the cited bytes, so the kind is `derived` or the "
                "value is ungrounded")
        mismatched = sorted(
            k for k in attr_transformed
            if (attr_sources[k].derivation or "").strip()
            != attr_transformed[k].transformation.strip())
        if mismatched:
            raise ValueError(
                f"transformed keys cite a different function than policy authorised: "
                f"{', '.join(mismatched)}. The pointer's `derivation` and the record's "
                "`transformation` must be the same call, or the audit trail describes a "
                "transformation that did not run")
        absent_from_attrs = sorted(k for k in attr_transformed if k not in attrs)
        if absent_from_attrs:
            raise ValueError(
                f"transformed keys absent from attrs: {', '.join(absent_from_attrs)}. A partial "
                "disclosure with nothing disclosed is not a partial disclosure")
        nulled = sorted(k for k in attr_transformed if attrs.get(k) is None)
        if nulled:
            raise ValueError(
                f"transformed keys hold None: {', '.join(nulled)}. Nothing reached the requester, "
                "so this is a denial - encode it as a WithholdingRecord, which is attributable "
                "and appealable and whose public projection refuses a value-describing reason "
                "code. A null 'transformation' is a route around that hygiene")
        if asked is not None:
            never_asked = sorted(set(attr_transformed) - asked)
            if never_asked:
                raise ValueError(
                    f"transformed keys that were never requested: {', '.join(never_asked)}. A "
                    "narrowed value for a field nobody asked about announces that the field is "
                    "there, exactly as a denial for one does")


#: The rules are no longer only about withholding (spine O1b added a fourth state). Kept under
#: both names because callers outside this package import the old one; same function, and the
#: precedent is ``trust_lub`` / ``trust_from_parents`` (R-003 finding 9).
assert_cell_states_disjoint = assert_withheld_disjoint


# Resolve the self-referential forward refs (child / facts / derived_from).
SourcePointer.model_rebuild()
GroundedValue.model_rebuild()
