"""`describe()` returns a capability matrix, not a schema (§3, spine D6).

A schema says what a source contains. It does not say what the source can PROVE, and planning on
the first while assuming the second is how a system ends up citing a mailbox that has since been
edited by its owner.

The invariants here all have the same shape: **a claim the deployment cannot honour is refused at
the point it is made**, not discovered when someone asks for a past instant.

`fixtures/packs/finance/sources.yaml` is read directly, because that file and this module are two
renderings of one set of facts and transcription is exactly how they would drift apart.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from databridge_adapter import (
    CursorDescription,
    EntityDescription,
    FieldDescription,
    SourceDescription,
    Verdict,
    source_description_from_yaml,
)
from databridge_core import Trust

FINANCE_SOURCES = (Path(__file__).resolve().parents[3]
                   / "fixtures" / "packs" / "finance" / "sources.yaml")


def description(**overrides) -> SourceDescription:
    base = dict(
        source_id="core_banking",
        capabilities=frozenset({"row_read", "structural_read"}),
        pinning_tier="T1",
        cursor=CursorDescription(kind="lsn", lossy=False, replay="forward_only"),
        deletes_detectable="soft_only",
        effective_retention={"txn": "P7Y", "party": "indefinite"},
        capture_required=True,
        egress_class="RESTRICTED_INTERNAL",
        reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.UNVERIFIABLE}),
    )
    base.update(overrides)
    return SourceDescription(**base)


# --- pinning claims -------------------------------------------------------------------

def test_a_t0_claim_needs_a_mechanism_and_a_retention_window():
    """Ordinary PostgreSQL is T1: a repeatable-read transaction gives a consistent read, not time
    travel. T0 is a claim about the DEPLOYMENT (spine D6)."""
    with pytest.raises(ValueError, match="t0_mechanism"):
        description(pinning_tier="T0")


def test_a_t0_claim_with_its_window_is_accepted():
    desc = description(pinning_tier="T0", t0_mechanism="point_in_time_recovery",
                       t0_retention_days=35)
    assert desc.pinning_tier == "T0"


def test_a_non_t0_source_may_not_carry_t0_machinery():
    """A retention window beside a T1 tier reads as a time-travel promise nothing honours."""
    with pytest.raises(ValueError, match="T0 machinery"):
        description(pinning_tier="T1", t0_retention_days=35)


def test_a_t3_source_must_require_capture():
    """An unversioned source cannot re-produce what it said, so a pointer into it that does not
    resolve against the Capture Store cites nothing checkable."""
    with pytest.raises(ValueError, match="require capture|T3"):
        description(pinning_tier="T3", capture_required=False,
                    cursor=CursorDescription(kind="none"))


# --- the rest of the matrix ------------------------------------------------------------

def test_effective_retention_must_be_declared():
    """'Unknown' is a planning input, so say it; omission is not a declaration."""
    with pytest.raises(ValueError, match="effective retention"):
        description(effective_retention={})


def test_an_adapter_must_advertise_at_least_one_capability():
    with pytest.raises(ValueError, match="no capabilities"):
        description(capabilities=frozenset())


def test_a_lossless_cursor_must_say_how_it_replays():
    with pytest.raises(ValueError, match="replay"):
        CursorDescription(kind="timestamp", lossy=False, replay="none")


def test_a_source_with_no_watermark_declares_kind_none():
    """Absence as a declaration, not as a blank."""
    assert CursorDescription(kind="none").replay == "none"


def test_duplicate_entities_are_refused():
    field = FieldDescription(name="notes", type="text", trust=Trust.UNTRUSTED)
    entity = EntityDescription(name="party", fields=(field,))
    with pytest.raises(ValueError, match="duplicate entities"):
        description(entities=(entity, entity))


# --- trust is per FIELD, never per source (spine D3) ------------------------------------

def test_trust_is_declared_per_field():
    """Ownership of the table is irrelevant: subject-supplied free text copied into a first-party
    column is attacker-writable."""
    entity = EntityDescription(
        name="party",
        key=("party_id",),
        fields=(FieldDescription(name="party_id", type="id", trust=Trust.TRUSTED),
                FieldDescription(name="legal_name", type="text", trust=Trust.TRUSTED),
                FieldDescription(name="notes", type="text", trust=Trust.UNTRUSTED,
                                 sensitive=True)))
    desc = description(entities=(entity,))
    assert desc.trust_of("party", "legal_name") is Trust.TRUSTED
    assert desc.trust_of("party", "notes") is Trust.UNTRUSTED


def test_a_field_cannot_omit_its_trust_class():
    """No default: an author who never thought about trust must not get a working description."""
    with pytest.raises(Exception):
        FieldDescription(name="notes", type="text")     # type: ignore[call-arg]


def test_an_undescribed_field_has_no_trust_class():
    """There is deliberately no fallback - guessing one is the fail-open the required label
    exists to prevent."""
    entity = EntityDescription(name="party",
                               fields=(FieldDescription(name="notes", type="text",
                                                        trust=Trust.UNTRUSTED),))
    with pytest.raises(KeyError):
        description(entities=(entity,)).trust_of("party", "secret_column")


def test_a_key_column_must_be_described():
    """A pointer cites the key, so an undescribed key is an uncitable row."""
    with pytest.raises(ValueError, match="key columns"):
        EntityDescription(name="party",
                          fields=(FieldDescription(name="notes", type="text",
                                                   trust=Trust.UNTRUSTED),),
                          key=("party_id",))


# --- the real estate ---------------------------------------------------------------------

@pytest.mark.skipif(not FINANCE_SOURCES.is_file(), reason="finance estate not present")
def test_every_finance_source_descriptor_builds_a_valid_description():
    """The shape a real estate declares must survive the type, or the type is describing something
    nobody has."""
    data = yaml.safe_load(FINANCE_SOURCES.read_text(encoding="utf-8"))
    built = []
    for entry in data["sources"]:
        desc = source_description_from_yaml(entry, capabilities=frozenset({"row_read"}))
        assert desc.source_id == entry["id"]
        assert desc.reachable_verdicts
        built.append(desc)
    assert len(built) == len(data["sources"])


@pytest.mark.skipif(not FINANCE_SOURCES.is_file(), reason="finance estate not present")
def test_the_estates_t3_sources_all_require_capture():
    data = yaml.safe_load(FINANCE_SOURCES.read_text(encoding="utf-8"))
    t3 = [e for e in data["sources"] if e["pinning_tier"] == "T3"]
    assert t3, "the finance estate is supposed to contain unversioned drops"
    for entry in t3:
        assert source_description_from_yaml(entry,
                                            capabilities=frozenset({"doc_read"})).capture_required


@pytest.mark.skipif(not FINANCE_SOURCES.is_file(), reason="finance estate not present")
def test_a_t0_capable_source_is_still_declared_at_its_actual_tier():
    """``t0_capable: true`` means a deployment OPTION exists. core_banking declares T1 anyway, and
    the description must not quietly promote it."""
    data = yaml.safe_load(FINANCE_SOURCES.read_text(encoding="utf-8"))
    entry = next(e for e in data["sources"] if e["id"] == "core_banking")
    assert entry["t0_capable"] is True
    desc = source_description_from_yaml(entry, capabilities=frozenset({"row_read"}))
    # Read from the entry rather than hard-coded: the estate revised `core_banking` from T1 to T2
    # ("the source tier is the MINIMUM over its streams"), and a test that pins the tier tests the
    # fixture's current opinion rather than the rule it exists to check. The RULE is that a
    # `t0_capable` source is described at its declared tier and carries no T0 machinery unless
    # that tier is actually T0.
    assert desc.pinning_tier == str(entry["pinning_tier"]).upper() != "T0"
    assert desc.t0_retention_days is None
