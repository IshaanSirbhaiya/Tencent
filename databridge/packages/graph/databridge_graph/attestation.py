"""The attestation — what turns a *claim* into a *determination*, and the wall that enforces it.

`meta.py` has said since it was written that `RESOLVED_TO` is "written only on the far side of a
named human's attestation". Until 2026-08-16 that sentence was **prose**: `ATTESTED_META_KINDS`
was defined and nothing consumed it, so any caller could construct an edge bound to `RESOLVED_TO`
— an identity determination, the one thing this system refuses to automate — with an ordinary
`SourcePointer`, no named human, no attestation record, no grant, and no check that the
destination was a declared subject at all.

This module is that sentence made into a validator.

## What an attestation is, and what it is not

**A `SourcePointer` is not an attestation.** A pointer says "this value was read from there". An
attestation says "*I*, named, at this time, having performed *this act*, selected *this* candidate
as *this* subject." Those are different claims with different authors, and conflating them is
exactly how an automated match acquires the authority of a human decision.

So an `Attestation` carries five things a pointer cannot:

1. **who** — `attestor`, a named human. Not a role, not a service account, not "system".
2. **when** — `attested_at`, timezone-aware, because "when" without an offset is not a time.
3. **what act was performed** — `mode`, from the closed set in `packs.ATTESTATION_MODES`, and it
   must MATCH what the subject's pack declares. A pack that requires `in_person` is not satisfied
   by a `verified_channel` attestation; the mode is the guarantee, not a label on it.
4. **what was checked** — `identifier_checked`, one of the identifiers the pack declares for that
   subject kind. "I confirmed them" is not checkable; naming the declared identifier that was
   held up and compared is.
5. **its own grounding** — `sources`. The attestation is itself an emitted record, and P4 does not
   exempt records about humans. Where the attestation was captured (which staff terminal, which
   ledger row) is part of what makes it auditable rather than assertable.

Plus `grant_type`, the capability this attestation mints (spine D11) — stated by the caller and
**checked against the pack's declaration**, so an attestation cannot name a grant its subject kind
does not confer.

## Where it is enforced

On the model, in `GraphEdge`'s after-validator, so there is no alternate route: the generic
constructor, `model_validate`, and the store's read-back path all pass through the same check.
`record_determination()` below is the ergonomic front door, not the only door — a private write
API with a public constructor beside it is a lock on one of two doors.

Two directions are enforced, not one:

* an edge bound to `RESOLVED_TO` **requires** a valid attestation, and
* an edge bound to anything else **refuses** one. That second rule is what stops a claim edge
  (`ABOUT` — "this document says it is about that person") from being quietly promoted by
  attaching an attestation to it. A claim stays a claim: two candidates yield two `ABOUT` edges
  and neither is an answer.

## An attestation authorises ONE act, not a subject kind

Review (2026-08-16, attestation finding 1, Critical) found the sentence above — "selected *this*
candidate as *this* subject" — was not encoded anywhere. Enforcement compared the attestation's
`subject_kind` against the destination's id prefix and nothing else, so one valid attestation for
one person was a **bearer token for every node of that kind**: the same record could be attached to
`doc:A → person:alice` and `doc:B → person:bob` and any number of others, and each of those is a
different human decision that nobody made.

So the selection is now part of the record. `candidate_id`, `subject_id` and `edge_kind` are
required fields, and `check_binds` refuses an attestation whose triple does not match the edge it
is attached to. `attestation_id` is derived from the content, so the store can tell one attestation
being persisted once from the same one being replayed onto a second edge.

## The wall must not trust the object it is handed

Attestation findings 2 and 3, both Critical, are the same shape: `model_construct()` and
`model_copy(update=...)` skip every validator in Pydantic, so an `Attestation` with an empty
`attestor`, a naive `attested_at` and no `sources` could be built and would satisfy enforcement —
and the store's supposedly independent second check called only `check_against()`, which never
re-examined those fields.

Two changes, and the second is the one that matters:

1. `revalidate_instances="always"` on the model, so an `Attestation` handed to `GraphEdge` is
   re-validated rather than accepted as already-valid;
2. **`enforce_attestation` reconstructs the attestation from its field values before trusting it**
   (`revalidated`). A forged object is rebuilt through the ordinary validators, and the rebuild is
   what is checked. `GraphStore.add_edge` calls the same wall, so a forged edge cannot be
   persisted even where it can be constructed in memory — which is the honest position: this
   codebase cannot stop `model_construct`, and the store is where it stops mattering.

## The honest limits that remain

* `attestor` is a **name**, and this module cannot verify that a name belongs to a human who was
  actually present. `attestor_principal` exists for the authenticated session reference that a
  gateway must supply; nothing here can check it either. The control that makes the name mean
  something is organisational — the staff login that produced it and the ledger row it lands in.
  Attestation finding 6 is therefore acknowledged rather than closed, and the test that records it
  is named for exactly that.
* `model_construct` still constructs, and always will. What changed is that nothing downstream
  believes the result: the wall rebuilds it, and the store refuses the rebuild if it fails.
* The destination's *kind* is read from its node id prefix (`f"{kind}:{slug}"`, `schema.node_id`),
  because an edge holds ids and not nodes. A determination whose destination id does not follow
  that convention is refused rather than assumed — see `subject_kind_of_node_id`. The STORE goes
  further and checks the destination node actually exists with that kind (finding 8), because only
  the store can.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from databridge_core import SourcePointer, Trust, trust_lub

from .meta import ATTESTED_META_KINDS, EdgeMetaKind
from .packs import ATTESTATION_MODES, Vocabulary, active_vocabulary


#: Tokens `attestor` is refused for matching, case-insensitively, as a whole word. Not identity
#: verification — this module has always said it cannot do that (see "The honest limits that
#: remain" above) — but the docstring for `attestor` has always promised "not a role, not a
#: service account, not 'system'", and until review R-012 (2026-08-16, Critical, finding 3)
#: nothing enforced that promise: `attestor="AI"` or `attestor="system"` satisfied the two-character
#: minimum and nothing else. This is a floor, not a ceiling: it catches the literal, careless case
#: of an automated caller typing its own name in the field meant for a human's, and it catches
#: nothing a caller willing to type two fake words could not still get past. The organisational
#: control — the staff login that produced the name and the ledger row it lands in — remains the
#: control that actually matters.
_NON_HUMAN_ATTESTOR_WORDS = frozenset({
    "system", "ai", "bot", "robot", "agent", "automated", "automation", "assistant",
    "service", "unknown", "n/a", "na", "none", "null", "test", "admin", "root", "anonymous",
})


class AttestationError(ValueError):
    """An identity determination was attempted without a valid human attestation.

    `ValueError` so that pydantic surfaces it as a `ValidationError` from the model validator,
    and so callers that already guard construction with `except ValueError` keep working.
    """


#: The separator in a node id (`schema.node_id` builds `f"{kind}:{slug}"`). Declared here rather
#: than imported to keep `schema` → `attestation` a one-way dependency.
_ID_SEP = ":"


def kind_of_node_id(node_id: str) -> Optional[str]:
    """The kind prefix of a node id, or ``None`` if the id does not carry one.

    Domain-free: it reads the id convention, never a vocabulary member.
    """
    if not isinstance(node_id, str) or _ID_SEP not in node_id:
        return None
    prefix = node_id.split(_ID_SEP, 1)[0].strip()
    return prefix or None


def subject_kind_of_node_id(node_id: str, vocab: Optional[Vocabulary] = None) -> Optional[str]:
    """The node id's kind **if the enabled packs declare it a subject** (spine D4b), else ``None``.

    ``None`` covers three different failures on purpose — no prefix, an unknown kind, and a known
    kind that is not a subject — because all three mean the same thing to the caller: *this
    destination has not been shown to be a subject*, and a determination may not be recorded
    against it. Distinguishing them would only make the refusal message longer; the message names
    the id and the declared subjects instead.
    """
    v = vocab if vocab is not None else active_vocabulary()
    kind = kind_of_node_id(node_id)
    return kind if kind is not None and v.is_subject(kind) else None


class Attestation(BaseModel):
    """A named human's record of selecting one candidate as one subject (spine D4b, D12).

    Frozen and ``extra="forbid"``: an attestation that silently absorbs a field the model does
    not declare is an attestation nobody can read back the same way twice.
    """

    # `revalidate_instances="always"`: an already-constructed Attestation handed to another model
    # is normally taken on trust. It must not be — `model_construct` and `model_copy(update=...)`
    # both produce instances that never saw a validator (attestation finding 2).
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    attestor: str
    """The named human who attested. Not a role, not a service account."""

    mode: str
    """The act actually performed — a member of `packs.ATTESTATION_MODES`, and it must equal what
    the subject's pack declares. Closed set: a pack inventing its own mode would be inventing its
    own identity guarantee, and a caller inventing one would be describing an act nobody defined."""

    attested_at: datetime
    """When. Timezone-aware, always."""

    subject_kind: str
    """The kind attested to. Checked against the destination node's id prefix and against the
    enabled packs' `subject_kinds`."""

    identifier_checked: str
    """WHICH of the subject kind's declared identifiers the human actually checked. Must be one
    the pack lists in its `subject_kinds[..].identifiers` — "I confirmed them" is not checkable,
    and the engine never names the identifiers itself."""

    grant_type: str
    """The capability this attestation mints (spine D11). Checked against the pack's declaration,
    so an attestation cannot name a grant its subject kind does not confer."""

    sources: tuple[SourcePointer, ...]
    """The attestation's OWN grounding (P4). Required and non-empty: a record about a human
    decision is still an emitted record."""

    candidate_id: str
    """The node id of the artefact whose subject was decided — the SOURCE end of the determination
    edge. Required, and checked against the edge (`check_binds`)."""

    subject_id: str
    """The node id of the subject that was selected — the DESTINATION end. Required.

    With `candidate_id` and `edge_kind` this is what turns the attestation from a token for a
    whole subject kind into a record of one decision (attestation finding 1)."""

    edge_kind: str
    """The determination edge kind this attestation was made for. An attestation made to record
    one kind of determination does not carry to another."""

    attestor_principal: str = ""
    """The authenticated principal the name came from — a staff session id, a signed token
    reference. Optional HERE because this module cannot verify one either; a deployment whose
    gateway authenticates its staff should populate it, and the value of writing it down is that
    an attestation without one is visibly weaker rather than indistinguishable."""

    attestor_role: str = ""
    """Optional — the role the human held. Never a substitute for the name."""

    statement: str = ""
    """Optional free text: what the human recorded at the moment of attesting."""

    @field_validator("attestor")
    @classmethod
    def _must_name_a_human(cls, v: str) -> str:
        name = (v or "").strip()
        words = name.split()
        if len(name) < 2 or len(words) < 2:
            raise AttestationError(
                "Attestation REFUSED: `attestor` must name the human who attested — a first and "
                "last name, not an initial or a single token. An identity determination with no "
                "named author is an automated determination wearing a record "
                "(CLAUDE.md §Hard constraints 1)")
        # Review R-012 (2026-08-16, Critical, finding 3): the length check alone let
        # `attestor="AI"` or `attestor="system"` through — this module's own docstring says
        # `attestor` is "not a role, not a service account, not 'system'"; nothing checked that.
        flagged = [w for w in words if w.strip(".,").casefold() in _NON_HUMAN_ATTESTOR_WORDS]
        if flagged:
            raise AttestationError(
                f"Attestation REFUSED: `attestor` {name!r} reads as a role or an automated caller "
                f"({flagged}), not a named human. This is a floor, not identity verification — "
                f"see 'The honest limits that remain' above — but the module's own promise that "
                f"an attestor is 'not a role, not a service account, not \"system\"' has to be "
                f"worth something (CLAUDE.md §Hard constraints 1)")
        return name

    @field_validator("mode")
    @classmethod
    def _mode_is_a_declared_act(cls, v: str) -> str:
        if v not in ATTESTATION_MODES:
            raise AttestationError(
                f"Attestation REFUSED: mode {v!r} is not a defined attestation act. "
                f"Valid: {sorted(ATTESTATION_MODES)}")
        return v

    @field_validator("attested_at")
    @classmethod
    def _when_must_be_unambiguous(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise AttestationError(
                "Attestation REFUSED: `attested_at` is naive. An attestation is an audit record "
                "and a time without an offset is not a time")
        return v

    @field_validator("sources")
    @classmethod
    def _must_be_grounded(cls, v: tuple[SourcePointer, ...]) -> tuple[SourcePointer, ...]:
        if not v:
            raise AttestationError(
                "Attestation REFUSED: no SourcePointer. The attestation is itself an emitted "
                "record and P4 does not exempt records about humans (anti-fabrication wall)")
        # Review R-012 (2026-08-16, Critical, finding 4) noted this reduced to `bool(sources)`: an
        # explicitly UNTRUSTED or unlabelled pointer satisfied it just as well as a trusted one.
        # `sources` here records WHERE the attestation was captured (which staff terminal, which
        # ledger row) rather than a span the attested fields were extracted from — this attestation
        # asserts a human act, not a fact read off a page, so there is no byte-span to re-derive it
        # from, and the extraction grounding wall's verbatim-match rule does not transfer. What DOES
        # transfer is spine D3: an untrusted pointer authorising an identity-bearing edge is
        # untrusted content deciding control flow. Capture provenance for a determination must
        # itself be trusted, on the same footing as `growth.EVIDENCE_UNTRUSTED`.
        if trust_lub(v) is not Trust.TRUSTED:
            raise AttestationError(
                "Attestation REFUSED: at least one capture-provenance SourcePointer is not "
                "TRUSTED (untrusted or unlabelled). An attestation authorises an identity-bearing "
                "edge; untrusted content may never decide that (spine D3)")
        return v

    @model_validator(mode="after")
    def _identity_fields_are_present(self) -> "Attestation":
        for field in ("subject_kind", "identifier_checked", "grant_type",
                      "candidate_id", "subject_id", "edge_kind"):
            if not str(getattr(self, field) or "").strip():
                raise AttestationError(
                    f"Attestation REFUSED: {field!r} is empty. It names what was attested to and "
                    f"what the attestation confers; neither is optional")
        return self

    # --- identity of the attestation itself ---------------------------------------------
    @property
    def attestation_id(self) -> str:
        """A content address for this attestation.

        Derived rather than assigned, so two records of the same decision have the same id and a
        replay of one attestation onto a different edge has a *different* triple and therefore a
        different id. The store uses it to refuse a second edge carrying an attestation already
        bound to another (attestation findings 1 and 5).

        Every field the model declares is in the payload, INCLUDING `attestor_role` and
        `statement`. Review R-012 (2026-08-16, Medium, finding 9): those two were left out, so two
        attestations that agreed on who/what/when/which-candidate but disagreed on the free-text
        statement or the role recorded — genuinely different records — shared one content address,
        letting the store's replay guard either treat them as the same act or refuse the second as
        a replay of the first when it was not.
        """
        payload = {
            "attestor": self.attestor, "attestor_principal": self.attestor_principal,
            "attestor_role": self.attestor_role, "statement": self.statement,
            "mode": self.mode, "attested_at": self.attested_at.isoformat(),
            "subject_kind": self.subject_kind, "identifier_checked": self.identifier_checked,
            "grant_type": self.grant_type, "candidate_id": self.candidate_id,
            "subject_id": self.subject_id, "edge_kind": self.edge_kind,
            "sources": [s.model_dump(mode="json", exclude_none=True) for s in self.sources],
        }
        return "at_" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]

    def check_binds(self, *, src_id: str, dst_id: str, kind) -> None:
        """Refuse an attestation that was not made for THIS edge.

        The three comparisons are the whole of attestation finding 1. Without them a valid
        attestation is a bearer token for every node of its subject kind, and "a named human
        selected this candidate as this subject" is a sentence the record does not support.
        """
        mismatches = []
        if self.candidate_id != src_id:
            mismatches.append(f"candidate {self.candidate_id!r} ≠ edge source {src_id!r}")
        if self.subject_id != dst_id:
            mismatches.append(f"subject {self.subject_id!r} ≠ edge destination {dst_id!r}")
        if self.edge_kind != str(kind):
            mismatches.append(f"edge_kind {self.edge_kind!r} ≠ {str(kind)!r}")
        if mismatches:
            raise AttestationError(
                "Attestation REFUSED: it was not made for this edge — "
                + "; ".join(mismatches)
                + ". An attestation records that a named human selected ONE candidate as ONE "
                  "subject; reusing it elsewhere would make it a bearer token for every node of "
                  "its kind (CLAUDE.md §Hard constraints 1)")

    # --- the deployment-dependent half -------------------------------------------------
    def check_against(self, vocab: Optional[Vocabulary] = None) -> None:
        """Check this attestation against the **enabled packs'** declaration for its subject kind.

        Separate from the field validators because it depends on the deployment binding, which is
        rebindable: an attestation is well-formed on its own terms, and *satisfying a pack* is a
        second question asked wherever the attestation is used.
        """
        v = vocab if vocab is not None else active_vocabulary()
        if not v.is_subject(self.subject_kind):
            raise AttestationError(
                f"Attestation REFUSED: {self.subject_kind!r} is not a subject kind in this "
                f"deployment, so there is no identity for a human to attest to. Declared "
                f"subjects: {sorted(v.subject_kinds)} (spine D4b)")
        declared_mode = v.attestation_for(self.subject_kind)
        if self.mode != declared_mode:
            raise AttestationError(
                f"Attestation REFUSED: subject kind {self.subject_kind!r} requires attestation "
                f"mode {declared_mode!r}; this attestation performed {self.mode!r}. The mode IS "
                f"the guarantee — a weaker act does not satisfy a stronger declaration")
        declared_grant = v.identity_grant_for(self.subject_kind)
        if declared_grant is None:
            raise AttestationError(
                f"Attestation REFUSED: no pack confers an identity grant for "
                f"{self.subject_kind!r}. A kind no pack declared a subject gets NO "
                f"identity-resolution capability, not an unguarded one (spine D4b)")
        if self.grant_type != declared_grant:
            raise AttestationError(
                f"Attestation REFUSED: names grant {self.grant_type!r}, but subject kind "
                f"{self.subject_kind!r} confers {declared_grant!r}")
        identifiers = v.identifiers_for(self.subject_kind)
        if self.identifier_checked not in identifiers:
            raise AttestationError(
                f"Attestation REFUSED: identifier_checked={self.identifier_checked!r} is not one "
                f"the pack declares for {self.subject_kind!r}. Declared: {sorted(identifiers)}")


#: How far ahead of the store clock an `attested_at` may sit before it is refused.
#:
#: Not zero: clocks on two machines disagree, and refusing an attestation because a terminal is
#: four seconds fast would push staff into retrying until it worked, which is worse than the
#: thing being prevented. Not open-ended either — attestation finding 5 — because an attestation
#: dated next year is either a mistake or a pre-signed record, and both should be refused at the
#: boundary rather than discovered in an audit.
FUTURE_ATTESTATION_SKEW = timedelta(minutes=5)


def revalidated(attestation: Attestation) -> Attestation:
    """Rebuild an `Attestation` through the ordinary validators and return the rebuild.

    Attestation finding 2, and the reason it is Critical: `model_construct()` and
    `model_copy(update=...)` produce instances that never met a validator, and `check_against()`
    only ever examined the pack-dependent half — so an attestation with an empty `attestor`, a
    naive timestamp and no grounding satisfied the wall.

    Fields are read with `getattr(..., None)` rather than through `model_dump()`, because a
    `model_construct`ed instance can be MISSING a required field entirely and `model_dump()` would
    then either raise or quietly omit it. Reading each declared field by name and handing the
    result to `model_validate` means an absent field fails as an absent field.
    """
    if not isinstance(attestation, Attestation):
        raise AttestationError(
            f"Attestation REFUSED: expected an Attestation, got "
            f"{type(attestation).__name__}. The wall validates a record, not a duck")
    raw = {name: getattr(attestation, name, None) for name in Attestation.model_fields}
    try:
        return Attestation.model_validate(raw)
    except AttestationError:
        raise
    except Exception as exc:                                    # pydantic ValidationError et al.
        raise AttestationError(
            f"Attestation REFUSED: it does not survive revalidation, so it was built through a "
            f"path that skipped the validators (model_construct / model_copy). {exc}") from exc


def _meta_of(kind, vocab: Optional[Vocabulary] = None) -> Optional[EdgeMetaKind]:
    """The edge kind's meta-kind **resolved through one explicit vocabulary**, or ``None``.

    ``None`` is treated as "cannot prove this is not a determination" by the caller, which fails
    CLOSED. An `EdgeKind` instance made under one deployment survives a rebinding (kinds are
    interned), so this is a live possibility rather than a defensive flourish.

    Attestation finding 4: this used to read `kind.meta`, which resolves through the CURRENT global
    deployment — while the caller was validating the subject and the attestation against a
    different, explicitly supplied vocabulary. One check answered from snapshot A and the next from
    snapshot B is a mixed-snapshot defect, and the mix is invisible in the failure it produces.
    Every question is now answered from the same snapshot.
    """
    v = vocab if vocab is not None else active_vocabulary()
    decl = v.edge_kinds.get(str(kind))
    return decl.binds_to if decl is not None else None


def is_determination_kind(kind, vocab: Optional[Vocabulary] = None) -> bool:
    """Whether an edge of this kind records an identity DETERMINATION (`ATTESTED_META_KINDS`)."""
    meta = _meta_of(kind, vocab)
    return meta is not None and meta in ATTESTED_META_KINDS


def enforce_attestation(kind, dst_id: str, attestation: Optional[Attestation], *,
                        src_id: Optional[str] = None,
                        edge_id: str = "", vocab: Optional[Vocabulary] = None,
                        now: Optional[datetime] = None) -> Optional[Attestation]:
    """The wall. Called from `GraphEdge`'s after-validator, and again at the store boundary.

    Refuses in four situations:

    1. the edge kind cannot be resolved in this deployment — we cannot prove it is *not* a
       determination, so it is treated as one and refused without an attestation (fail closed);
    2. it IS a determination and carries no attestation, or one that does not satisfy the pack;
    3. it IS a determination and its destination is not a declared subject;
    4. it is NOT a determination and carries an attestation anyway — the promotion of a claim.

    **Returns the REVALIDATED attestation** (`revalidated(attestation)`), or `None` for a
    non-determination edge that legitimately carries none. Review R-012 (2026-08-16, High,
    finding 8) noted this used to return `None` unconditionally: every check below runs against
    `checked`, the rebuild, but a caller that then persisted the ORIGINAL `attestation` object
    (whatever `model_construct`/whitespace/etc. it carried) would store bytes that were never the
    bytes actually checked — undermining `attestation_id`-based replay detection at the one place
    it matters most. Existing callers that ignore the return value are unaffected; callers that
    persist an attestation should persist THIS one.
    """
    what = f"edge {edge_id!r}" if edge_id else "edge"
    # ONE vocabulary snapshot answers every question below, including the meta-kind lookup
    # (attestation finding 4).
    v = vocab if vocab is not None else active_vocabulary()
    meta = _meta_of(kind, v)

    if meta is None:
        raise AttestationError(
            f"{what} REFUSED: edge kind {str(kind)!r} is not declared by any enabled pack, so "
            f"whether it records an identity determination cannot be established. An edge whose "
            f"meaning is unknown is refused rather than assumed harmless")

    if meta not in ATTESTED_META_KINDS:
        if attestation is not None:
            raise AttestationError(
                f"{what} REFUSED: edge kind {str(kind)!r} binds to {meta.value!r}, which records "
                f"a CLAIM, not a determination — and a claim carrying an attestation reads as a "
                f"determination to everything downstream. Two candidates yield two claim edges "
                f"and neither is an answer; record the determination on a kind bound to "
                f"{sorted(m.value for m in ATTESTED_META_KINDS)} instead "
                f"(CLAUDE.md §Hard constraints 1)")
        return None

    subject_kind = subject_kind_of_node_id(dst_id, v)
    if subject_kind is None:
        raise AttestationError(
            f"{what} REFUSED: an identity determination must point at a declared SUBJECT, and "
            f"destination {dst_id!r} does not name one. Node ids carry their kind as a prefix "
            f"('kind:slug'); declared subjects in this deployment: {sorted(v.subject_kinds)} "
            f"(spine D4b)")

    if attestation is None:
        raise AttestationError(
            f"{what} REFUSED: edge kind {str(kind)!r} records an identity DETERMINATION and "
            f"carries no Attestation. A SourcePointer is not an attestation — it says where a "
            f"value was read, not that a named human selected this candidate in person. This "
            f"system never auto-confirms a subject's identity "
            f"(CLAUDE.md §Hard constraints 1; spine D4b/D12)")

    # Rebuild before believing anything it says (attestation finding 2). Everything below reads
    # the REBUILD, never the object that was handed in — otherwise the checks would be run against
    # a validated copy while the forged original is what gets persisted.
    checked = revalidated(attestation)

    if checked.subject_kind != subject_kind:
        raise AttestationError(
            f"{what} REFUSED: the attestation is for subject kind "
            f"{checked.subject_kind!r} but the destination {dst_id!r} is a "
            f"{subject_kind!r}. An attestation to one subject kind does not carry to another")

    # The binding. Review R-012 (2026-08-16, Critical, finding 2): this used to default a missing
    # `src_id` to `checked.candidate_id` — comparing the attestation's candidate to ITSELF, which
    # always succeeds and is not a weaker check, it is no check at all. A caller validating an edge
    # whose real source was `doc:B` could omit `src_id` and reuse an attestation made for `doc:A`,
    # and `check_binds` would report the two as equal because it was never told what to compare
    # against. The wall must refuse a determination whose source it cannot verify, not silently
    # skip the verification.
    if src_id is None:
        raise AttestationError(
            f"{what} REFUSED: no `src_id` was supplied, so the attestation's `candidate_id` "
            f"cannot be checked against the edge's actual source. Comparing it to itself would "
            f"not be a weaker check, it would be no check — every caller enforcing this wall "
            f"(`GraphEdge`, `GraphStore.add_edge`) MUST supply the edge's real source id "
            f"(CLAUDE.md §Hard constraints 1)")
    checked.check_binds(src_id=src_id, dst_id=dst_id, kind=kind)

    if now is not None:
        # Attestation finding 5. Only a caller holding a clock can ask this, which is why it is a
        # parameter and why the STORE is the caller that passes one: a model validator has no
        # authoritative clock, and one it invented would be the caller's clock wearing a uniform.
        if checked.attested_at > now + FUTURE_ATTESTATION_SKEW:
            raise AttestationError(
                f"{what} REFUSED: attested_at {checked.attested_at.isoformat()} is ahead of the "
                f"store clock {now.isoformat()} by more than {FUTURE_ATTESTATION_SKEW}. An "
                f"attestation dated in the future is either a wrong clock or a pre-signed record, "
                f"and neither may become durable")

    checked.check_against(v)
    return checked


def record_determination(store, *, edge_id: str, src_id: str, dst_id: str, kind,
                         attestation: Attestation, sources: Sequence[SourcePointer],
                         valid_from: datetime,
                         attrs: Optional[dict] = None,
                         attr_sources: Optional[dict] = None,
                         weight: float = 1.0) -> Any:
    """The front door for writing a determination: build the edge, then upsert it.

    Deliberately **not** the only door. A private write API beside a public constructor is a lock
    on one of two doors, so the enforcement lives on the model (`GraphEdge`) and at the store, and
    this function only makes the correct call ergonomic. Its value is that the argument list names
    every part of the record a determination needs, so a caller cannot forget one by omission.

    **`system_from` is no longer a parameter** (attestation finding 5). It is the moment the store
    learned the fact, and a caller that can set it can backdate the audit boundary — which makes
    "what did we know on the day we decided this?" answerable only by whoever wrote the row. It
    comes from `store.now()` and nowhere else.
    """
    from .merge import upsert_edge          # local: merge imports schema, schema imports this
    from .schema import GraphEdge

    edge = GraphEdge(
        id=edge_id, src_id=src_id, dst_id=dst_id, kind=kind, weight=weight,
        attrs=attrs or {}, attr_sources=attr_sources or {},
        sources=tuple(sources), attestation=attestation,
        valid_from=valid_from, system_from=store.now())
    return upsert_edge(store, edge)


__all__ = [
    "Attestation",
    "AttestationError",
    "FUTURE_ATTESTATION_SKEW",
    "revalidated",
    "enforce_attestation",
    "is_determination_kind",
    "kind_of_node_id",
    "record_determination",
    "subject_kind_of_node_id",
]
