"""THE RECIPIENT RULE, tested. An address may come from two places, and a model is neither.

CLAUDE.md §Hard constraints 1 says the system never decides who someone is. Choosing which person
to email is that decision wearing a different hat: an agent that can pick a recipient from a
document has resolved an identity, whatever the code calls the variable.

The structural guard is a signature, exactly as it is for
``intake.claim.build_claim_draft`` — and that test says why in one line worth repeating here: *a
comment saying "only use committed values" is what the previous version had in spirit and did not
have in fact.* So the parameter list is the assertion, and
``test_build_outbound_draft_has_no_parameter_an_address_can_arrive_through`` is the version of it
for this module.

Everything else here exists because the signature alone would be satisfied by a ``Recipient`` that
anything could construct. The type has to be as narrow as the parameter.
"""
from __future__ import annotations

import datetime as dt
import inspect
import re
import typing

import pytest

from databridge_core import SourcePointer
from databridge_outbox import drafting, models, recipients
from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.models import Recipient, RecipientRefused
from databridge_outbox.recipients import (
    ConflictingSelections,
    NoAddressOnFile,
    NoCommittedSelection,
    recipient_from_committed_ledger,
    recipient_typed_by_human,
)


# --- the structural guard ----------------------------------------------------------


def test_build_outbound_draft_has_no_parameter_an_address_can_arrive_through():
    """The fix for this class of bug is a signature, not a convention.

    If a `to`, `address`, `email`, `recipients` or `cc` parameter is ever added here, a
    model-supplied recipient can reach a mailbox — so the parameter list itself is the assertion,
    the same shape as `test_build_claim_draft_cannot_be_handed_the_working_draft`.
    """
    params = inspect.signature(build_outbound_draft).parameters
    assert set(params) == {"monitor", "observation", "recipient", "created_at"}, (
        f"build_outbound_draft takes {sorted(params)}; anything beyond the monitor, the "
        f"observation, an already-established Recipient and a clock is a route through which a "
        f"model-supplied address can arrive")


def test_the_recipient_parameter_is_a_recipient_and_not_a_string():
    """A `recipient: str` would satisfy the test above and defeat the rule entirely."""
    params = inspect.signature(build_outbound_draft).parameters
    # `from __future__ import annotations` makes every annotation a string, so resolve them —
    # asserting against the string "Recipient" would pass for a class of the same name.
    hints = typing.get_type_hints(build_outbound_draft)
    assert hints["recipient"] is Recipient
    assert params["recipient"].default is inspect.Parameter.empty, (
        "a defaulted recipient is a recipient nobody chose")
    stringy = [n for n in params if hints.get(n) is str]
    assert stringy == [], (
        f"{stringy} are plain strings on the drafting API; an address is a string, and this "
        f"function must have nowhere for one to land")


def test_a_string_where_a_recipient_belongs_is_refused(table, coverage_observation):
    """Belt and braces on the signature: the runtime says no too, loudly."""
    with pytest.raises(models.DraftRefused, match="not a Recipient"):
        build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                             recipient="attacker@example.com")   # type: ignore[arg-type]


def test_the_drafting_module_names_no_address_shaped_parameter():
    """A second reading of the same property, in case the signature grows a `**kwargs`."""
    source = inspect.getsource(drafting)
    for forbidden in (r"\bto\s*[:=]\s*str", r"\baddress\s*[:=]\s*str", r"\bemail\s*[:=]\s*str",
                      r"\bcc\b", r"\bbcc\b", r"\*\*kwargs"):
        assert not re.search(forbidden, source), (
            f"drafting.py matches {forbidden!r} — an address must have nowhere to arrive")


# --- the type is as narrow as the parameter ----------------------------------------


def test_a_recipient_cannot_simply_be_constructed():
    """The door is `recipients.py`, and there is no other."""
    pointer = SourcePointer(source_id="evidence_ledger:x", kind="table_cell",
                            table="evidence", column="selected_patient_id", row_id="1")
    with pytest.raises(RecipientRefused, match="two places"):
        Recipient(address="attacker@example.com", origin="committed_ledger",
                  chosen_by="Nurse Lim", pointer=pointer)


def test_a_recipient_cannot_be_parsed_out_of_json():
    """THE model's channel. A model's output arrives as a JSON object parsed into a schema; if
    `Recipient` could be one of those schemas, every other check here is decoration."""
    payload = {"address": "attacker@example.com", "origin": "typed_by_human",
               "chosen_by": "Nurse Lim",
               "pointer": {"source_id": "front_desk_entry:x", "kind": "table_cell",
                           "table": "outbox_recipient_entry", "column": "address", "row_id": "1"}}
    with pytest.raises(RecipientRefused):
        Recipient.model_validate(payload)
    with pytest.raises(RecipientRefused):
        Recipient.model_validate_json(__import__("json").dumps(payload))


def test_a_recipient_cannot_be_parsed_out_of_a_non_mapping():
    with pytest.raises(RecipientRefused, match="never parsed"):
        Recipient.model_validate("attacker@example.com")


def test_there_are_exactly_two_doors():
    """A third factory is a third door. Counted from the source, so adding one is a red suite."""
    source = inspect.getsource(recipients)
    uses = re.findall(r"_RECIPIENT_TOKEN", source)
    # once in the import, once per factory.
    assert len(uses) == 3, (
        f"the construction token appears {len(uses)} times in recipients.py; there are two doors "
        f"onto the recipient decision and adding a third is not a refactor")
    public = {n for n in vars(recipients) if not n.startswith("_") and callable(getattr(recipients, n))}
    assert {n for n in public if n.startswith("recipient_")} == {
        "recipient_from_committed_ledger", "recipient_typed_by_human"}


def test_no_module_outside_recipients_holds_the_token():
    for module in (models, drafting):
        source = inspect.getsource(module)
        if module is models:
            continue        # it DEFINES the token; that is the point of it being module-private
        assert "_RECIPIENT_TOKEN" not in source, f"{module.__name__} can forge a recipient"


# --- door (a): a committed ledger row -----------------------------------------------


def test_the_ledger_door_takes_no_address(ledger):
    params = set(inspect.signature(recipient_from_committed_ledger).parameters)
    assert params == {"intake_id", "ledger", "registry"}, (
        f"recipient_from_committed_ledger takes {sorted(params)}; an address parameter here would "
        f"make 'the address on file' mean 'whatever the caller passed'")


def test_the_ledger_door_returns_the_address_on_file(intake, ledger):
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    assert recipient.origin == "committed_ledger"
    assert "@" in recipient.address
    assert recipient.chosen_by == "staff:front_desk_01"


def test_the_ledger_recipient_cites_both_the_attestation_and_the_registry_cell(intake, ledger):
    """The two citations are not interchangeable and neither is sufficient.

    The ledger row says a named human checked an NRIC against a face and picked this record. The
    registry cell says this is the address written against that record. Citing only the registry
    would imply the system knows whose address it is — the identity claim it must never make.
    """
    from databridge_intake.registry import by_patient_id

    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    assert recipient.pointer.kind == "derived"
    cited = recipient.pointer.derived_from or []
    kinds = {(p.table, p.column) for p in cited}
    assert ("evidence", "selected_patient_id") in kinds, "the human's attestation is not cited"
    assert ("patient_registry", "Email") in kinds, "the registry cell is not cited"

    # And the registry citation RESOLVES: it is the very cell the registry read this address from,
    # for the patient the ledger row names — not a pointer that merely looks like one.
    registry_pointer = next(p for p in cited if p.table == "patient_registry")
    ledger_pointer = next(p for p in cited if p.table == "evidence")
    patient_id = ledger.entries()[int(ledger_pointer.row_id) - 1]["selected_patient_id"]
    patient = by_patient_id()[patient_id]
    assert patient.email.pointer == registry_pointer
    assert patient.email.value == recipient.address


def test_the_ledger_door_refuses_an_intake_nobody_committed(ledger):
    from databridge_intake.ledger import EvidenceLedger

    with pytest.raises(NoCommittedSelection):
        recipient_from_committed_ledger("intake:never-confirmed", EvidenceLedger())


def test_the_ledger_door_refuses_when_the_live_rows_name_two_patients(intake, ledger):
    """A correction that changed WHO this is leaves two live selections. The system stops.

    Taking the newest and mailing it would be the machine resolving a contradiction about who
    someone is — the one thing it must never do, and here it would do it by sending mail.
    """
    row = ledger.entries(intake.intake_id)[0]
    ledger.conn.execute(
        "INSERT INTO evidence (intake_id, field_path, value_json, source_pointer_json, "
        "extracted_at, confirmed_by, confirmed_at, selected_patient_id) VALUES (?,?,?,?,?,?,?,?)",
        (intake.intake_id, "issuer.issuer_code", row["value_json"], row["source_pointer_json"],
         row["extracted_at"], "staff:front_desk_02", row["confirmed_at"], "pat:someone-else"))
    ledger.conn.commit()
    with pytest.raises(ConflictingSelections, match="more than one selected patient"):
        recipient_from_committed_ledger(intake.intake_id, ledger)


def test_the_ledger_door_refuses_a_patient_with_no_address_on_file(intake, ledger):
    """No address means no draft. It does not mean "find a plausible one"."""
    from databridge_core import GroundedValue, SourcePointer as SP

    patient_id = ledger.entries(intake.intake_id)[0]["selected_patient_id"]
    from databridge_intake.registry import by_patient_id

    real = by_patient_id()[patient_id]
    blanked = real.model_copy(update={"email": GroundedValue(
        value="", pointer=SP(source_id="patient_registry", kind="table_cell",
                             table="patient_registry", column="Email", row_id=patient_id))})
    with pytest.raises(NoAddressOnFile, match="no email address on file"):
        recipient_from_committed_ledger(intake.intake_id, ledger,
                                        registry={patient_id: blanked})


def test_the_ledger_door_refuses_a_selection_that_is_not_in_the_registry(intake, ledger):
    with pytest.raises(NoAddressOnFile, match="not in the registry"):
        recipient_from_committed_ledger(intake.intake_id, ledger, registry={})


# --- door (b): a human types it ------------------------------------------------------


def test_the_typed_door_records_who_typed_it():
    recipient = recipient_typed_by_human("patient@example.com", typed_by="Nurse Lim",
                                         typed_at=dt.datetime(2026, 8, 12, 9, 14,
                                                              tzinfo=dt.timezone.utc))
    assert recipient.origin == "typed_by_human"
    assert recipient.chosen_by == "Nurse Lim"
    assert recipient.pointer.source_id == "front_desk_entry:Nurse Lim"
    assert recipient.pointer.table == "outbox_recipient_entry"


@pytest.mark.parametrize("who", ["agent", "system", "walnut", "AI", " ", "", "dispatcher"])
def test_the_typed_door_refuses_a_machine_as_the_typist(who):
    """An arbitrary address is fine as long as a PERSON owns having entered it."""
    with pytest.raises(models.NotApproved):
        recipient_typed_by_human("patient@example.com", typed_by=who)


def test_the_machine_actor_list_agrees_with_the_agent_packages():
    """Two copies of a safety list drift, and the one that drifts is the one nobody reads.

    The list is duplicated on purpose — importing `databridge_agent` here would create exactly the
    dependency the design exists to avoid — so the agreement is asserted instead.
    """
    from databridge_agent.sealed import _NOT_A_HUMAN

    assert _NOT_A_HUMAN <= models.NOT_A_HUMAN, (
        f"the agent refuses {sorted(_NOT_A_HUMAN - models.NOT_A_HUMAN)} as an attester and the "
        f"outbox would accept them as an approver")


# --- an address has to be one address, and only headers it declares ------------------


@pytest.mark.parametrize("address", [
    "patient@example.com\r\nBcc: attacker@evil.test",
    "patient@example.com\nBcc: attacker@evil.test",
    "patient@example.com, attacker@evil.test",
    "patient@example.com; attacker@evil.test",
    "Patient <patient@example.com>",
    "not-an-address",
    "@example.com",
    "patient@",
])
def test_an_address_that_could_carry_a_second_addressee_is_refused(address):
    """One draft addresses one person. "Who else was on that email" is not a question a clinic
    should have to ask, and a CR/LF is how a Bcc gets appended to one."""
    with pytest.raises(models.OutboxError):
        recipient_typed_by_human(address, typed_by="Nurse Lim")


def test_origin_and_pointer_must_make_the_same_claim():
    """A `typed_by_human` recipient citing the ledger would claim a human attested to an identity
    that nobody attested to."""
    wrong = SourcePointer(source_id="evidence_ledger:x", kind="table_cell", table="evidence",
                          column="selected_patient_id", row_id="1")
    with pytest.raises(RecipientRefused, match="same claim"):
        Recipient(via_=models._RECIPIENT_TOKEN, address="p@example.com",
                  origin="typed_by_human", chosen_by="Nurse Lim", pointer=wrong)
