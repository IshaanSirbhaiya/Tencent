"""The post-visit TPA claim draft — assembled from **committed evidence only**.

Problem-statement step 12 is the most expensive line in the as-is table (5-8 min) and the only one
it raises twice: after the visit, staff log into the insurer's portal and re-key what the clinic
already knows. This module drafts that submission. A human sends it; nothing here contacts a payer.

Three properties, each of which the obvious implementation gets wrong:

**It cannot read the working draft.** ``build_claim_draft`` takes an ``EvidenceLedger`` and the
config table, and that is all it takes — there is no parameter through which an uncommitted value
could arrive. The first version of this endpoint read the in-memory draft and returned
``submit_to``/``issuer_code``/``package_code``/``patient_id``/``line_items`` as bare strings, so a
partial confirmation (``field_paths``) emitted fields no human had ever confirmed, and none of them
could say where they came from. Making the ledger the *only* input is what stops that recurring;
a comment saying "only use committed values" would not have.

**Every emitted value carries a pointer, structurally.** ``ClaimValue.pointer`` is required, so a
field is either absent or grounded — there is no third state in which a value exists without
provenance (CLAUDE.md §Hard constraints 2). Values read straight from the ledger carry the
``doc_span`` the extractor recorded. Values the clinic's config decides — who the payer is, whether
a TPA submission is required — carry a ``derived`` pointer naming the ``coverage_rules.yaml`` row
and citing the committed rows that selected it. Anything missing is ``None`` **and** named in
``unresolved``, which a validator enforces rather than trusts.

**The patient id is provenance-tracked as a human act, not as a fact about the document.** Its
pointer is a ``table_cell`` into the evidence ledger's ``selected_patient_id`` column, so it
resolves to the row recording that a named person selected this candidate after an in-person check
they attested to. That is the honest description: the system never verified anyone's identity, and
the pointer must not imply otherwise (CLAUDE.md §Hard constraints 1).
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer

from .billing import SYNTHETIC_PRICE_CAVEAT, covered_by_rule
from .ledger import EvidenceLedger
from .models import BillingParty
from .rules import CoverageRules, rules

# The same source id ``clinic_seed`` uses for pointers into the rules file, so a claim's provenance
# and the graph's agree about what to call it.
RULES_SOURCE_ID = "coverage_rules.yaml"

ISSUER_CODE_PATH = "issuer.issuer_code"
PACKAGE_CODE_PATH = "authorisation.package_code"
TEST_PATH_PREFIXES = ("authorisation.requested_tests[", "authorisation.add_ons[")

DRAFT_CAVEAT = ("This is a draft. A staff member reviews and submits it; the system never files a "
                "claim on anyone's behalf.")
COMMITTED_ONLY_CAVEAT = ("Every value here is read from the evidence ledger — the fields a named "
                         "human confirmed — never from the working draft.")
# Eligibility is a determination about a PATIENT (an age against a package's band), not a fact the
# document states, so it is not among the committed rows this module reads and cannot be recomputed
# here without reaching for the registry — which would make a claim draft depend on identity data.
# `billing.py` raises a caveat when `eligibility_flags` are set; this module cannot see them.
# Saying so is the honest option. Silently omitting it would let a claim for a patient outside a
# package's age band read as though eligibility had been checked and passed.
ELIGIBILITY_NOT_RECHECKED_CAVEAT = (
    "Eligibility is NOT re-evaluated here. Any eligibility flag on the intake card — an age "
    "outside the package band, an unresolved conflict — still applies to this claim and must be "
    "cleared before it is submitted.")

# The field names this module can emit. A tuple so the "missing implies unresolved" validator and
# the payload serialiser cannot fall out of step with each other.
CLAIM_FIELDS = ("submit_to", "tpa_claim_required", "issuer_code", "package_code", "patient_id")


class ClaimError(RuntimeError):
    """A claim draft could not be produced. Subclasses map to stable refusal codes."""


class NothingCommitted(ClaimError):
    """No confirmed evidence exists for this intake, so there is nothing to draft from."""


class NoPayerToClaimFrom(ClaimError):
    """No third party to submit to — a walk-in, or an issuer nobody confirmed."""


class ConflictingSelections(ClaimError):
    """The live rows for this intake name more than one selected patient. A human decides."""


class AmbiguousPackageRule(ClaimError):
    """No package code was confirmed, and this issuer has more than one rule it could be."""


class ClaimValue(BaseModel):
    """One value on the claim, and where it came from.

    ``pointer`` is required. That is the whole design: a claim field is unconstructible without
    provenance, so "emit it anyway, add the citation later" is not a shortcut anyone can take.
    """

    model_config = ConfigDict(frozen=True)

    value: Any
    pointer: SourcePointer
    confirmed_by: Optional[str] = None      # the staff id on the ledger row this came from
    confirmed_at: Optional[str] = None

    def to_payload(self) -> dict:
        return {"value": self.value,
                "pointer": self.pointer.model_dump(mode="json", exclude_none=True),
                "confirmed_by": self.confirmed_by,
                "confirmed_at": self.confirmed_at}


class ClaimLine(BaseModel):
    """One test on the claim, pointing at the line of the document that ordered it."""

    model_config = ConfigDict(frozen=True)

    as_printed: str
    source_field: str
    pointer: SourcePointer
    canonical: Optional[str] = None
    in_package: bool = False
    list_price_sgd: Optional[str] = None
    # A line carries more than one claim, so it needs more than one citation. `pointer` grounds the
    # PRINTED TEXT and nothing else; review's phrasing was that a document span "could not be
    # invalidated" by changing the price beside it, which is true and is a constraint-2 gap on the
    # one artefact where the number is the point. These name the config rows the other two values
    # were read from.
    price_pointer: Optional[SourcePointer] = None       # the test_catalogue row, when priced
    coverage_pointer: Optional[SourcePointer] = None    # the rule row that decided it is covered

    def to_payload(self) -> dict:
        return {"as_printed": self.as_printed, "canonical": self.canonical,
                "in_package": self.in_package, "list_price_sgd": self.list_price_sgd,
                "source_field": self.source_field,
                "pointer": self.pointer.model_dump(mode="json", exclude_none=True),
                "price_pointer": (self.price_pointer.model_dump(mode="json", exclude_none=True)
                                  if self.price_pointer else None),
                "coverage_pointer": (self.coverage_pointer.model_dump(mode="json",
                                                                      exclude_none=True)
                                     if self.coverage_pointer else None)}

    @model_validator(mode="after")
    def _a_price_names_where_it_came_from(self) -> "ClaimLine":
        """A number on a claim without a citation is the thing this whole architecture exists to
        prevent — more so here than anywhere else, because this one is what gets billed."""
        if self.list_price_sgd is not None and self.price_pointer is None:
            raise ValueError(
                f"ClaimLine REFUSED: {self.as_printed!r} carries a price with no pointer to the "
                f"catalogue row it was read from")
        return self


class ExcludedLine(BaseModel):
    """A test the document ordered that this claim does NOT ask the payer for, and why.

    Present because the alternative — filtering silently — reads to staff as "the claim covers
    everything on the letter". It does not, and the difference is money the patient is asked for at
    the counter.
    """

    model_config = ConfigDict(frozen=True)

    as_printed: str
    source_field: str
    reason: str
    pointer: SourcePointer

    def to_payload(self) -> dict:
        return {"as_printed": self.as_printed, "source_field": self.source_field,
                "reason": self.reason,
                "pointer": self.pointer.model_dump(mode="json", exclude_none=True)}


class ClaimDraft(BaseModel):
    """The claim, as far as committed evidence supports it. Never a submission."""

    model_config = ConfigDict(frozen=True)

    intake_id: str
    status: Literal["draft"] = "draft"
    submit_to: Optional[ClaimValue] = None
    tpa_claim_required: Optional[ClaimValue] = None
    issuer_code: Optional[ClaimValue] = None
    package_code: Optional[ClaimValue] = None
    patient_id: Optional[ClaimValue] = None
    line_items: list[ClaimLine] = Field(default_factory=list)
    excluded_from_claim: list[ExcludedLine] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _absent_fields_are_declared(self) -> "ClaimDraft":
        """A blank field must say it is blank.

        The same argument as ``IntakeRecord.unresolved`` and ``CoverageDetermination.status``: an
        honesty channel that nothing checks is decoration. Without this, a consumer rendering only
        the populated fields would show a confident-looking claim and never learn that the payer
        was never established.
        """
        missing = [f for f in CLAIM_FIELDS if getattr(self, f) is None]
        undeclared = [f for f in missing if f not in self.unresolved]
        if undeclared:
            raise ValueError(
                f"ClaimDraft REFUSED: field(s) {undeclared} are absent but not named in "
                f"`unresolved` — a claim must declare what it could not establish")
        overclaimed = [f for f in self.unresolved
                       if f in CLAIM_FIELDS and getattr(self, f) is not None]
        if overclaimed:
            raise ValueError(
                f"ClaimDraft REFUSED: field(s) {overclaimed} are named in `unresolved` but carry "
                f"a value")
        return self

    def to_payload(self) -> dict:
        """The wire shape. ``None`` fields are kept, not dropped: a missing key reads as an
        oversight, an explicit ``null`` beside ``unresolved`` reads as an answer."""
        return {
            "intake_id": self.intake_id,
            "status": self.status,
            "submit_to": self.submit_to.to_payload() if self.submit_to else None,
            "tpa_claim_required": (self.tpa_claim_required.to_payload()
                                   if self.tpa_claim_required else None),
            "issuer_code": self.issuer_code.to_payload() if self.issuer_code else None,
            "package_code": self.package_code.to_payload() if self.package_code else None,
            "patient_id": self.patient_id.to_payload() if self.patient_id else None,
            "line_items": [line.to_payload() for line in self.line_items],
            "excluded_from_claim": [x.to_payload() for x in self.excluded_from_claim],
            "unresolved": list(self.unresolved),
            "caveats": list(self.caveats),
        }


# --- internals ---------------------------------------------------------------------


def _yaml_ptr(table: str, column: str, row_id: object) -> SourcePointer:
    """A ``table_cell`` pointer into ``coverage_rules.yaml`` — a list of rows with named fields is
    a table in every sense that matters, and it resolves to a place a human can open."""
    return SourcePointer(source_id=RULES_SOURCE_ID, kind="table_cell",
                         table=table, column=column, row_id=str(row_id))


def _row_pointer(row: dict) -> SourcePointer:
    return SourcePointer.model_validate_json(row["source_pointer_json"])


def _row_value(row: dict) -> Any:
    return json.loads(row["value_json"])


def _committed(rows: dict[str, dict], path: str) -> Optional[ClaimValue]:
    """The committed value at ``path``, or ``None`` if no human confirmed it."""
    row = rows.get(path)
    if row is None:
        return None
    value = _row_value(row)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return ClaimValue(value=value, pointer=_row_pointer(row),
                      confirmed_by=row["confirmed_by"], confirmed_at=row["confirmed_at"])


def _selected_patient(rows: dict[str, dict], intake_id: str) -> Optional[ClaimValue]:
    """Who a human selected, cited to the ledger row that records the selection.

    Deliberately NOT pointed at a span of the document. The document carries a *claimed* name; it
    does not say which registry patient this is. The only thing that says so is a person who
    checked an NRIC against a face and attested to it, and the pointer names that row.

    **Refuses when the live rows disagree about who this is.** The ledger is append-only, so a
    correction is a NEW row that supersedes one field — and `current()` merges the latest row per
    field path across every commit batch. Nothing stopped those rows coming from different
    selections: an intake committed for patient A, later corrected with a row selecting patient B,
    would yield B's identity attached to A's package and A's tests. Taking the newest and moving on
    would be the machine resolving a contradiction about *who someone is*, which is the one thing
    it must never do (CLAUDE.md §Hard constraints 1). It stops and asks for a human instead.
    """
    selections = {r["selected_patient_id"] for r in rows.values() if r["selected_patient_id"]}
    if len(selections) > 1:
        raise ConflictingSelections(
            f"the committed evidence for intake {intake_id!r} names more than one selected "
            f"patient ({', '.join(sorted(selections))}). A claim cannot be drafted until a human "
            f"establishes which record this encounter belongs to; the system does not choose "
            f"between them.")
    row = max((r for r in rows.values() if r["selected_patient_id"]),
              key=lambda r: r["rowid_pk"], default=None)
    if row is None:
        return None
    return ClaimValue(
        value=row["selected_patient_id"],
        pointer=SourcePointer(
            source_id=f"evidence_ledger:{intake_id}", kind="table_cell",
            table="evidence", column="selected_patient_id", row_id=str(row["rowid_pk"])),
        confirmed_by=row["confirmed_by"], confirmed_at=row["confirmed_at"])


_INDEXED_PATH = re.compile(r"^(.*)\[(\d+)\]$")


def _path_order(path: str) -> tuple[str, int]:
    """Sort key that reads ``[2]`` as two, not as the string "2".

    A plain lexicographic sort puts ``requested_tests[10]`` before ``requested_tests[2]``, so a
    document listing ten or more tests produced a claim whose lines were out of the order they were
    printed in — which is the order the person checking it against the letter reads them in.
    """
    match = _INDEXED_PATH.match(path)
    return (match.group(1), int(match.group(2))) if match else (path, -1)


def _test_rows(rows: dict[str, dict]) -> list[tuple[str, dict]]:
    """Committed test rows, in the order they were printed on the document."""
    return sorted(((p, r) for p, r in rows.items() if p.startswith(TEST_PATH_PREFIXES)),
                  key=lambda pr: _path_order(pr[0]))


def build_claim_draft(intake_id: str,
                      ledger: EvidenceLedger,
                      *,
                      table: Optional[CoverageRules] = None) -> ClaimDraft:
    """Draft the post-visit claim from what a human committed for ``intake_id``.

    Raises ``NothingCommitted`` when the ledger holds nothing for this intake, and
    ``NoPayerToClaimFrom`` when no issuer was confirmed — a walk-in self-pay has no third party,
    and inventing one would put a bill in front of a company that never agreed to it.
    """
    table = table or rules()
    rows = ledger.current(intake_id)
    if not rows:
        raise NothingCommitted(
            f"no confirmed evidence for intake {intake_id!r}: a claim draft is built from the "
            f"ledger, and nothing has been committed")

    issuer_code = _committed(rows, ISSUER_CODE_PATH)
    package_code = _committed(rows, PACKAGE_CODE_PATH)
    patient_id = _selected_patient(rows, intake_id)

    if issuer_code is None:
        raise NoPayerToClaimFrom(
            f"no issuer code has been confirmed for intake {intake_id!r}, so there is no third "
            f"party to submit a claim to")

    issuer = table.issuer(str(issuer_code.value))
    package = table.package(str(issuer_code.value),
                            str(package_code.value) if package_code else None)

    if package_code is None and package is not None:
        # `table.package(issuer, None)` falls back to the issuer's document-defined rule. For a
        # referral letter that is exactly right — the letter names the tests, so there is no package
        # code to confirm. But an ABSENCE is not evidence: the same absence occurs when a form's
        # package box was never committed, and picking a rule on that basis would be a guess wearing
        # a caveat (CLAUDE.md §Hard constraints 2).
        #
        # The distinction that makes it safe: if the document-defined rule is the issuer's ONLY
        # rule, no other rule could have been meant. If the issuer ALSO has coded packages, the
        # absence is genuinely ambiguous and the system stops. No issuer in the current config has
        # both, so this refuses nothing today — it makes the hazard unreachable rather than
        # documented.
        siblings = table.packages_for(str(issuer_code.value))
        coded = [p for p in siblings if p.package_code]
        if coded:
            raise AmbiguousPackageRule(
                f"no package code was confirmed for intake {intake_id!r}, and issuer "
                f"{issuer_code.value!r} has {len(coded)} coded package(s) as well as a "
                f"document-defined rule. Which one applies cannot be established from the "
                f"committed evidence, and the system does not choose.")

    caveats = [COMMITTED_ONLY_CAVEAT, DRAFT_CAVEAT, SYNTHETIC_PRICE_CAVEAT,
               ELIGIBILITY_NOT_RECHECKED_CAVEAT]
    unresolved: list[str] = []

    # -- the payer -------------------------------------------------------------
    submit_to: Optional[ClaimValue] = None
    if package is None:
        unresolved.append("submit_to")
        caveats.append(
            f"no rule in coverage_rules.yaml matches issuer {issuer_code.value!r} with the "
            f"committed package code, so the payer could not be established from the confirmed "
            f"evidence — establish it by hand before submitting")
    else:
        payer = package.billing.covered_by
        if payer is BillingParty.PATIENT:
            raise NoPayerToClaimFrom(
                f"intake {intake_id!r} is billed to the patient under rule {package.id!r} — there "
                f"is no third party to submit a claim to")
        cited = [issuer_code.pointer]
        if package_code is not None:
            cited.append(package_code.pointer)
        cited.append(_yaml_ptr("packages", "billing.covered_by", package.id))
        submit_to = ClaimValue(
            value=payer.value,
            pointer=SourcePointer(
                source_id=RULES_SOURCE_ID, kind="derived",
                derivation=(f"coverage_rules.yaml packages[{package.id}].billing.covered_by, "
                            f"selected by the committed issuer code"
                            + (" and package code" if package_code else "")),
                derived_from=cited))
        if package_code is None and package.tests_from_document:
            # The hazard worth naming out loud: with no committed package code, `table.package`
            # falls back to the issuer's document-defined rule. For a referral letter that is
            # exactly right. For a form whose package box was never ticked it would be a rule
            # nobody chose — so the claim says which one it used rather than letting it pass as
            # settled.
            caveats.append(
                f"no package code was confirmed for this intake, so the payer above comes from "
                f"the issuer's document-defined rule {package.id!r}: the tests printed on the "
                f"document are the authorised scope")

    # -- whether the payer wants a TPA submission at all ------------------------
    tpa_required: Optional[ClaimValue] = None
    if issuer is None:
        unresolved.append("tpa_claim_required")
        caveats.append(
            f"issuer {issuer_code.value!r} is not in coverage_rules.yaml, so whether it requires "
            f"a TPA submission is unknown")
    else:
        tpa_required = ClaimValue(
            value=bool(issuer.tpa_claim_required),
            pointer=SourcePointer(
                source_id=RULES_SOURCE_ID, kind="derived",
                derivation=(f"coverage_rules.yaml issuers[{issuer.code}].tpa_claim_required, "
                            f"selected by the committed issuer code"),
                derived_from=[issuer_code.pointer,
                              _yaml_ptr("issuers", "tpa_claim_required", issuer.code)]))

    if package_code is None:
        unresolved.append("package_code")
    if patient_id is None:
        unresolved.append("patient_id")
        caveats.append(
            "no candidate selection is recorded on the committed rows, so this claim names no "
            "patient — it cannot be submitted as it stands")

    # -- the lines -------------------------------------------------------------
    line_items: list[ClaimLine] = []
    excluded: list[ExcludedLine] = []
    seen: set[str] = set()
    for path, row in _test_rows(rows):
        as_printed = str(_row_value(row))
        match = table.match_test(as_printed)
        canonical = match.canonical if match.matched else None
        # One line per test. A test printed twice — in a table and again in prose, or under two
        # aliases — would otherwise be claimed, and paid, twice.
        key = canonical or f"as_printed:{as_printed.strip().lower()}"
        pointer = _row_pointer(row)
        if key in seen:
            # A test printed twice — in a table and again in prose, or under two aliases — must not
            # be claimed, and paid, twice. But it must not VANISH either: two committed rows can
            # equally be two genuinely separate authorisations (two doses, two specimens), and
            # dropping the second silently would under-claim without anyone being told. It is
            # surfaced for a human to decide, which is the same instinct as `unmatched_tests`.
            excluded.append(ExcludedLine(
                as_printed=as_printed, source_field=path, pointer=pointer,
                reason=(f"already claimed once from an earlier line on this document. If this is a "
                        f"second, separately authorised occurrence rather than the same test "
                        f"printed twice, add it to the claim by hand")))
            continue
        seen.add(key)
        if canonical is None:
            excluded.append(ExcludedLine(
                as_printed=as_printed, source_field=path, pointer=pointer,
                reason=("not in the test catalogue: neither its price nor its coverage can be "
                        "established here, so it is left for staff to enter by hand")))
            continue
        in_package = bool(package and canonical in package.includes_tests)
        if package is None or not covered_by_rule(package, in_package):
            excluded.append(ExcludedLine(
                as_printed=as_printed, source_field=path, pointer=pointer,
                reason=(f"authorised by the document but not included in package "
                        f"{package.package_code or package.id}" if package is not None
                        else "no package rule matched, so coverage is unestablished")))
            continue
        entry = table.test(canonical)
        price = entry.list_price_sgd if entry is not None else None
        line_items.append(ClaimLine(
            as_printed=as_printed, canonical=canonical, in_package=in_package,
            list_price_sgd=str(price) if price is not None else None,
            price_pointer=(_yaml_ptr("test_catalogue", "list_price_sgd", canonical)
                           if price is not None else None),
            coverage_pointer=_yaml_ptr("packages", "billing.covered_by", package.id),
            source_field=path, pointer=pointer))

    # -- what the PACKAGE includes but the document never printed ---------------
    # A screening package authorises its whole contents; an authorisation form ticks the package
    # and lists only the extras. Claiming just the printed lines would submit a PEE226 patient's
    # claim with nothing on it but the add-ons the package does NOT cover.
    #
    # These are grounded, but not on the document: their provenance is the config row the
    # committed package code selected, so they carry a `table_cell` pointer into
    # coverage_rules.yaml. `AuthorisedTest` already draws exactly this distinction.
    #
    # Gated on a COMMITTED package code. Reading a package's contents onto the claim when nobody
    # confirmed which package this is would be the same fabrication in a different costume.
    if package is not None and package_code is not None:
        for canonical in package.includes_tests:
            if canonical in seen:
                continue
            seen.add(canonical)
            entry = table.test(canonical)
            price = entry.list_price_sgd if entry is not None else None
            line_items.append(ClaimLine(
                as_printed=table.display_name(canonical),
                canonical=canonical,
                in_package=True,
                list_price_sgd=str(price) if price is not None else None,
                price_pointer=(_yaml_ptr("test_catalogue", "list_price_sgd", canonical)
                               if price is not None else None),
                coverage_pointer=_yaml_ptr("packages", "includes_tests", package.id),
                source_field=f"coverage_rules.yaml:packages[{package.id}].includes_tests",
                pointer=_yaml_ptr("packages", "includes_tests", package.id)))

    if not line_items:
        caveats.append(
            "no committed test on this document is claimable under the matched rule; there is "
            "nothing to submit until that is resolved")
    if excluded:
        caveats.append(
            f"{len(excluded)} confirmed test(s) are NOT on this claim — see `excluded_from_claim`. "
            f"They are not covered by the payer and the patient may be asked for them")

    return ClaimDraft(
        intake_id=intake_id,
        submit_to=submit_to,
        tpa_claim_required=tpa_required,
        issuer_code=issuer_code,
        package_code=package_code,
        patient_id=patient_id,
        line_items=line_items,
        excluded_from_claim=excluded,
        unresolved=unresolved,
        caveats=caveats)
