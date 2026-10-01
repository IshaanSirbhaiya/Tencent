"""`verify()` returns the lattice, and `DRIFT_BENIGN` has to be earned (spine D6, §5).

The split that decides whether verification survives contact with production: `DRIFT_BENIGN` must
not page anyone and `DRIFT_MATERIAL` must always page someone. The trap is that `DRIFT_BENIGN` is
only *detectable* where a cheap entity-level change signal already exists - `xmin`, an ETag, a
`historyId`, an mtime. An adapter without one that emits it anyway is telling an operator "we
looked and it was fine" on the strength of a hash that covers only the columns it read.
"""
from __future__ import annotations

import pytest

from databridge_adapter import (
    ADVERTISED_ONLY_VERDICTS,
    UNIVERSAL_VERDICTS,
    CapabilityNotAdvertised,
    CursorDescription,
    SourceDescription,
    Verdict,
    assert_verdict_advertised,
)


def description(**overrides) -> SourceDescription:
    base = dict(
        source_id="core_banking",
        capabilities=frozenset({"row_read"}),
        pinning_tier="T1",
        cursor=CursorDescription(kind="lsn", lossy=False, replay="forward_only"),
        deletes_detectable="soft_only",
        effective_retention={"default": "P7Y"},
        capture_required=True,
        egress_class="RESTRICTED_INTERNAL",
        reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.DRIFT_MATERIAL,
                                      Verdict.UNVERIFIABLE}),
    )
    base.update(overrides)
    return SourceDescription(**base)


def test_the_lattice_has_exactly_eight_states():
    assert len(Verdict) == 8


def test_exactly_one_verdict_pages_someone():
    escalating = [v for v in Verdict if v.must_escalate]
    assert escalating == [Verdict.DRIFT_MATERIAL]


def test_verified_as_of_capture_is_a_positive_outcome():
    """It is the DESIGNED outcome for a captured T1/T3 artefact, not a degraded one."""
    assert Verdict.VERIFIED_AS_OF_CAPTURE.is_positive
    assert not Verdict.UNVERIFIABLE.is_positive
    assert not Verdict.DRIFT_BENIGN.is_positive


def test_unverifiable_is_not_a_failure():
    """A tier that cannot answer says so; a test demanding VERIFIED everywhere would assert
    something the design does not claim (§11.2)."""
    assert not Verdict.UNVERIFIABLE.must_escalate


def test_drift_benign_is_the_only_advertised_only_verdict():
    assert ADVERTISED_ONLY_VERDICTS == frozenset({Verdict.DRIFT_BENIGN})
    assert UNIVERSAL_VERDICTS | ADVERTISED_ONLY_VERDICTS == set(Verdict)


def test_an_adapter_without_the_signal_may_not_emit_drift_benign():
    with pytest.raises(CapabilityNotAdvertised, match="detects_benign_drift"):
        assert_verdict_advertised(Verdict.DRIFT_BENIGN, description())


def test_an_adapter_with_the_signal_may_emit_drift_benign():
    desc = description(
        detects_benign_drift=True, benign_drift_signal="xmin",
        reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.DRIFT_BENIGN,
                                      Verdict.DRIFT_MATERIAL, Verdict.UNVERIFIABLE}))
    assert assert_verdict_advertised(Verdict.DRIFT_BENIGN, desc) is Verdict.DRIFT_BENIGN


def test_a_verdict_outside_the_declared_set_is_refused():
    """A verdict the capability matrix says is unreachable is a bug in the adapter, not a
    discovery about the source."""
    with pytest.raises(CapabilityNotAdvertised, match="reachable_verdicts"):
        assert_verdict_advertised(Verdict.VANISHED, description())


def test_drift_material_is_always_permitted_where_declared():
    assert assert_verdict_advertised(Verdict.DRIFT_MATERIAL, description())


def test_a_description_may_not_list_drift_benign_without_advertising_it():
    """The two declarations cannot disagree: listing it as reachable IS advertising it."""
    with pytest.raises(ValueError, match="detects_benign_drift"):
        description(reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.DRIFT_BENIGN}))


def test_advertising_benign_drift_requires_naming_the_signal():
    with pytest.raises(ValueError, match="naming the signal"):
        description(detects_benign_drift=True,
                    reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.DRIFT_BENIGN}))
