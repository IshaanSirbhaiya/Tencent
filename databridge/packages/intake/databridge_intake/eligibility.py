"""Coverage determination — a pure lookup against the active pack's ``coverage_rules.yaml``.

**No issuer is named in this file.** Everything it knows about insurers, packages, age bands and
tests it asks ``rules.py`` for, and ``rules.py`` reads YAML. That is what makes "adding an insurer
is a config change" true rather than aspirational, and ``test_no_issuer_branching`` enforces it by
grepping this package for every code in the table (CLAUDE.md §Hard constraints 3).

Two behaviours are worth reading the code for, because both are cases where the honest answer is
less impressive than the confident one:

**Undetermined is a real outcome.** A TPA authorisation form that ticks no test code (chit 8) does
not entitle the patient to anything this table can name. The determination comes back
``UNDETERMINED`` with the reason spelled out, and ``package_code`` stays ``None``. Guessing the
issuer's most common package would be right most of the time and catastrophic the rest.

**An age band is checked against whoever the patient might be.** Eligibility depends on age, age
comes from the registry, and identity is not resolved until a human selects a candidate — so at
determination time there may be several possible ages. Rather than skip the check (silent) or pick
the top candidate (a de-facto identity decision), the band is evaluated against *every* candidate:
a flag is raised only when it holds for all of them, and when candidates disagree that fact is
itself reported. Chit 5 is the case this exists for — two possible patients, aged 21 and 30,
against a voucher ticked for a 40-59 package. Neither could be eligible, so the conflict is
certain even though the identity is not.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from databridge_core import SourcePointer

from .history import ClinicHistory, NoHistory
from .models import (AuthorisedTest, CoverageDetermination, CoverageStatus, DocumentType,
                     IntakeRecord, PatientRecord, QuestionnaireKind, ResolutionCandidate)
from .clock import resolve_today
from .rules import CoverageRules, Package, TestMatch, rules

# Flags are closed-vocabulary strings so the UI can style them and the eval suite can assert on
# them without matching prose.
FLAG_AGE_OUTSIDE_BAND = "age_outside_package_band"
FLAG_AGE_PENDING_IDENTITY = "age_eligibility_pending_identity"
FLAG_AGE_UNKNOWN = "age_not_checked_no_patient"
FLAG_UNMATCHED_TESTS = "tests_not_in_catalogue"
FLAG_AMBIGUOUS_TESTS = "tests_ambiguous_in_catalogue"
FLAG_NO_TESTS_ON_DOCUMENT = "no_tests_stated_on_document"
# Raised from a GRAPH TRAVERSAL, not from this document. See `_check_other_documents`.
FLAG_CONFLICTING_DOCUMENT = "conflicting_document_on_file"


def _value(gv) -> Optional[str]:
    return str(gv.value) if gv is not None and gv.value is not None else None




def _rule_pointer(package: Package) -> SourcePointer:
    """A ``table_cell`` pointer at the YAML row that lists a package's contents."""
    return SourcePointer(source_id="coverage_rules.yaml", kind="table_cell",
                         table="packages", column="includes_tests", row_id=package.id)


def _authorised_from_package(table: CoverageRules, package: Package) -> dict[str, AuthorisedTest]:
    """The package's own contents, keyed by canonical.

    These are not quoted from the document — they are what the rule says the package contains — so
    they must NOT carry a document span. They carry a ``table_cell`` pointer at the YAML row
    instead: a real, resolvable citation into a file a human can open. Emitting them with no
    pointer at all (the first draft) left a billable line on the summary card whose only
    provenance was a rule id printed elsewhere on the page.
    """
    out: dict[str, AuthorisedTest] = {}
    rule_ptr = _rule_pointer(package)
    for canonical in package.includes_tests:
        entry = table.test(canonical)
        out[canonical] = AuthorisedTest(
            as_printed=table.display_name(canonical),
            canonical=canonical,
            in_package=True,
            list_price_sgd=entry.list_price_sgd if entry else None,
            pointer=rule_ptr)
    return out


def _merge_printed(table: CoverageRules,
                   into: dict[str, AuthorisedTest],
                   unkeyed: list[AuthorisedTest],
                   printed: Sequence,
                   field_prefix: str) -> tuple[list[str], list[str]]:
    """Fold the tests actually printed on the document into the package contents.

    A printed test that IS in the package upgrades the existing entry with its printed wording and
    its source pointer, so the front desk can click it through to the line of the letter. A printed
    test that is NOT in the package is carried with ``in_package=False`` — an add-on the document
    ordered beyond the package (chit 7's vaccination), which is exactly the thing that must not be
    quietly folded into "covered".

    ``into`` is keyed by canonical and holds everything that mapped to the catalogue; ``unkeyed``
    holds what did not (no canonical to key on). Keying every matched test — package or add-on —
    through one dict is what makes this idempotent across the two calls (``requested_tests`` then
    ``add_ons``): a test printed in both lists, or twice under different aliases, updates one entry
    instead of producing two AuthorisedTests for the same canonical and billing it twice.
    """
    unmatched: list[str] = []
    ambiguous: list[str] = []
    for i, gv in enumerate(printed):
        as_printed = str(gv.value)
        match: TestMatch = table.match_test(as_printed)
        source_field = f"{field_prefix}[{i}]"
        if not match.matched:
            # Surfaced, never invented into the catalogue and never dropped.
            (ambiguous if match.ambiguous else unmatched).append(as_printed)
            unkeyed.append(AuthorisedTest(
                as_printed=as_printed, canonical=None, in_package=False,
                source_field=source_field, pointer=gv.pointer,
                ambiguous_with=match.ambiguous_with))
            continue
        entry = table.test(match.canonical)
        price = entry.list_price_sgd if entry else None
        existing = into.get(match.canonical)
        if existing is not None:
            into[match.canonical] = existing.model_copy(update={
                "as_printed": as_printed, "source_field": source_field, "pointer": gv.pointer})
        else:
            into[match.canonical] = AuthorisedTest(
                as_printed=as_printed, canonical=match.canonical, in_package=False,
                list_price_sgd=price, source_field=source_field, pointer=gv.pointer)
    return unmatched, ambiguous


def _check_age_band(package: Package,
                    patient: Optional[PatientRecord],
                    candidates: Sequence[ResolutionCandidate],
                    today: dt.date) -> tuple[list[str], list[str]]:
    """Evaluate the package's age band. Returns ``(flags, reasoning)``.

    See the module docstring for why this looks at every candidate rather than the best one.
    """
    band = package.eligibility
    if band.age_min is None and band.age_max is None:
        return [], [f"package {package.package_code or package.id} has no age restriction"]

    described = band.describe()
    if patient is not None:
        age = patient.age_at(today)
        if band.covers_age(age):
            return [], [f"selected patient is {age}; package requires {described} — eligible"]
        return ([FLAG_AGE_OUTSIDE_BAND],
                [f"selected patient is {age}; package requires {described} — NOT eligible"])

    if not candidates:
        return ([FLAG_AGE_UNKNOWN],
                [f"package requires {described}; no registry patient is selected, so age "
                 f"eligibility has not been checked"])

    ages = {c.record.patient_id: c.record.age_at(today) for c in candidates}
    eligible = {pid for pid, age in ages.items() if band.covers_age(age)}
    listed = ", ".join(str(a) for a in sorted(ages.values()))
    if not eligible:
        # True whichever candidate turns out to be the patient — assertable without deciding
        # identity, which is the only kind of pre-selection conclusion this system may draw.
        return ([FLAG_AGE_OUTSIDE_BAND],
                [f"package requires {described}; every candidate patient (ages {listed}) falls "
                 f"outside that band, so this package cannot apply to any of them"])
    if len(eligible) < len(ages):
        return ([FLAG_AGE_PENDING_IDENTITY],
                [f"package requires {described}; candidate ages are {listed} — whether the band "
                 f"is met depends on which patient staff select"])
    return [], [f"package requires {described}; all candidate patients (ages {listed}) are "
                f"within it"]


# How recent another document must be to count as a live conflict. An insurer-A referral from last
# year and a legitimate insurer-B referral today are not a contradiction — they are two visits. Only
# documents inside this window are close enough that staff must establish which applies *now*.
#
# A window, not "any disagreement ever": flagging every historical difference would put a permanent
# warning on every returning patient, which is how a flag stops being read.
CONFLICT_WINDOW_DAYS = 120


def _check_other_documents(record: IntakeRecord,
                           candidates: Sequence[ResolutionCandidate],
                           issuer_code: Optional[str],
                           package_code: Optional[str],
                           history,
                           today: dt.date) -> tuple[list[str], list[str], list]:
    """Is there another RECENT document on file for a candidate that disagrees with this one?

    **This is the one determination in the pipeline that comes from a graph traversal.** Reading one
    document at a time, the pipeline structurally cannot see that the same patient also has a
    referral from a different insurer or for a different package — and that is a real front-desk
    problem, because whichever one staff act on may be the wrong one.

    Per CANDIDATE, never per "the patient": identity is unresolved here, so the honest framing is
    "one of the people this document might be about also has another document on file".

    Returns ``(flags, reasoning, findings)``. The findings are typed and carry the traversal's
    pointers — reducing them to prose would discard the only evidence the claim has.

    Fails open. Any error from the history port is swallowed: an enrichment must never be able to
    stop an intake being read.
    """
    from .models import HistoryFinding

    flags: list[str] = []
    reasoning: list[str] = []
    findings: list = []
    this_document = record.document.document_id
    cutoff = dt.datetime.combine(
        today - dt.timedelta(days=CONFLICT_WINDOW_DAYS), dt.time.min, tzinfo=dt.timezone.utc)

    def _same_code(a: Optional[str], b: Optional[str]) -> bool:
        """Compare codes the way the rules table does.

        The traversal is handed the RAW extracted values so it still works when the issuer cannot be
        resolved, but the graph's values are canonical. Comparing them literally made a chit printing
        "mrdeb" or " WELL2 " disagree with itself and told staff two documents named different
        insurers when they named the same one. `rules.py` matches on `.strip().upper()`; so does this.
        """
        return (a or "").strip().upper() == (b or "").strip().upper()

    for candidate in candidates:
        # The WHOLE per-candidate body is inside the guard, not just the port call. Reading a
        # `PriorIntake` can raise too — `seen_at` has no timezone contract in the port, so a
        # non-graph implementation returning a naive datetime made the comparison below throw
        # `TypeError`, which escaped `determine_coverage` and turned into an EXTRACTION_FAILED
        # refusal. An enrichment must never be able to stop an intake being read.
        try:
            others = history.documents_naming(candidate.patient_id,
                                              exclude_document_id=this_document)
            for other in others:
                seen_at = other.seen_at
                if seen_at is not None and seen_at.tzinfo is None:
                    seen_at = seen_at.replace(tzinfo=dt.timezone.utc)
                if seen_at is not None and seen_at < cutoff:
                    continue                # old enough to be a separate episode, not a conflict
                differs = []
                if (other.issuer_code and issuer_code
                        and not _same_code(other.issuer_code, issuer_code)):
                    differs.append(f"issuer {other.issuer_code} rather than {issuer_code}")
                if (other.package_code and package_code
                        and not _same_code(other.package_code, package_code)):
                    differs.append(f"package {other.package_code} rather than {package_code}")
                if not differs:
                    continue

                # `candidate_count` qualifies the claim. A prior document that matched three
                # patients did not identify this one — saying it "also names" them overstates
                # what the graph actually knows. `None` (a history implementation that never
                # recorded a count, e.g. a FOR_PATIENT edge from `extract_graph`'s brain_ingest
                # path, which builds edges with empty attrs) is FALSY in Python, so `other.
                # candidate_count and ... > 1` used to fall through to the unqualified "also
                # names" wording exactly as if the count were confirmed at 1 — treating "we never
                # counted" as "we counted and it was exactly one". An unknown count must produce
                # the MORE cautious wording, not the less (CLAUDE.md §Hard constraints 1): it is
                # never safe to assert a resolved link when the resolution was never recorded.
                if other.candidate_count is None:
                    names = (f"may also concern {candidate.record.full_name.value} "
                             f"(candidate count on that document is unknown)")
                elif other.candidate_count > 1:
                    names = (f"may also concern {candidate.record.full_name.value} "
                             f"(1 of {other.candidate_count} candidates on that document)")
                else:
                    names = f"also names {candidate.record.full_name.value}"
                message = f"{other.document_id} {names}: {'; '.join(differs)}"
                reasoning.append(message)
                findings.append(HistoryFinding(
                    kind=FLAG_CONFLICTING_DOCUMENT, message=message,
                    document_id=other.document_id, patient_id=candidate.patient_id,
                    sources=other.sources))
        except Exception:                                            # noqa: BLE001
            # A broken, empty or misbehaving history degrades the card; it does not block the
            # counter. Fail open on enrichment; never on safety.
            continue

    if findings:
        flags.append(FLAG_CONFLICTING_DOCUMENT)
        reasoning.insert(
            len(reasoning) - len(findings),
            f"another document on file (within {CONFLICT_WINDOW_DAYS} days) disagrees with this "
            f"one — staff should establish which applies before the visit")
    return flags, reasoning, findings


def _undetermined(reasoning: list[str], issuer_code: Optional[str] = None,
                  findings: Optional[list] = None) -> CoverageDetermination:
    """An undetermined coverage still carries graph findings.

    The conflict check used to sit after the early returns, so an unknown issuer or an unmatched
    package code skipped it entirely — and "we could not determine this document AND there is a
    conflicting one on file" is strictly more useful to staff than either half.
    """
    return CoverageDetermination(
        issuer_code=issuer_code, status=CoverageStatus.UNDETERMINED, reasoning=reasoning,
        eligibility_flags=([FLAG_CONFLICTING_DOCUMENT] if findings else []),
        history_findings=findings or [])


def determine_coverage(record: IntakeRecord,
                       *,
                       selected_patient: Optional[PatientRecord] = None,
                       candidates: Sequence[ResolutionCandidate] = (),
                       today: Optional[dt.date] = None,
                       table: Optional[CoverageRules] = None,
                       history: Optional[ClinicHistory] = None) -> CoverageDetermination:
    """What the config table says this document entitles the patient to.

    **Caller contract.** ``selected_patient`` means *a human has picked this person after checking
    their identity in person* — it is the patient behind a ``ConfirmationState`` with an
    attestation, and nothing else. Passing ``resolve(...).top.record`` here would be a bug with
    real consequences: the top candidate's age would silently produce a settled eligibility answer
    where the honest one is ``age_eligibility_pending_identity``. The parameter is named for the
    act, not for the data, so a call site that violates this reads wrong.

    Enforcement lives at the confirm gate (PLAN.md §5 Phase 4), which is the only place that can
    tell selection from ranking. Here the safe default does the work: with no selection, the age
    band is evaluated across *all* ``candidates`` and a flag is raised only when it holds for every
    one of them. ``candidates`` alone therefore never constitutes a selection.
    """
    table = table or rules()
    today = resolve_today(today)
    # NoHistory by default: the pipeline must produce the same card with no graph attached.
    history = history if history is not None else NoHistory()
    reasoning: list[str] = []

    doc_type = record.document_type.value
    if doc_type == DocumentType.NO_DOCUMENT.value:
        # The walk-in path is a rule in the table like any other, not a branch in code.
        sp = table.self_pay
        reasoning.append(f"no referring document — rule {sp.id!r} ({sp.name}) applies; "
                         f"billed to {sp.billing.covered_by.value}")
        return CoverageDetermination(
            matched_rule_id=sp.id, package_name=sp.name, questionnaire=sp.questionnaire,
            status=CoverageStatus.DETERMINED, reasoning=reasoning)

    issuer_code = _value(record.issuer.issuer_code)
    raw_package_code = _value(record.authorisation.package_code)
    # The graph traversal runs FIRST, on the RAW values, so it still reports a conflict when the
    # issuer or package cannot be resolved against the config table.
    conflict_flags, conflict_reasoning, conflict_findings = _check_other_documents(
        record, candidates, issuer_code, raw_package_code, history, today)

    if not issuer_code:
        return _undetermined(
            ["the document does not carry an issuer code, so no coverage rule can be selected"]
            + conflict_reasoning, findings=conflict_findings)

    issuer = table.issuer(issuer_code)
    if issuer is None:
        return _undetermined(
            [f"issuer code {issuer_code!r} is not in the coverage table — add it to "
             f"coverage_rules.yaml; it is not something this system may infer"]
            + conflict_reasoning,
            issuer_code=issuer_code, findings=conflict_findings)
    reasoning.append(f"issuer {issuer_code} resolves to {issuer.name} ({issuer.kind.value}); "
                     f"billed to {issuer.billing_party.value}")

    package_code = raw_package_code
    package = table.package(issuer_code, package_code)
    if package is None:
        if package_code:
            return _undetermined(
                reasoning + [f"the document states package {package_code!r}, but the coverage "
                             f"table has no such package for issuer {issuer_code}"]
                + conflict_reasoning,
                issuer_code=issuer_code, findings=conflict_findings)
        return _undetermined(
            reasoning + [f"the document states no package code, and issuer {issuer_code} has no "
                         f"document-defined rule — the package cannot be determined from this "
                         f"document and must be confirmed with the issuer"]
            + conflict_reasoning,
            issuer_code=issuer_code, findings=conflict_findings)

    if package_code:
        reasoning.append(f"package {package_code} matched rule {package.id!r} ({package.name})")
    else:
        reasoning.append(f"no package code on the document; rule {package.id!r} ({package.name}) "
                         f"takes the authorised tests from the document itself")

    by_canonical = _authorised_from_package(table, package)
    unkeyed: list[AuthorisedTest] = []
    unmatched, ambiguous = _merge_printed(
        table, by_canonical, unkeyed, record.authorisation.requested_tests,
        "authorisation.requested_tests")
    more_unmatched, more_ambiguous = _merge_printed(
        table, by_canonical, unkeyed, record.authorisation.add_ons, "authorisation.add_ons")
    unmatched += more_unmatched
    ambiguous += more_ambiguous

    authorised = list(by_canonical.values()) + unkeyed
    printed_count = len(record.authorisation.requested_tests) + len(record.authorisation.add_ons)
    from_package = sum(1 for t in by_canonical.values() if t.in_package)
    reasoning.append(
        f"{printed_count} test(s) printed on the document; {from_package} from the package "
        f"definition; {len(unmatched) + len(ambiguous)} could not be matched to the catalogue")

    flags, age_reasoning = _check_age_band(package, selected_patient, candidates, today)
    reasoning += age_reasoning

    # Fold in the graph traversal run at the top of this function. Everything else on the
    # determination is derived from THIS document and the config table; these are the only findings
    # that come from what the clinic already knows.
    flags += conflict_flags
    reasoning += conflict_reasoning

    if package.tests_from_document and not authorised:
        # This rule's whole definition is "the tests are whatever the document lists" — so a
        # document that lists none has not established a scope. Reporting that as `determined`
        # would put an empty, confident-looking test list on the summary card (chit 9, an HR email
        # that asks the clinic to arrange a medical without naming one).
        flags.append(FLAG_NO_TESTS_ON_DOCUMENT)
        reasoning.append(
            f"rule {package.id!r} takes its tests from the document, but this document names none "
            f"— the authorised scope must be confirmed with the issuer before the visit")

    if unmatched:
        flags.append(FLAG_UNMATCHED_TESTS)
        reasoning.append(f"not in the test catalogue, shown to staff rather than dropped: "
                         f"{', '.join(repr(u) for u in unmatched)}")
    if ambiguous:
        flags.append(FLAG_AMBIGUOUS_TESTS)
        reasoning.append(f"matched more than one catalogue entry equally well, so left for staff "
                         f"to pick: {', '.join(repr(a) for a in ambiguous)}")

    questionnaire = package.questionnaire
    if questionnaire is QuestionnaireKind.NONE:
        questionnaire = issuer.default_questionnaire
    reasoning.append(f"questionnaire: {questionnaire.value}")

    # PARTIAL means "a rule fired, but something on this document still needs a human". It is not
    # a failure state — it is the state most real documents should land in.
    status = CoverageStatus.PARTIAL if flags else CoverageStatus.DETERMINED

    return CoverageDetermination(
        issuer_code=issuer_code,
        matched_rule_id=package.id,
        package_code=package.package_code,
        package_name=package.name,
        authorised_tests=authorised,
        questionnaire=questionnaire,
        eligibility_flags=flags,
        unmatched_tests=unmatched + ambiguous,
        status=status,
        reasoning=reasoning,
        history_findings=conflict_findings)
