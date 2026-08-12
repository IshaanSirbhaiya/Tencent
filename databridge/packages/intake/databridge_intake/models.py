"""Normalized intake schemas (PLAN.md §2) — the contract every stage of the pipeline speaks.

Two load-bearing design rules, both from CLAUDE.md §Hard constraints:

**1. One schema for all document types.** A referral letter, a voucher, an underwriting memo, a
TPA authorisation form and an HR email all land in the SAME ``IntakeRecord``. Heterogeneity is
expressed by *which blocks are populated*, never by per-issuer subclasses or per-template branches.
Adding an insurer is a row in ``config/coverage_rules.yaml`` — zero code changes.

**2. No value without a source.** Every extracted leaf is a ``GroundedValue`` carrying a
``SourcePointer``. A field the document does not carry is ``None`` **and** its dotted path is
listed in ``IntakeRecord.unresolved`` — never guessed, inferred from the registry, or defaulted.
``unresolved`` is what makes absence a first-class, countable, reviewable fact instead of a silent
null.

Nothing in ``SubjectBlock`` is an identity assertion — the fields are named ``claimed_*`` so that
no downstream reader can mistake "the document says this" for "this person is who they say".
Identity verification happens in person, by a human. See ``ResolutionResult``.
"""
from __future__ import annotations

import datetime as dt
import functools
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import GroundedValue, SourcePointer

# --- closed vocabularies ----------------------------------------------------------


class DocumentType(str, Enum):
    """The referring-document taxonomy (brief §3).

    Closed set. The classifier may return ``UNRECOGNISED`` and MUST when it is not confident —
    abstention routes to a human, which is always the safe direction. ``NO_DOCUMENT`` is a
    first-class path (walk-in / self-pay), not an error state.
    """

    INSURER_REFERRAL_LETTER = "insurer_referral_letter"
    HEALTH_CHECKUP_VOUCHER = "health_checkup_voucher"
    GROUP_INSURANCE_INVITATION = "group_insurance_invitation"
    UNDERWRITING_FOLLOWUP = "underwriting_followup"
    TPA_AUTHORISATION_FORM = "tpa_authorisation_form"
    EMPLOYER_HR_EMAIL = "employer_hr_email"
    NO_DOCUMENT = "no_document"
    UNRECOGNISED = "unrecognised"


class IssuerKind(str, Enum):
    INSURER = "insurer"
    TPA = "tpa"
    EMPLOYER = "employer"
    GOVERNMENT_SCHEME = "government_scheme"
    NONE = "none"


class BillingParty(str, Enum):
    """Who the visit is billed to. ``PATIENT`` is the walk-in / self-pay path."""

    INSURER = "insurer"
    EMPLOYER = "employer"
    GOVERNMENT_SCHEME = "government_scheme"
    TPA = "tpa"
    PATIENT = "patient"


class VisitType(str, Enum):
    GP_CONSULT = "gp_consult"
    GENERAL_HEALTH_SCREENING = "general_health_screening"
    OCCUPATIONAL_HEALTH_SCREENING = "occupational_health_screening"
    UNKNOWN = "unknown"


class IdType(str, Enum):
    """Format classification of an identifier — NOT a statement about its validity.

    ``UNKNOWN`` exists because the alternative is worse: without it, a blank or malformed
    identifier gets positively asserted as a passport, which is a guessed value in the one package
    whose rule is that absence must be recorded as absence.
    """

    NRIC_FIN = "nric_fin"
    PASSPORT = "passport"
    UNKNOWN = "unknown"


class QuestionnaireKind(str, Enum):
    GENERAL_HEALTH = "general_health"
    OCCUPATIONAL_HEALTH = "occupational_health"
    NONE = "none"


# --- patient registry -------------------------------------------------------------


class PatientRecord(BaseModel):
    """One row of ``patient_registration_synthetic.csv``, fully grounded (PLAN.md §2.1).

    Every field is a ``GroundedValue`` whose pointer is a ``table_cell`` into the CSV — so the
    front desk can click any pre-filled value and see the exact cell it came from. ``date_of_birth``
    additionally carries a ``derived`` pointer recording the two-digit-year pivot (§2.1.1), because
    an age that drives an eligibility decision must be traceable to the rule that produced it.
    """

    model_config = ConfigDict(frozen=True)

    patient_id: str
    full_name: GroundedValue
    id_number: GroundedValue
    id_type: GroundedValue
    sex: GroundedValue
    nationality: GroundedValue
    date_of_birth: GroundedValue          # value is a datetime.date
    address: GroundedValue
    postal_code: GroundedValue
    contact_home: GroundedValue
    contact_office: GroundedValue
    contact_mobile: GroundedValue
    email: GroundedValue
    drug_allergy: GroundedValue           # "None" is a real recorded answer, not a null

    def age_at(self, on: dt.date) -> int:
        """Whole years old on ``on``. Derived, never stored — see §2.1.1."""
        dob = self.date_of_birth.value
        if not isinstance(dob, dt.date):
            raise TypeError(f"date_of_birth must hold a date, got {type(dob).__name__}")
        return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


# --- the normalized document extraction -------------------------------------------


class DocumentRef(BaseModel):
    """Identity of the source document an ``IntakeRecord`` was read from."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    sha256: str
    media_type: str                       # text/plain | message/rfc822 | application/pdf | image/*
    channel: str                          # upload | email_forward | walk_in
    received_at: dt.datetime


class IssuerBlock(BaseModel):
    """Who is asking for the examination, and who pays."""

    model_config = ConfigDict(frozen=True)

    issuer_name: Optional[GroundedValue] = None
    issuer_kind: Optional[GroundedValue] = None      # an IssuerKind value
    issuer_code: Optional[GroundedValue] = None      # VERBATIM as printed: MRDEB, MOL0199VME…
    policy_number: Optional[GroundedValue] = None
    reference_number: Optional[GroundedValue] = None  # certificate / proposal / contract no.
    contact: Optional[GroundedValue] = None


class SubjectBlock(BaseModel):
    """Who the document is ABOUT — as *claimed by the document*, never as verified fact.

    The ``claimed_`` prefix is deliberate and load-bearing. Downstream code reads these to build a
    registry *candidate search*; nothing here ever becomes an identity determination. Identity is
    checked in person by staff (CLAUDE.md §Hard constraints 1).
    """

    model_config = ConfigDict(frozen=True)

    claimed_name: Optional[GroundedValue] = None
    claimed_id_number: Optional[GroundedValue] = None
    claimed_id_type: Optional[GroundedValue] = None
    claimed_dob: Optional[GroundedValue] = None
    claimed_sex: Optional[GroundedValue] = None
    claimed_nationality: Optional[GroundedValue] = None


class AuthorisationBlock(BaseModel):
    """What is authorised: package, tests, validity window, appointment, billing party."""

    model_config = ConfigDict(frozen=True)

    purpose_text: Optional[GroundedValue] = None
    package_code: Optional[GroundedValue] = None     # WELL2, PEE226 — as printed
    package_name: Optional[GroundedValue] = None
    requested_tests: list[GroundedValue] = Field(default_factory=list)   # verbatim strings
    add_ons: list[GroundedValue] = Field(default_factory=list)           # vaccinations / serology
    valid_from: Optional[GroundedValue] = None
    valid_until: Optional[GroundedValue] = None
    appointment_datetime: Optional[GroundedValue] = None
    appointment_location: Optional[GroundedValue] = None
    billing_party: Optional[GroundedValue] = None
    billing_address: Optional[GroundedValue] = None
    special_instructions: list[GroundedValue] = Field(default_factory=list)


class EmployerBlock(BaseModel):
    """The employing organisation / agency, when the document names one."""

    model_config = ConfigDict(frozen=True)

    employer_name: Optional[GroundedValue] = None
    agency: Optional[GroundedValue] = None
    scheme_of_service: Optional[GroundedValue] = None
    hr_contact: Optional[GroundedValue] = None


def _check_enum(gv: Optional[GroundedValue], enum_cls: type[Enum], path: str) -> None:
    """Assert a grounded value's payload is a member of its closed vocabulary."""
    if gv is None:
        return
    allowed = {m.value for m in enum_cls}
    if gv.value not in allowed:
        raise ValueError(
            f"IntakeRecord REFUSED: {path}={gv.value!r} is not a {enum_cls.__name__} "
            f"(allowed: {sorted(allowed)})")


@functools.lru_cache(maxsize=1)
def _known_field_paths() -> frozenset[str]:
    """Every dotted path an ``IntakeRecord`` can legitimately carry — used to reject an
    ``unresolved`` entry naming a field that does not exist."""
    paths = {"document_type", "visit_type"}
    for block_name, block_cls in (("issuer", IssuerBlock), ("subject", SubjectBlock),
                                  ("authorisation", AuthorisationBlock),
                                  ("employer", EmployerBlock)):
        paths.update(f"{block_name}.{f}" for f in block_cls.model_fields)
    return frozenset(paths)


class IntakeRecord(BaseModel):
    """The normalized, fully-grounded reading of one referring document.

    ``unresolved`` and ``refused`` are the honesty channels and are as important as the data:
      * ``unresolved`` — dotted paths the document genuinely does not carry ("this voucher has no
        NRIC on it"). Expected, benign, and shown to staff as "not on document".
      * ``refused``    — values the model proposed that the grounding wall could not locate in the
        source, with reasons. These are caught fabrications. A non-empty list is a signal worth
        surfacing, not hiding.
    """

    model_config = ConfigDict(frozen=True)

    intake_id: str
    document: DocumentRef
    document_type: GroundedValue                     # a DocumentType value + confidence
    issuer: IssuerBlock = Field(default_factory=IssuerBlock)
    subject: SubjectBlock = Field(default_factory=SubjectBlock)
    authorisation: AuthorisationBlock = Field(default_factory=AuthorisationBlock)
    employer: EmployerBlock = Field(default_factory=EmployerBlock)
    visit_type: Optional[GroundedValue] = None       # a VisitType value
    unresolved: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    extracted_at: dt.datetime

    @model_validator(mode="after")
    def _closed_vocabularies_hold(self) -> "IntakeRecord":
        """A ``GroundedValue`` carries an arbitrary payload, so the enums above are only real if
        something checks them. Without this, a classifier returning ``"referral_letter"`` instead
        of ``"insurer_referral_letter"`` flows through the whole pipeline and merely fails to match
        any coverage rule — presenting as "no coverage found" rather than as the bug it is."""
        _check_enum(self.document_type, DocumentType, "document_type")
        _check_enum(self.visit_type, VisitType, "visit_type")
        _check_enum(self.issuer.issuer_kind, IssuerKind, "issuer.issuer_kind")
        _check_enum(self.subject.claimed_id_type, IdType, "subject.claimed_id_type")
        return self

    @model_validator(mode="after")
    def _unresolved_describes_real_absences(self) -> "IntakeRecord":
        """``unresolved`` is a claim about this record, so it has to be true of this record.

        A path listed as unresolved while the field is populated would show staff "not on document"
        next to a value we did extract; an unknown path would render a row for a field that does
        not exist. Both erode the one thing this list is for — being trustworthy about absence.
        """
        if dupes := {p for p in self.unresolved if list(self.unresolved).count(p) > 1}:
            raise ValueError(f"IntakeRecord REFUSED: duplicate unresolved path(s) {sorted(dupes)}")
        populated = set(self.grounded_fields())
        known = _known_field_paths()
        for path in self.unresolved:
            base = path.split("[")[0]
            if base not in known:
                raise ValueError(
                    f"IntakeRecord REFUSED: unresolved path {path!r} is not a field of this schema")
            if path in populated:
                raise ValueError(
                    f"IntakeRecord REFUSED: {path!r} is listed as unresolved but has a value")
        return self

    def grounded_fields(self) -> dict[str, GroundedValue]:
        """Every populated leaf, keyed by dotted path — the flat view the UI and the ledger use.

        List fields are indexed (``authorisation.requested_tests[0]``) so each element keeps its
        own addressable pointer; a test authorised by the document must be individually citable.
        """
        out: dict[str, GroundedValue] = {"document_type": self.document_type}
        if self.visit_type is not None:
            out["visit_type"] = self.visit_type
        for block_name in ("issuer", "subject", "authorisation", "employer"):
            block = getattr(self, block_name)
            for field_name in type(block).model_fields:
                val = getattr(block, field_name)
                if isinstance(val, GroundedValue):
                    out[f"{block_name}.{field_name}"] = val
                elif isinstance(val, list):
                    for i, item in enumerate(val):
                        if isinstance(item, GroundedValue):
                            out[f"{block_name}.{field_name}[{i}]"] = item
        return out


# --- resolution (candidates only — never a confirmation) --------------------------


class ResolutionStatus(str, Enum):
    SINGLE_CANDIDATE = "single_candidate"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    NO_CANDIDATE = "no_candidate"
    INSUFFICIENT_IDENTIFIERS = "insufficient_identifiers"


class ResolutionCandidate(BaseModel):
    """A registry row that *might* be this patient, with the reason it scored."""

    model_config = ConfigDict(frozen=True)

    patient_id: str
    score: float = Field(ge=0.0, le=1.0)
    matched_on: list[str] = Field(default_factory=list)   # ["id_number", "dob"] — WHY it matched
    conflicts: list[str] = Field(default_factory=list)    # ["dob_mismatch"] — why to be careful
    record: PatientRecord


class ResolutionResult(BaseModel):
    """Ranked registry candidates for a claimed subject. **Never a confirmation.**

    ``requires_human_selection`` is ``Literal[True]``: pydantic rejects any other value at
    construction, so there is no code path — present or future — that can produce a resolution the
    system considers settled. Even a single exact-NRIC candidate at score 1.0 is a *candidate*.
    A human selects it, in person, after checking the physical ID (CLAUDE.md §Hard constraints 1).

    ``status`` is checked against ``candidates`` rather than trusted: a result claiming
    ``SINGLE_CANDIDATE`` with an empty list would render a confident-looking card backed by
    nothing, and ``NO_CANDIDATE`` while holding candidates would hide real matches from staff.
    """

    model_config = ConfigDict(frozen=True)

    candidates: tuple[ResolutionCandidate, ...] = ()
    status: ResolutionStatus
    requires_human_selection: Literal[True] = True
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_the_candidates(self) -> "ResolutionResult":
        n = len(self.candidates)
        expected = {
            ResolutionStatus.SINGLE_CANDIDATE: n == 1,
            ResolutionStatus.MULTIPLE_CANDIDATES: n > 1,
            ResolutionStatus.NO_CANDIDATE: n == 0,
            ResolutionStatus.INSUFFICIENT_IDENTIFIERS: n == 0,
        }[self.status]
        if not expected:
            raise ValueError(
                f"ResolutionResult REFUSED: status={self.status.value!r} contradicts "
                f"{n} candidate(s)")
        scores = [c.score for c in self.candidates]
        if scores != sorted(scores, reverse=True):
            raise ValueError(
                f"ResolutionResult REFUSED: candidates must be ranked best-first (got {scores}) — "
                f"the UI presents them in order and `top` assumes it")
        return self

    @property
    def top(self) -> Optional[ResolutionCandidate]:
        """Highest-scoring candidate, for display ordering only — NOT a selection."""
        return self.candidates[0] if self.candidates else None


# --- eligibility / coverage -------------------------------------------------------


class CoverageStatus(str, Enum):
    DETERMINED = "determined"
    PARTIAL = "partial"
    UNDETERMINED = "undetermined"


class AuthorisedTest(BaseModel):
    """One test the document authorises, mapped to the catalogue where possible.

    ``pointer`` is the span of the source document the printed name came from, so a test on the
    front-desk card can be clicked through to the line of the letter that ordered it. Tests that
    come from a package definition rather than the document itself (``in_package`` with no printed
    line) legitimately have no document pointer — their provenance is the rule id on the parent
    ``CoverageDetermination``.
    """

    model_config = ConfigDict(frozen=True)

    as_printed: str
    canonical: Optional[str] = None        # None ⇒ not in the catalogue; surfaced, never invented
    in_package: bool = False
    list_price_sgd: Optional[Decimal] = None
    source_field: Optional[str] = None     # dotted path back into the IntakeRecord
    pointer: Optional[SourcePointer] = None
    ambiguous_with: tuple[str, ...] = ()   # catalogue entries this matched equally well


class HistoryFinding(BaseModel):
    """Something the CLINIC GRAPH knows that this document does not say.

    Carried as a typed, grounded object rather than folded into ``reasoning`` as prose, because a
    graph-derived claim has to be clickable-through exactly like an extracted field. Reducing a
    traversal to a string discards the ``FOR_PATIENT`` / ``ISSUED`` / ``AUTHORISES`` pointers that
    are its only evidence — and then the card tells staff "document X conflicts" with nothing behind
    it (CLAUDE.md §Hard constraints 2).
    """

    model_config = ConfigDict(frozen=True)

    kind: str                              # closed vocabulary — see eligibility.FLAG_* constants
    message: str
    document_id: str
    patient_id: str
    sources: list[SourcePointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _must_cite_the_traversal(self) -> "HistoryFinding":
        if not self.sources:
            raise ValueError(
                "HistoryFinding REFUSED: no SourcePointer. A finding derived from the graph must "
                "cite the nodes and edges it was traversed from.")
        return self


class CoverageDetermination(BaseModel):
    """What the config table says this document entitles the patient to.

    ``reasoning`` is a human-readable trace of which YAML rule fired and why — the front desk sees
    it, and it is what makes the determination arguable rather than oracular. ``status`` is
    ``UNDETERMINED`` whenever the document does not carry enough to decide (e.g. an authorisation
    form with no test code ticked): the system says so instead of guessing.
    """

    model_config = ConfigDict(frozen=True)

    issuer_code: Optional[str] = None
    matched_rule_id: Optional[str] = None
    package_code: Optional[str] = None
    package_name: Optional[str] = None
    authorised_tests: list[AuthorisedTest] = Field(default_factory=list)
    questionnaire: QuestionnaireKind = QuestionnaireKind.NONE
    eligibility_flags: list[str] = Field(default_factory=list)
    unmatched_tests: list[str] = Field(default_factory=list)
    status: CoverageStatus = CoverageStatus.UNDETERMINED
    reasoning: list[str] = Field(default_factory=list)
    # Findings from the clinic GRAPH rather than from this document. Each carries the pointers of
    # the traversal that produced it.
    history_findings: list[HistoryFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_is_supported_by_the_determination(self) -> "CoverageDetermination":
        """``status`` is a claim about this object, so it has to be true of this object.

        Without this, ``CoverageDetermination(status=DETERMINED)`` is constructible with no issuer,
        no rule, no tests and no reasoning — a fully confident answer backed by nothing, which
        would render on the summary card as settled coverage. The same argument as
        ``IntakeRecord.unresolved``: the honesty channels only mean something if something checks
        them.
        """
        if self.status is CoverageStatus.UNDETERMINED:
            if self.package_code:
                raise ValueError(
                    "CoverageDetermination REFUSED: status=undetermined but a package_code is "
                    "set — an undetermined coverage must not name a package")
            return self
        if not self.matched_rule_id:
            raise ValueError(
                f"CoverageDetermination REFUSED: status={self.status.value} without a "
                f"matched_rule_id — a determination must name the rule that produced it")
        if not self.reasoning:
            raise ValueError(
                f"CoverageDetermination REFUSED: status={self.status.value} with an empty "
                f"reasoning trace — a determination staff cannot argue with is not one")
        if self.status is CoverageStatus.DETERMINED and (self.eligibility_flags
                                                         or self.unmatched_tests):
            raise ValueError(
                "CoverageDetermination REFUSED: status=determined while carrying "
                f"flags={self.eligibility_flags} unmatched={self.unmatched_tests} — anything "
                "needing a human makes this partial, not determined")
        return self


# --- billing ----------------------------------------------------------------------


class BillingDraft(BaseModel):
    """A *draft* determination for staff to approve. Never an authorisation to charge."""

    model_config = ConfigDict(frozen=True)

    billed_to: str                         # insurer | employer | government_scheme | patient
    # Every scalar below is a determination, and each was read from a different row of
    # coverage_rules.yaml. A draft that carried them behind one caveat list was the Shape A
    # pattern on the artefact where money is the point: `billed_to` says who pays, and nothing
    # on the object said where that came from. Keyed by field name, exactly like the graph.
    field_sources: dict[str, SourcePointer] = Field(default_factory=dict)
    covered_items: list[dict] = Field(default_factory=list)
    patient_payable_items: list[dict] = Field(default_factory=list)
    # A test the document prints that is not in the catalogue at all: neither confirmed covered
    # nor confirmed patient-payable, because we have nothing to check coverage against. Its own
    # bucket, separate from `patient_payable_items`, so a consumer cannot read "the patient pays
    # for this" out of a determination we were never able to make (billing.py `draft_billing`).
    unknown_coverage_items: list[dict] = Field(default_factory=list)
    # `None` whenever ANYTHING on the document is unpriced or uncatalogued. A number here is a
    # complete estimate or it is not a number: a UI renders the figure and may not render the
    # caveat beside it, and "SGD 0" next to a non-empty `unknown_coverage_items` told a patient
    # with one uncatalogued $400 test that they owed nothing. The machine-readable
    # `TOTAL_IS_LOWER_BOUND` caveat was not enough, because it relies on the consumer choosing to
    # read it.
    estimated_patient_payable_sgd: Optional[Decimal] = None
    # What IS known, named so it cannot be mistaken for the whole. Present whenever the total is
    # `None` but some lines did price — the front desk still needs the figure it can quote.
    known_subtotal_sgd: Optional[Decimal] = None
    tpa_claim_required: bool = False
    caveats: list[str] = Field(default_factory=list)
    status: Literal["draft"] = "draft"


# --- questionnaire pre-fill -------------------------------------------------------

class PrefillStatus(str, Enum):
    """An enum, not a string: ``prefilled_count`` feeds the demo's time-saved metric, so a typo
    like ``"prefill"`` would silently report zero minutes saved rather than failing."""

    PREFILLED = "prefilled"
    PATIENT_MUST_ANSWER = "patient_must_answer"
    NOT_APPLICABLE = "not_applicable"


# Back-compat alias for the string constant used in prose and configs.
PATIENT_MUST_ANSWER = PrefillStatus.PATIENT_MUST_ANSWER.value


class PrefilledField(BaseModel):
    """One questionnaire field: either pre-filled with provenance, or left for the patient.

    A field is pre-filled ONLY when it is administrative re-entry (name, NRIC, DOB, contact,
    address) or drug allergy offered for confirmation. Clinical self-report — medical history,
    family history, lifestyle, pregnancy, the declaration and the signature — is always
    ``patient_must_answer``. Pre-filling a patient's medical history from another dataset would be
    fabricating clinical data about a person (PLAN.md §6.4).

    The status/value invariant is enforced, not assumed: a field marked ``prefilled`` with no
    value would render an empty box the patient thinks is already answered, and one marked
    ``patient_must_answer`` while carrying a value is precisely the clinical-prefill leak the
    scope line above forbids.
    """

    model_config = ConfigDict(frozen=True)

    field_id: str
    label: str
    value: Optional[GroundedValue] = None
    status: PrefillStatus = PrefillStatus.PATIENT_MUST_ANSWER
    requires_confirmation: bool = False    # drug allergy: shown pre-filled, patient must confirm

    @model_validator(mode="after")
    def _status_matches_value(self) -> "PrefilledField":
        if self.status is PrefillStatus.PREFILLED and self.value is None:
            raise ValueError(
                f"PrefilledField {self.field_id!r} REFUSED: status=prefilled with no value")
        if self.status is not PrefillStatus.PREFILLED and self.value is not None:
            raise ValueError(
                f"PrefilledField {self.field_id!r} REFUSED: status={self.status.value} but a value "
                f"is present — a field the patient must answer cannot arrive pre-answered")
        return self


class PrefilledQuestionnaire(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: QuestionnaireKind
    fields: tuple[PrefilledField, ...] = ()

    @property
    def prefilled_count(self) -> int:
        return sum(1 for f in self.fields if f.status is PrefillStatus.PREFILLED)

    @property
    def patient_must_answer_count(self) -> int:
        return sum(1 for f in self.fields if f.status is PrefillStatus.PATIENT_MUST_ANSWER)


# --- the human confirmation gate --------------------------------------------------


class ConfirmationState(BaseModel):
    """What a human has actually attested to. Nothing commits without this.

    **Frozen, with explicit transitions.** The obvious design — a mutable model with
    ``validate_assignment=True`` — does not actually hold: pydantic writes the field *first* and
    then runs model validators, so a rejected assignment raises but leaves the bad value in place.
    A guard that throws while the object keeps the forbidden state is worse than no guard, because
    a caller who swallows the exception ends up with a patient selected and nobody's name against
    the ID check.

    So the state is immutable and every human act returns a NEW instance, validated at
    construction — the only place pydantic's guarantees are absolute. An invalid
    ``ConfirmationState`` cannot be brought into existence, which is the property the identity
    constraint actually needs (CLAUDE.md §Hard constraints 1).
    """

    model_config = ConfigDict(frozen=True)

    intake_id: str
    identity_checked_in_person_by: Optional[str] = None
    identity_checked_at: Optional[dt.datetime] = None
    selected_patient_id: Optional[str] = None
    confirmed_fields: dict[str, str] = Field(default_factory=dict)   # field path → staff id
    committed: bool = False

    @model_validator(mode="after")
    def _gate_invariants(self) -> "ConfirmationState":
        if self.selected_patient_id is not None and not self.identity_checked_in_person_by:
            raise ValueError(
                "ConfirmationState REFUSED: selected_patient_id is set but "
                "identity_checked_in_person_by is not — a registry candidate cannot be selected "
                "before a human has verified identity in person (CLAUDE.md §Hard constraints 1)")
        # An attester with no timestamp is not an audit record. Require them together so the
        # ledger can always answer "who checked, and when".
        if bool(self.identity_checked_in_person_by) != bool(self.identity_checked_at):
            raise ValueError(
                "ConfirmationState REFUSED: identity_checked_in_person_by and identity_checked_at "
                "must be set together — an attestation without a time is not an audit record")
        if self.committed and not self.is_ready_to_commit()[0]:
            raise ValueError(
                "ConfirmationState REFUSED: committed=True without a completed in-person identity "
                "check and a staff-selected candidate")
        return self

    def is_ready_to_commit(self) -> tuple[bool, str]:
        """Whether the confirm gate may open, and why not if it may not."""
        if not self.identity_checked_in_person_by or not self.identity_checked_at:
            return False, "identity has not been verified in person by staff"
        if not self.selected_patient_id:
            return False, "no registry candidate has been selected by staff"
        return True, ""

    # -- transitions ---------------------------------------------------------------
    # Every transition goes through `_evolve`, which re-validates. NEVER use
    # `model_copy(update=...)` here: it writes the fields WITHOUT running validators, so a
    # transition written that way silently reopens the exact hole this class exists to close.
    def _evolve(self, **changes) -> "ConfirmationState":
        return ConfirmationState.model_validate({**self.model_dump(), **changes})

    def model_copy(self, *, update=None, deep=False):  # type: ignore[override]
        """Re-validate on copy. Pydantic's default skips validation entirely, which would let
        ``state.model_copy(update={"committed": True})`` forge a committed state with no
        attestation. For a model whose whole job is to be a gate, an unvalidated copy constructor
        is a bypass, so this one validates."""
        copied = super().model_copy(update=update, deep=deep)
        return ConfirmationState.model_validate(copied.model_dump())

    def _require_open(self, action: str) -> None:
        if self.committed:
            raise ValueError(
                f"{action} REFUSED: this intake is already committed. The evidence ledger is "
                f"append-only — record a correction as a new entry rather than reopening the gate")

    def attest_identity_checked(self, staff_id: str, at: dt.datetime) -> "ConfirmationState":
        """Record the in-person ID check — the only thing that can open the gate.

        Requires a named staff member: an attestation with nobody's name on it is not an
        attestation, it is a checkbox. Refuses to overwrite an existing attestation, so the name
        against a completed check cannot be swapped after the fact.
        """
        self._require_open("attest_identity_checked")
        if not staff_id or not staff_id.strip():
            raise ValueError("attestation requires a staff identifier")
        if at is None:
            raise ValueError("attestation requires the time the check was performed")
        if self.identity_checked_in_person_by:
            raise ValueError(
                f"attest_identity_checked REFUSED: identity was already attested by "
                f"{self.identity_checked_in_person_by!r} at {self.identity_checked_at} — an "
                f"attestation is a record of something a person did, not a mutable field")
        return self._evolve(identity_checked_in_person_by=staff_id.strip(),
                            identity_checked_at=at)

    def select_patient(self, patient_id: str) -> "ConfirmationState":
        """Record the candidate a human picked. Refused before the in-person check."""
        self._require_open("select_patient")
        if not patient_id or not patient_id.strip():
            raise ValueError("select_patient requires a patient id")
        return self._evolve(selected_patient_id=patient_id.strip())

    def confirm_field(self, field_path: str, staff_id: str) -> "ConfirmationState":
        """Record that a named human accepted one extracted field."""
        self._require_open("confirm_field")
        if not staff_id or not staff_id.strip():
            raise ValueError("confirm_field requires a staff identifier")
        return self._evolve(confirmed_fields={**self.confirmed_fields, field_path: staff_id.strip()})

    def commit(self) -> "ConfirmationState":
        """Close the gate. Refused unless identity was checked and a candidate selected."""
        self._require_open("commit")
        ok, why = self.is_ready_to_commit()
        if not ok:
            raise ValueError(f"commit REFUSED: {why}")
        return self._evolve(committed=True)


# --- the append-only evidence ledger ----------------------------------------------


class EvidenceEntry(BaseModel):
    """One committed fact. Append-only: corrections are new rows, never updates (PLAN.md §5.4).

    ``pointer`` is a real ``SourcePointer``, not a JSON blob. A ledger row whose provenance is an
    unvalidated string can hold ``""`` or ``"not json"`` and still look like evidence; typing it
    means an entry that cannot say where it came from cannot be written at all. Serialisation to
    the SQLite column happens at the storage boundary, not in the schema.
    """

    model_config = ConfigDict(frozen=True)

    intake_id: str
    field_path: str
    value: Any
    pointer: SourcePointer
    extracted_at: dt.datetime
    confirmed_by: str
    confirmed_at: dt.datetime
    # The candidate the human selected. Stored on every row so the durable record can show the
    # pair the gate exists to prove — an attestation AND the patient it was made about. Without
    # it the selection lives only in process memory and is gone on restart.
    selected_patient_id: Optional[str] = None
    supersedes: Optional[int] = None       # rowid of the entry this correction replaces

    @model_validator(mode="after")
    def _confirmed_by_a_named_human(self) -> "EvidenceEntry":
        if not self.confirmed_by.strip():
            raise ValueError("EvidenceEntry REFUSED: confirmed_by must name the staff member who "
                             "confirmed this value — an unattributed commit is not an audit trail")
        return self
