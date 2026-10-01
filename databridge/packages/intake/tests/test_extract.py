"""Phase 1 — end-to-end extraction over the real chit-letter corpus.

The single most important test here is `test_every_pointer_resolves_to_its_value`: it walks every
grounded field of every fixture, slices the source at the recorded span, and checks the value is
really there. That one assertion is the anti-hallucination proof the whole product claims.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_intake import DocumentType, chits_dir, fixtures_dir
from databridge_intake.extract import FIELD_MAP, LIST_MAP, IntakeDraft, extract_intake
from databridge_intake.gold import GOLD, gold_harness, markers_are_unique

CHITS = list(GOLD)


@pytest.fixture(scope="module")
def harness():
    return gold_harness()


@pytest.fixture(scope="module")
def texts() -> dict[str, str]:
    return {n: (chits_dir() / n).read_text(encoding="utf-8") for n in CHITS}


@pytest.fixture(scope="module")
def records(harness, texts) -> dict:
    return {n: extract_intake(t, document_id=n, harness=harness) for n, t in texts.items()}


# --- the anti-fabrication proof --------------------------------------------------------
def test_every_pointer_resolves_to_its_value(records, texts):
    """For every extracted field of every fixture: slice the source at the recorded span and
    confirm it contains the value. If this passes, no field on the front-desk card can be
    something the model invented."""
    checked = 0
    for name, rec in records.items():
        text = texts[name]
        for path, gv in rec.grounded_fields().items():
            p = gv.pointer
            if p.kind != "doc_span":
                continue        # classified fields carry `derived` pointers, checked separately
            sliced = text[p.char_start:p.char_end]
            norm = lambda s: "".join(s.lower().split())
            # EQUALITY, not containment. Allowing the value to sit anywhere inside the slice
            # would let a whole-document pointer pass this test and call itself provenance.
            assert norm(str(gv.value)) == norm(sliced), (
                f"{name} {path}: pointer [{p.char_start}:{p.char_end}] slices to {sliced!r} "
                f"but the value is {gv.value!r}")
            # A citation the size of the page is not a citation.
            assert p.char_end - p.char_start <= max(len(str(gv.value)) + 8, 64), (
                f"{name} {path}: span is far wider than the value it cites")
            checked += 1
    assert checked > 100, f"only {checked} pointers checked — the corpus should yield far more"


def test_derived_pointers_chain_back_to_the_document(records):
    for rec in records.values():
        p = rec.document_type.pointer
        assert p.kind == "derived"
        assert p.derived_from and p.derived_from[0].document_id == rec.document.document_id


def test_no_fixture_produces_a_refusal_under_the_gold_backend(records):
    """The gold drafts quote the documents, so nothing should be refused. A refusal here means
    either a draft drifted from its fixture or the wall became over-strict."""
    for name, rec in records.items():
        assert not rec.refused, f"{name}: {rec.refused}"


# --- classification --------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("chit_01_meridian_referral.txt", DocumentType.INSURER_REFERRAL_LETTER),
    ("chit_02_everwell_voucher.txt", DocumentType.HEALTH_CHECKUP_VOUCHER),
    ("chit_03_everwell_group_invitation.txt", DocumentType.GROUP_INSURANCE_INVITATION),
    ("chit_04_bluepeak_underwriting.txt", DocumentType.UNDERWRITING_FOLLOWUP),
    ("chit_05_bluepeak_wellness_voucher.txt", DocumentType.HEALTH_CHECKUP_VOUCHER),
    ("chit_06_northstar_further_req.txt", DocumentType.UNDERWRITING_FOLLOWUP),
    ("chit_07_mol_authorisation_pee226.txt", DocumentType.TPA_AUTHORISATION_FORM),
    ("chit_08_mol_authorisation_vital.txt", DocumentType.TPA_AUTHORISATION_FORM),
    ("chit_09_coralharbour_hr_email.eml", DocumentType.EMPLOYER_HR_EMAIL),
])
def test_document_type(records, name, expected):
    assert records[name].document_type.value == expected.value


# --- the three widest-variety fixtures (PLAN.md Phase 1 demo set) ----------------------
def test_chit_01_referral_letter(records):
    rec = records["chit_01_meridian_referral.txt"]
    assert rec.issuer.issuer_code.value == "MRDEB"
    assert rec.issuer.policy_number.value == "MRD707314"
    assert rec.subject.claimed_name.value == "Loh Wei Ming"
    assert rec.subject.claimed_id_number.value == "S8010946C"
    assert rec.employer.employer_name.value == "Coral Harbour Pte Ltd"
    printed = [t.value for t in rec.authorisation.requested_tests]
    assert printed == ["Chest X-Ray", "Meridian Life# 10 - HIV Antibody Test", "Treadmill ECG"]


def test_chit_02_voucher(records):
    rec = records["chit_02_everwell_voucher.txt"]
    assert rec.issuer.issuer_code.value == "EVWPA"
    assert rec.issuer.policy_number.value == "EVW563298"
    assert rec.subject.claimed_id_number.value == "T2221854H"
    # The printed list, read from the fixture (HC1, rule 6): every line after the voucher's
    # "shall consist of the following:" up to the next table cell.
    from databridge_intake.paths import chits_dir

    lines = (chits_dir() / "chit_02_everwell_voucher.txt").read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if "consist of the following" in l) + 1
    listed = []
    for line in lines[start:]:
        if "|" in line:
            break
        listed.append(line.strip())
    assert len(listed) >= 1
    assert [t.value for t in rec.authorisation.requested_tests] == listed
    assert rec.authorisation.appointment_datetime.value == "30 / 07 / 2026"
    assert rec.authorisation.valid_until.value == "30 Sep 2026"


def test_chit_07_tpa_authorisation_reads_only_the_ticked_box(records):
    """The form prints PEE224, PEE225 and PEE226; only PEE226 is ticked. Reading an unticked
    option would authorise the wrong examination for the patient's age band."""
    rec = records["chit_07_mol_authorisation_pee226.txt"]
    assert rec.issuer.issuer_code.value == "MOL0199VME"
    assert rec.authorisation.package_code.value == "PEE226"
    assert rec.subject.claimed_id_number.value == "S8001691D"
    assert rec.subject.claimed_dob.value == "20/05/77"
    assert rec.employer.scheme_of_service.value == "MKL"
    assert [a.value for a in rec.authorisation.add_ons] == ["MMR Dose 1 Vaccination"]
    # the unticked tiers must appear nowhere in the record
    blob = str(rec.model_dump())
    assert "PEE224" not in blob and "PEE225" not in blob


def test_chit_05_voucher_reads_only_the_ticked_tier(records):
    rec = records["chit_05_bluepeak_wellness_voucher.txt"]
    assert rec.authorisation.package_code.value == "WELL2"
    blob = str(rec.model_dump())
    assert "WELL1" not in blob and "WELL3" not in blob
    # The voucher prints every test of the ticked tier; the count is the rule table's (HC1).
    from databridge_intake.rules import rules

    package = rules().package(rec.issuer.issuer_code.value, rec.authorisation.package_code.value)
    assert package is not None and len(package.includes_tests) >= 1
    assert len(rec.authorisation.requested_tests) == len(package.includes_tests)


# --- absence is recorded as absence, never inferred -------------------------------------
@pytest.mark.parametrize("name", [
    "chit_03_everwell_group_invitation.txt",
    "chit_05_bluepeak_wellness_voucher.txt",
    "chit_09_coralharbour_hr_email.eml",
])
def test_documents_without_an_nric_do_not_acquire_one(records, name):
    """These three drive the ambiguous-resolution cases. If extraction back-filled an NRIC from
    the registry the whole identity story would be fiction — the resolver's job is to propose
    candidates, and only a human may pick one."""
    rec = records[name]
    assert rec.subject.claimed_id_number is None
    assert "subject.claimed_id_number" in rec.unresolved


def test_chit_08_has_no_package_code_and_says_so(records):
    """Section B is absent from this form entirely. Coverage must later come back UNDETERMINED;
    extraction must not invent a tier."""
    rec = records["chit_08_mol_authorisation_vital.txt"]
    assert rec.authorisation.package_code is None
    assert "authorisation.package_code" in rec.unresolved
    assert rec.authorisation.requested_tests == []


def test_unresolved_covers_every_unpopulated_field(records):
    for name, rec in records.items():
        populated = set(rec.grounded_fields())
        for path, _ in FIELD_MAP.values():
            if path not in populated:
                assert path in rec.unresolved, f"{name}: {path} is neither populated nor unresolved"
        for path in LIST_MAP.values():
            if not any(p.startswith(path + "[") for p in populated):
                assert path in rec.unresolved


# --- the walk-in / no-document path is first class ---------------------------------------
def test_no_document_path(harness):
    rec = extract_intake("", document_id="walkin_01", harness=harness, channel="walk_in")
    assert rec.document_type.value == DocumentType.NO_DOCUMENT.value
    assert rec.document.channel == "walk_in"
    assert rec.grounded_fields() == {"document_type": rec.document_type}
    assert rec.refused == ()


def test_walkin_fixture_loads_and_extracts(harness):
    import json
    data = json.loads((fixtures_dir() / "walkin" / "walkin_01.json").read_text(encoding="utf-8"))
    rec = extract_intake("", document_id=data["document_id"], harness=harness,
                         channel=data["channel"])
    assert rec.document_type.value == "no_document"


def test_an_unknown_document_extracts_nothing_rather_than_the_nearest_known_one(harness):
    """The honest answer to "I have never seen this layout" is an empty record that routes to a
    human, not the closest fixture's values."""
    rec = extract_intake("Dear Sir, please find enclosed our quarterly newsletter. Regards.",
                         document_id="unknown.txt", harness=harness)
    assert rec.document_type.value == DocumentType.UNRECOGNISED.value
    assert rec.issuer.issuer_code is None
    assert rec.subject.claimed_name is None


# --- integrity of the gold backend itself -------------------------------------------------
def test_gold_dispatch_markers_are_unique(texts):
    assert markers_are_unique(texts) == []


def test_gold_covers_every_chit_on_disk():
    """Enumerates the fixture DIRECTORY, not the gold table.

    The previous version compared `GOLD` against `texts`, which is built from `CHITS`, which is
    built from `GOLD` — tautologically true, and it would not have noticed a new fixture landing
    in the corpus with no gold draft behind it.
    """
    on_disk = {p.name for p in chits_dir().iterdir() if p.is_file()}
    assert on_disk == set(GOLD), (
        f"fixtures without a gold draft: {sorted(on_disk - set(GOLD))}; "
        f"gold drafts with no fixture: {sorted(set(GOLD) - on_disk)}")


def test_extraction_is_deterministic(harness, texts):
    a = extract_intake(texts[CHITS[0]], document_id=CHITS[0], harness=harness,
                       received_at=dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc))
    b = extract_intake(texts[CHITS[0]], document_id=CHITS[0], harness=harness,
                       received_at=dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc))
    assert a.model_dump() == b.model_dump()


def test_a_model_that_fabricates_gets_its_values_dropped(texts):
    """The wall's job, exercised directly: a backend that returns plausible-but-absent values must
    produce an empty record with refusals, not a confident wrong one."""
    from databridge_harness import FakeBackend, Harness

    liar = IntakeDraft(issuer_code="ZZZZZ", policy_number="XX999999",
                       claimed_name="Someone Else Entirely", claimed_id_number="S1234567A",
                       requested_tests=["Colonoscopy", "Mammogram"])
    payload = liar.model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")

    rec = extract_intake(texts[CHITS[0]], document_id=CHITS[0], harness=h)
    assert rec.issuer.issuer_code is None
    assert rec.issuer.policy_number is None
    assert rec.subject.claimed_name is None
    assert rec.subject.claimed_id_number is None
    assert rec.authorisation.requested_tests == []
    assert len(rec.refused) >= 6
    assert any("ZZZZZ" in r for r in rec.refused)


# --- accounting completeness: no field escapes both columns -------------------------------
def test_every_schema_field_is_either_populated_or_declared_unresolved(records):
    """`unresolved` is the product's promise about absence, so it has to be exhaustive.

    Regression: `authorisation.billing_party` and `subject.claimed_id_type` were originally in
    neither column — silently missing rather than reported missing, which is exactly the failure
    the honesty channel exists to prevent.
    """
    from databridge_intake.models import _known_field_paths

    for name, rec in records.items():
        populated = set(rec.grounded_fields())
        for path in _known_field_paths():
            accounted = (path in populated
                         or path in rec.unresolved
                         or any(p.startswith(path + "[") for p in populated))
            assert accounted, f"{name}: {path} is neither populated nor listed unresolved"


def test_classified_fields_never_claim_a_document_span(records):
    """issuer_kind / billing_party / visit_type are conclusions, not quotes. A `doc_span` would be
    a fabricated citation: pointing at text that does not literally say the value."""
    for rec in records.values():
        for gv in (rec.visit_type, rec.issuer.issuer_kind, rec.authorisation.billing_party):
            if gv is not None:
                assert gv.pointer.kind in ("derived", "table_cell")
                if gv.pointer.kind == "derived":
                    assert gv.pointer.derived_from


def test_issuer_kind_and_billing_party_come_from_the_rules_table_not_the_model(records):
    """Who the issuer is and who pays are properties of the scheme. Once the issuer code is
    grounded, the config is authoritative — otherwise a model could silently reroute a bill."""
    for name, rec in records.items():
        if rec.issuer.issuer_code is None:
            continue
        for gv in (rec.issuer.issuer_kind, rec.authorisation.billing_party):
            assert gv is not None, name
            assert gv.pointer.kind == "table_cell"
            assert gv.pointer.source_id == "coverage_rules.yaml"
            assert gv.pointer.row_id == rec.issuer.issuer_code.value
            assert gv.confidence == 1.0


def test_the_model_cannot_override_the_configured_payer():
    """A draft claiming the patient pays for an insurer-covered scheme must be ignored."""
    from databridge_harness import FakeBackend, Harness
    payload = IntakeDraft(issuer_code="MOL0199VME", billing_party="patient",
                          issuer_kind="insurer").model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    text = (chits_dir() / "chit_07_mol_authorisation_pee226.txt").read_text(encoding="utf-8")
    rec = extract_intake(text, document_id="chit_07_mol_authorisation_pee226.txt", harness=h)
    assert rec.authorisation.billing_party.value == "government_scheme"
    assert rec.issuer.issuer_kind.value == "government_scheme"


def test_classification_pointer_covers_the_marker_that_actually_fired(records, texts):
    """A fixed 0:400 evidence window would be a citation that need not contain its own evidence."""
    for name, rec in records.items():
        p = rec.document_type.pointer.derived_from[0]
        snippet = texts[name][p.char_start:p.char_end].lower()
        assert snippet.strip(), f"{name}: empty evidence span"
        assert p.char_end - p.char_start < 500, f"{name}: evidence span is a whole-page citation"


def test_claimed_id_type_is_derived_from_the_id_number_when_present(records):
    rec = records["chit_01_meridian_referral.txt"]
    assert rec.subject.claimed_id_type.value == "nric_fin"
    assert rec.subject.claimed_id_type.pointer.kind == "derived"
    # chains back to the actual NRIC span on the document
    base = rec.subject.claimed_id_type.pointer.derived_from[0]
    assert base.char_start == rec.subject.claimed_id_number.pointer.char_start


def test_no_id_type_is_claimed_when_there_is_no_id(records):
    """The three no-NRIC documents must not acquire an ID *type* either."""
    for name in ("chit_03_everwell_group_invitation.txt",
                 "chit_05_bluepeak_wellness_voucher.txt",
                 "chit_09_coralharbour_hr_email.eml"):
        rec = records[name]
        assert rec.subject.claimed_id_type is None
        assert "subject.claimed_id_type" in rec.unresolved


def test_an_out_of_vocabulary_classification_is_refused_not_stored():
    from databridge_harness import FakeBackend, Harness
    payload = IntakeDraft(issuer_kind="insuurer", visit_type="screening",
                          billing_party="whoever").model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    text = (chits_dir() / "chit_01_meridian_referral.txt").read_text(encoding="utf-8")
    rec = extract_intake(text, document_id="chit_01_meridian_referral.txt", harness=h)
    assert rec.issuer.issuer_kind is None and rec.visit_type is None
    assert rec.authorisation.billing_party is None
    assert sum("is not a valid" in r for r in rec.refused) == 3


# --- checkbox correctness is the WALL's job, not the prompt's ---------------------------
# Review finding: PEE224/PEE225 and WELL1/WELL3 are printed in full on their forms, so a wall
# that only asks "does this string appear in the source?" confirms an option the form declined.
# The gold drafts never test this because they already omit unticked options — a model that got
# it wrong would have sailed through.
@pytest.mark.parametrize("fixture,declined", [
    ("chit_07_mol_authorisation_pee226.txt", "PEE224"),
    ("chit_07_mol_authorisation_pee226.txt", "PEE225"),
    ("chit_05_bluepeak_wellness_voucher.txt", "WELL1"),
    ("chit_05_bluepeak_wellness_voucher.txt", "WELL3"),
])
def test_an_unticked_option_cannot_be_grounded_as_the_selected_package(fixture, declined, texts):
    from databridge_harness import FakeBackend, Harness

    payload = IntakeDraft(package_code=declined).model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts[fixture], document_id=fixture, harness=h)

    assert rec.authorisation.package_code is None, (
        f"{declined} is printed on {fixture} but UNTICKED — grounding it would authorise and "
        f"bill an examination the form explicitly declined")
    assert "authorisation.package_code" in rec.unresolved
    assert any(declined in r for r in rec.refused)


def test_the_ticked_option_still_grounds(texts):
    from databridge_harness import FakeBackend, Harness
    payload = IntakeDraft(package_code="PEE226").model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts["chit_07_mol_authorisation_pee226.txt"],
                         document_id="chit_07_mol_authorisation_pee226.txt", harness=h)
    assert rec.authorisation.package_code.value == "PEE226"


def test_an_unticked_add_on_vaccination_cannot_be_grounded(texts):
    """chit_07 section C lists 13 vaccinations; only MMR Dose 1 is ticked. Billing a patient for
    a Yellow Fever vaccination nobody ordered is the concrete harm here."""
    from databridge_harness import FakeBackend, Harness
    payload = IntakeDraft(add_ons=["Yellow Fever Single Dose (Vaccination)",
                                   "MMR Dose 1 Vaccination"]).model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts["chit_07_mol_authorisation_pee226.txt"],
                         document_id="chit_07_mol_authorisation_pee226.txt", harness=h)
    kept = [a.value for a in rec.authorisation.add_ons]
    assert kept == ["MMR Dose 1 Vaccination"]
    assert any("Yellow Fever" in r for r in rec.refused)


def test_masking_is_a_no_op_for_documents_without_checkboxes(texts):
    """A referral letter has no ballot boxes, so nothing should be hidden from the wall."""
    from databridge_intake.ground import mask_unticked_options
    t = texts["chit_01_meridian_referral.txt"]
    assert mask_unticked_options(t) == t


def test_masking_preserves_offsets(texts):
    """Pointers built against the masked view must still resolve against the original text."""
    from databridge_intake.ground import mask_unticked_options
    for name in ("chit_05_bluepeak_wellness_voucher.txt", "chit_07_mol_authorisation_pee226.txt"):
        assert len(mask_unticked_options(texts[name])) == len(texts[name])


def test_a_partially_refused_list_keeps_its_survivors_and_reports_the_loss(texts):
    """`unresolved` and `refused` stay disjoint: a list with one bad item is not "not on
    document" — it is populated, with a refusal explaining what was dropped."""
    from databridge_harness import FakeBackend, Harness
    payload = IntakeDraft(requested_tests=["Chest X-Ray", "Colonoscopy"]).model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts["chit_01_meridian_referral.txt"],
                         document_id="chit_01_meridian_referral.txt", harness=h)
    assert [t.value for t in rec.authorisation.requested_tests] == ["Chest X-Ray"]
    assert "authorisation.requested_tests" not in rec.unresolved
    assert any("Colonoscopy" in r for r in rec.refused)
    assert any("kept 1 item" in r for r in rec.refused)


# --- cross-field confusion ---------------------------------------------------------------
def test_an_nric_filed_as_a_policy_number_is_rejected(texts):
    """Both values are genuinely printed on chit_01, so the grounding wall alone accepts both.
    Filing a patient's NRIC as a policy number is caught on format instead.

    The reverse direction is deliberately NOT symmetric — see
    `extract.py:FIELD_SHAPE_RULES` and the two tests below."""
    from databridge_harness import FakeBackend, Harness

    payload = IntakeDraft(policy_number="S8010946C",        # actually the NRIC
                          claimed_id_number="MRD707314"     # actually the policy number
                          ).model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts["chit_01_meridian_referral.txt"],
                         document_id="chit_01_meridian_referral.txt", harness=h)

    assert rec.issuer.policy_number is None
    assert "issuer.policy_number" in rec.unresolved
    assert any("not a policy number" in r for r in rec.refused)


def test_the_identity_field_accepts_a_passport_not_only_an_nric(texts):
    """The asymmetry this protects: requiring NRIC shape on `claimed_id_number` would turn every
    foreign patient's genuine passport number into "not on document". CLAUDE.md names passports
    explicitly and the chit forms label the field "NRIC/Passport No."."""
    from databridge_harness import FakeBackend, Harness

    text = texts["chit_01_meridian_referral.txt"].replace("S8010946C", "E12345678")
    payload = IntakeDraft(claimed_id_number="E12345678").model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(text, document_id="chit_01_meridian_referral.txt", harness=h)

    assert rec.subject.claimed_id_number.value == "E12345678"
    assert "subject.claimed_id_number" not in rec.unresolved


def test_the_identity_field_still_refuses_an_unclassifiable_identifier(texts):
    """Shape screening on that field is loosened, not removed: a value that is not plausibly any
    identity-document number at all is still refused rather than filed as the patient's ID."""
    from databridge_harness import FakeBackend, Harness

    # Genuinely printed on the letter, so the grounding wall admits it; the shape rule is the
    # only thing standing between it and the patient's identity field.
    payload = IntakeDraft(claimed_id_number="eb.uw@mail.meridianlife.com.sg").model_dump_json()
    h = Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")
    rec = extract_intake(texts["chit_01_meridian_referral.txt"],
                         document_id="chit_01_meridian_referral.txt", harness=h)

    assert rec.subject.claimed_id_number is None
    assert "subject.claimed_id_number" in rec.unresolved
    assert any("identity document number" in r for r in rec.refused)


def test_the_correctly_assigned_values_still_pass(records):
    rec = records["chit_01_meridian_referral.txt"]
    assert rec.issuer.policy_number.value == "MRD707314"
    assert rec.subject.claimed_id_number.value == "S8010946C"


def test_shape_rules_do_not_reject_legitimate_reference_formats(records):
    """Policy and certificate numbers vary widely across issuers; only the NRIC shape is barred."""
    assert records["chit_03_everwell_group_invitation.txt"].issuer.reference_number.value == "C-58393"
    assert records["chit_04_bluepeak_underwriting.txt"].issuer.reference_number.value == "BLP795612"
    assert records["chit_06_northstar_further_req.txt"].issuer.reference_number.value == "NST245095"
