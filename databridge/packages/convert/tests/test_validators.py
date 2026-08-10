"""Validation framework: row-count law, EV-QC accounting, EV-RT, E3 distribution, policy."""
from __future__ import annotations

from databridge_convert.validators import (
    DistributionCheck,
    QuarantineAccounting,
    RoundTrip,
    RowCountLaw,
    ValidationProfile,
)


def test_row_count_law_1to1():
    assert RowCountLaw().validate(source_rows=10, output_rows=9, quarantined=1).passed
    assert not RowCountLaw().validate(source_rows=10, output_rows=8, quarantined=1).passed


def test_quarantine_accounting():
    assert QuarantineAccounting().validate(source_rows=10, output_rows=9, quarantined=1).passed
    assert not QuarantineAccounting().validate(source_rows=10, output_rows=9, quarantined=0).passed


def test_round_trip():
    assert RoundTrip().validate(original=[1, 2], roundtrip=[1, 2]).passed
    assert not RoundTrip().validate(original=[1, 2], roundtrip=[1, 3]).passed


def test_distribution_categorical_containment():
    assert DistributionCheck().validate(source_values=["a", "b"], output_values=["a"],
                                        kind="categorical").passed
    bad = DistributionCheck().validate(source_values=["a", "b"], output_values=["a", "c"],
                                       kind="categorical")
    assert not bad.passed and "invented" in bad.detail            # E3: no invented categoricals


def test_distribution_ks():
    same = DistributionCheck().validate(source_values=list(range(100)),
                                        output_values=list(range(100)), kind="numeric")
    assert same.passed
    diff = DistributionCheck().validate(source_values=[0.0] * 100,
                                        output_values=[1000.0] * 100, kind="numeric")
    assert not diff.passed


def test_profile_override_policy():
    prof = ValidationProfile(overrides={"distribution": "hard_fail"})
    res = prof.run(DistributionCheck(), source_values=["a"], output_values=["a"],
                   kind="categorical")
    assert res.policy == "hard_fail" and res.passed
