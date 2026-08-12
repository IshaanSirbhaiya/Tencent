"""The post-visit TPA claim draft, and the property that makes it safe to put on a screen.

`claim.py` exists because the first version of this endpoint read the in-memory working draft and
returned `submit_to`, `issuer_code`, `package_code`, `patient_id` and `line_items` as bare strings
— no `SourcePointer` on any of them, and, under a partial `field_paths` confirmation, fields no
human had ever committed. That is CLAUDE.md §Hard constraints 2 straight through the middle.

The two tests that carry the weight here are
`test_build_claim_draft_cannot_be_handed_the_working_draft` (the structural guard: there is no
parameter through which an uncommitted value could arrive) and
`test_every_claim_pointer_resolves_to_its_value` (the anti-fabrication proof, the same shape as
`test_extract.py::test_every_pointer_resolves_to_its_value`).
"""
from __future__ import annotations

import datetime as dt
import inspect
import json

import pytest
from pydantic import ValidationError

from databridge_core import SourcePointer
from databridge_intake.claim import (AmbiguousPackageRule, ClaimDraft, ClaimLine, ClaimValue,
                                     ConflictingSelections, NoPayerToClaimFrom,
                                     NothingCommitted, build_claim_draft)
from databridge_intake.ledger import EvidenceLedger
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake
from databridge_intake.rules import rules

STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)

# One with a package code and an add-on outside the package, one whose issuer's rule takes its
# scope from the document itself. Between them they exercise both branches of `covered_by_rule`.
PACKAGED = "chit_07_mol_authorisation_pee226.txt"
FROM_DOCUMENT = "chit_02_everwell_voucher.txt"


def _text(name: str) -> str:
    return (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def texts() -> dict:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted((fixtures_dir() / "chits").glob("*"))}


@pytest.fixture(scope="module")
def drafts(texts) -> dict:
    return {name: run_intake(text, document_id=name) for name, text in texts.items()}


def _attested(draft) -> ConfirmationState:
    return (ConfirmationState(intake_id=draft.intake_id)
            .attest_identity_checked(STAFF, NOW)
            .select_patient(draft.resolution.candidates[0].patient_id))


def _committed_ledger(draft, *, field_paths=None) -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.commit(draft.record, _attested(draft), at=NOW, field_paths=field_paths)
    return ledger


@pytest.fixture(scope="module")
def claims(drafts) -> dict:
    """A claim draft per chit that has a payer, built from a fully committed ledger."""
    out = {}
    for name, draft in drafts.items():
        ledger = _committed_ledger(draft)
        try:
            out[name] = (build_claim_draft(draft.intake_id, ledger), draft, ledger)
        except NoPayerToClaimFrom:
            continue                # self-pay / no confirmed issuer — covered by its own test
    return out


# --- the structural guard ----------------------------------------------------------------


def test_build_claim_draft_cannot_be_handed_the_working_draft():
    """The fix for the original bug is a signature, not a convention.

    A comment saying "read only committed values" is exactly what the previous version had in
    spirit and did not have in fact. If a `draft`/`record`/`coverage`/`billing` parameter is ever
    added here, an uncommitted value can reach the claim again — so the parameter list itself is
    the assertion.
    """
    params = set(inspect.signature(build_claim_draft).parameters)
    assert params == {"intake_id", "ledger", "table"}, (
        f"build_claim_draft takes {sorted(params)}; anything beyond the intake id, the evidence "
        f"ledger and the config table is a route through which uncommitted data can arrive")


def test_the_claim_module_does_not_import_the_pipeline():
    """Belt and braces on the same property: `claim.py` must not be able to re-run extraction and
    quietly read what the document says rather than what a human confirmed."""
    import databridge_intake.claim as claim_module

    source = inspect.getsource(claim_module)
    for forbidden in ("from .pipeline", "from .extract", "import pipeline", "IntakeDraft"):
        assert forbidden not in source, (
            f"claim.py references {forbidden!r} — the claim is built from the ledger, not from a "
            f"re-read of the document")


# --- the anti-fabrication proof ----------------------------------------------------------


def test_every_claim_pointer_resolves_to_its_value(claims, texts):
    """Every value a claim emits can be traced, and a `doc_span` slice equals the value it cites.

    Same discipline as the extraction proof: EQUALITY, not containment, so a whole-document
    pointer cannot pass itself off as provenance.
    """
    norm = lambda s: "".join(str(s).lower().split())
    checked = 0
    for name, (claim, _draft, _ledger) in claims.items():
        text = texts[name]
        emitted = [(f, getattr(claim, f)) for f in ("issuer_code", "package_code", "patient_id",
                                                    "submit_to", "tpa_claim_required")]
        for field, value in emitted:
            if value is None:
                continue
            p = value.pointer
            if p.kind == "doc_span":
                assert norm(value.value) == norm(text[p.char_start:p.char_end]), (
                    f"{name} {field}: pointer slices to "
                    f"{text[p.char_start:p.char_end]!r} but the value is {value.value!r}")
            elif p.kind == "derived":
                # A config-decided value: it must name the rule and cite what selected it.
                assert p.derivation, f"{name} {field}: derived pointer with no derivation"
                assert p.derived_from, f"{name} {field}: derived pointer citing nothing"
            elif p.kind == "table_cell":
                assert p.table and p.column and p.row_id, f"{name} {field}: incomplete table_cell"
            else:
                pytest.fail(f"{name} {field}: unexpected pointer kind {p.kind!r}")
            checked += 1
        for line in list(claim.line_items) + list(claim.excluded_from_claim):
            p = line.pointer
            if p.kind == "doc_span":
                assert norm(line.as_printed) == norm(text[p.char_start:p.char_end]), (
                    f"{name} {line.source_field}: line pointer does not slice to its printed text")
            elif p.kind == "table_cell":
                # A test the PACKAGE includes rather than one the document printed. Its provenance
                # is the config row the committed package code selected.
                assert p.source_id == "coverage_rules.yaml" and p.row_id, (
                    f"{name} {line.source_field}: config-derived line with no rule row")
                assert line.in_package, "only package contents may cite the rules file"
            else:
                pytest.fail(f"{name}: unexpected claim-line pointer kind {p.kind!r}")
            checked += 1
    assert checked > 20, f"only {checked} claim pointers checked — the corpus should yield more"


def test_every_claim_line_names_a_row_that_is_actually_in_the_ledger(claims):
    """A line item exists because a human confirmed that test, not because the document printed
    it. `source_field` must be a live ledger path for this intake."""
    for name, (claim, draft, ledger) in claims.items():
        live = set(ledger.current(draft.intake_id))
        for line in list(claim.line_items) + list(claim.excluded_from_claim):
            if line.pointer.kind != "doc_span":
                continue        # a package's contents come from config, not from a ledger row
            assert line.source_field in live, (
                f"{name}: claim line {line.source_field!r} is not a committed field")


def test_a_test_is_claimed_at_most_once(claims):
    """A test printed twice — in a table and again in prose, or under two aliases — must not be
    submitted, and paid, twice."""
    for name, (claim, _draft, _ledger) in claims.items():
        canonicals = [line.canonical for line in claim.line_items]
        assert len(canonicals) == len(set(canonicals)), f"{name}: duplicate line items"


# --- what was NOT committed ---------------------------------------------------------------


def test_a_field_nobody_committed_is_blank_and_says_so(drafts):
    """The original bug, pinned. Confirm a subset of field paths and the claim must not emit the
    rest — and must name them rather than leaving a silent gap."""
    draft = drafts[PACKAGED]
    grounded = draft.record.grounded_fields()
    assert "authorisation.package_code" in grounded, "fixture no longer carries a package code"
    # Everything EXCEPT the package code.
    subset = [p for p in grounded if p != "authorisation.package_code"]
    ledger = _committed_ledger(draft, field_paths=subset)

    claim = build_claim_draft(draft.intake_id, ledger)
    assert claim.package_code is None, (
        "the package code was never committed, so the claim must not carry it")
    assert "package_code" in claim.unresolved
    # And the consequence, which is the part that matters: with no committed package code this
    # issuer has no rule, so the PAYER is unestablished too. The previous implementation would
    # have read it straight off the working draft and named one.
    assert claim.submit_to is None and "submit_to" in claim.unresolved
    assert any("payer could not be established" in c for c in claim.caveats)


def test_a_claim_with_no_committed_issuer_is_refused(drafts):
    """No confirmed issuer means no third party. Inventing one would put a bill in front of a
    company that never agreed to it."""
    draft = drafts[FROM_DOCUMENT]
    grounded = draft.record.grounded_fields()
    subset = [p for p in grounded if p != "issuer.issuer_code"]
    ledger = _committed_ledger(draft, field_paths=subset)

    with pytest.raises(NoPayerToClaimFrom, match="no issuer code"):
        build_claim_draft(draft.intake_id, ledger)


def test_an_empty_ledger_yields_no_claim(drafts):
    draft = drafts[FROM_DOCUMENT]
    with pytest.raises(NothingCommitted):
        build_claim_draft(draft.intake_id, EvidenceLedger())


# --- identity, which is never the machine's to assert --------------------------------------


def test_the_patient_id_cites_the_human_selection_not_the_document(claims):
    """The document carries a *claimed* name. Only a person who checked an NRIC against a face can
    say which registry row that is, and the pointer must name that act rather than a span of text
    (CLAUDE.md §Hard constraints 1)."""
    for name, (claim, draft, _ledger) in claims.items():
        assert claim.patient_id is not None, f"{name}: a committed intake has a selection"
        p = claim.patient_id.pointer
        assert p.kind == "table_cell" and p.table == "evidence", (
            f"{name}: the patient id must cite the ledger row recording the human selection, "
            f"not a document span — it is not a fact the document states")
        assert p.column == "selected_patient_id"
        assert claim.patient_id.confirmed_by == STAFF
        assert claim.patient_id.value == draft.resolution.candidates[0].patient_id


def test_no_claim_field_claims_identity_was_verified(claims):
    """The wording check. `STATUS.md` records a UI that said "verified in person" in green with a
    tick; the system records that a named person *said* they checked."""
    for name, (claim, _draft, _ledger) in claims.items():
        blob = " ".join(claim.caveats).lower()
        if claim.patient_id is not None:
            blob += " " + (claim.patient_id.pointer.derivation or "").lower()
        for forbidden in ("identity verified", "identity confirmed", "verified identity"):
            assert forbidden not in blob, f"{name}: claim text asserts {forbidden!r}"


# --- what the claim does NOT ask the payer for ---------------------------------------------


def test_a_test_outside_the_package_is_excluded_and_gives_a_reason(drafts):
    """Chit 7 orders an MMR dose alongside the PEE226 package. Folding it into "covered" would
    tell the patient the insurer pays for something it never authorised."""
    draft = drafts[PACKAGED]
    ledger = _committed_ledger(draft)
    claim = build_claim_draft(draft.intake_id, ledger)

    assert claim.excluded_from_claim, "chit 7 orders more than the package includes"
    for excluded in claim.excluded_from_claim:
        assert excluded.reason.strip(), "an exclusion with no reason is a silent drop"
    assert any("excluded_from_claim" in c for c in claim.caveats)


def test_no_committed_test_is_silently_dropped(claims):
    """Every committed test row ends up somewhere visible: claimed, explicitly excluded, or
    de-duplicated against an identical earlier line. Nothing else may vanish.

    The de-duplication exemption is computed here rather than assumed, because "it was probably a
    duplicate" is exactly the reasoning that lets a real omission through.
    """
    table = rules()
    for name, (claim, draft, ledger) in claims.items():
        rows = ledger.current(draft.intake_id)
        committed = sorted(p for p in rows
                           if p.startswith(("authorisation.requested_tests[",
                                            "authorisation.add_ons[")))
        accounted = {line.source_field for line in claim.line_items}
        accounted |= {x.source_field for x in claim.excluded_from_claim}

        seen_keys: set[str] = set()
        for path in committed:
            printed = str(json.loads(rows[path]["value_json"]))
            match = table.match_test(printed)
            key = match.canonical if match.matched else f"as_printed:{printed.strip().lower()}"
            was_duplicate = key in seen_keys
            seen_keys.add(key)
            if was_duplicate:
                continue
            assert path in accounted, (
                f"{name}: committed test {path!r} ({printed!r}) is neither claimed nor excluded, "
                f"and is not a duplicate of an earlier line — it vanished")


def test_a_document_defined_rule_claims_the_tests_it_authorises(drafts):
    """The happy path, asserted positively: chit 2's issuer takes its scope from the document, so
    the tests printed on it are what the insurer is billed for. A claim that produced no lines at
    all would pass every negative test in this file."""
    draft = drafts[FROM_DOCUMENT]
    claim = build_claim_draft(draft.intake_id, _committed_ledger(draft))

    assert claim.submit_to is not None and claim.submit_to.value == "insurer"
    assert claim.line_items, "chit 2 authorises tests; the claim must carry them"
    assert all(line.canonical for line in claim.line_items)


# --- the honesty channel is checked, not trusted -------------------------------------------


def test_a_claim_cannot_be_constructed_with_an_undeclared_blank():
    """`unresolved` is only meaningful if something enforces it."""
    with pytest.raises(ValidationError, match="unresolved"):
        ClaimDraft(intake_id="intake:x")            # every field blank, nothing declared


def test_a_claim_cannot_declare_a_field_it_actually_carries():
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d",
                        char_start=0, char_end=5)
    with pytest.raises(ValidationError, match="unresolved"):
        ClaimDraft(intake_id="intake:x",
                   issuer_code=ClaimValue(value="EVWPA", pointer=ptr),
                   unresolved=["submit_to", "tpa_claim_required", "package_code", "patient_id",
                               "issuer_code"])


def test_a_claim_value_cannot_exist_without_a_pointer():
    """The structural half of hard constraint 2: there is no third state between absent and
    grounded."""
    with pytest.raises(ValidationError):
        ClaimValue(value="insurer")


def test_a_priced_line_cannot_exist_without_a_pointer_to_the_price():
    """A document span grounds the printed TEST NAME and nothing else. Review's point was that the
    price beside it could be changed arbitrarily without invalidating that pointer — and on a claim
    the price is the part that gets paid."""
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d", char_start=0, char_end=5)
    with pytest.raises(ValidationError, match="price"):
        ClaimLine(as_printed="Chest X-Ray", source_field="authorisation.requested_tests[0]",
                  pointer=ptr, canonical="chest_xray", list_price_sgd="45")


def test_every_priced_line_cites_the_catalogue_row(claims):
    for name, (claim, _draft, _ledger) in claims.items():
        for line in claim.line_items:
            if line.list_price_sgd is None:
                continue
            assert line.price_pointer is not None
            assert line.price_pointer.source_id == "coverage_rules.yaml"
            assert line.price_pointer.table == "test_catalogue"
            assert line.price_pointer.row_id == line.canonical, (
                f"{name}: the price pointer names {line.price_pointer.row_id!r} but the line is "
                f"{line.canonical!r}")


# --- what the system refuses to decide ----------------------------------------------------


def test_a_ledger_naming_two_patients_refuses_rather_than_picking_the_newer(drafts):
    """The ledger is append-only, so a correction is a NEW row — and `current()` merges the latest
    row per field across batches. Nothing stopped those rows coming from different selections.

    Taking the newest would be the machine settling a contradiction about WHO SOMEONE IS, which is
    the single thing it must never do (CLAUDE.md §Hard constraints 1). Found in review.
    """
    draft = drafts[FROM_DOCUMENT]
    assert len(draft.resolution.candidates) >= 1
    ledger = _committed_ledger(draft)

    # A correction committed against a DIFFERENT patient — the shape an append-only ledger allows.
    other = "pat:someone_else_entirely"
    rows = ledger.entries(draft.intake_id)
    ledger.conn.execute(
        "INSERT INTO evidence (intake_id, field_path, value_json, source_pointer_json, "
        "extracted_at, confirmed_by, confirmed_at, selected_patient_id, supersedes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft.intake_id, rows[0]["field_path"], rows[0]["value_json"],
         rows[0]["source_pointer_json"], rows[0]["extracted_at"], STAFF,
         NOW.isoformat(), other, rows[0]["rowid_pk"]))
    ledger.conn.commit()

    with pytest.raises(ConflictingSelections, match="more than one selected patient"):
        build_claim_draft(draft.intake_id, ledger)


def test_an_absent_package_code_refuses_when_the_issuer_has_coded_packages(drafts):
    """An absence is not evidence. With no committed package code `table.package` falls back to the
    issuer's document-defined rule — right for a referral letter, a guess for a form whose package
    box was never confirmed. It is only safe when no other rule could have been meant.
    """
    draft = drafts[PACKAGED]                       # MOL0199VME — three coded packages
    grounded = draft.record.grounded_fields()
    subset = [p for p in grounded if p != "authorisation.package_code"]
    ledger = _committed_ledger(draft, field_paths=subset)

    table = rules()
    issuer = str(draft.record.issuer.issuer_code.value)
    coded = [p for p in table.packages_for(issuer) if p.package_code]
    fallback = [p for p in table.packages_for(issuer) if p.tests_from_document]
    if coded and fallback:
        with pytest.raises(AmbiguousPackageRule):
            build_claim_draft(draft.intake_id, ledger)
    else:
        # This issuer has no fallback rule at all, so there is nothing to guess WITH: the claim
        # comes back with the payer unresolved rather than refusing outright.
        claim = build_claim_draft(draft.intake_id, ledger)
        assert claim.submit_to is None and "submit_to" in claim.unresolved


def test_no_issuer_can_reach_the_ambiguous_fallback_today():
    """The hazard above is made unreachable rather than merely documented — asserted against the
    real config so that adding a coded package beside a document-defined rule fails loudly here
    instead of silently changing what a claim means."""
    table = rules()
    for issuer in table.issuers:
        pkgs = table.packages_for(issuer.code)
        coded = [p.id for p in pkgs if p.package_code]
        fallback = [p.id for p in pkgs if p.tests_from_document]
        assert not (coded and fallback), (
            f"issuer {issuer.code} now has both coded packages {coded} and a document-defined "
            f"rule {fallback}; an uncommitted package code is genuinely ambiguous for it")


# --- nothing disappears quietly -----------------------------------------------------------


def test_the_claim_says_eligibility_was_not_re_evaluated(claims):
    """`billing.py` raises a caveat when eligibility flags are set; this module cannot see them,
    because eligibility is a determination about a patient rather than a fact the document states.
    Silence would read as "checked and passed"."""
    for name, (claim, _draft, _ledger) in claims.items():
        assert any("Eligibility is NOT re-evaluated" in c for c in claim.caveats), name


def test_a_repeated_test_is_surfaced_rather_than_dropped(drafts):
    """De-duplication stops one test being claimed, and paid, twice. It must not silently swallow a
    genuinely separate second authorisation — two doses, two specimens — so the dropped occurrence
    is reported for a human instead of vanishing.

    No chit in the corpus currently prints the same test twice, so the duplicate row is written
    directly. A latent hazard is still a hazard; it just has not fired yet.
    """
    draft = drafts[FROM_DOCUMENT]
    ledger = _committed_ledger(draft)
    rows = ledger.current(draft.intake_id)
    first_test = min((p for p in rows if p.startswith("authorisation.requested_tests[")),
                     key=lambda p: p)
    original = rows[first_test]

    ledger.conn.execute(
        "INSERT INTO evidence (intake_id, field_path, value_json, source_pointer_json, "
        "extracted_at, confirmed_by, confirmed_at, selected_patient_id, supersedes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft.intake_id, "authorisation.requested_tests[99]", original["value_json"],
         original["source_pointer_json"], original["extracted_at"], STAFF, NOW.isoformat(),
         original["selected_patient_id"], None))
    ledger.conn.commit()

    claim = build_claim_draft(draft.intake_id, ledger)
    canonicals = [line.canonical for line in claim.line_items]
    assert len(canonicals) == len(set(canonicals)), "the duplicate was claimed twice"
    dropped = [x for x in claim.excluded_from_claim
               if x.source_field == "authorisation.requested_tests[99]"]
    assert dropped, "the duplicate vanished instead of being reported"
    assert "already claimed once" in dropped[0].reason


def test_claim_lines_follow_the_order_they_were_printed_in():
    """A plain lexicographic sort puts `[10]` before `[2]`, so a document listing ten or more tests
    produced a claim out of the order the person checking it against the letter reads them in."""
    from databridge_intake.claim import _path_order

    paths = [f"authorisation.requested_tests[{i}]" for i in (10, 2, 1, 11)]
    assert [_path_order(p)[1] for p in sorted(paths, key=_path_order)] == [1, 2, 10, 11]
