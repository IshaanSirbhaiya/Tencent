"""sentinel_to_null must NOT conflate distinct types (review fix): 1.0 != int sentinel 1,
and 1 != bool sentinel True. Only an exact type+value match nulls."""
from __future__ import annotations

from databridge_convert.rules.coercions import sentinel_to_null


def test_exact_type_match_nulls():
    assert sentinel_to_null(-999, sentinels=[-999]) is None
    assert sentinel_to_null("N/A", sentinels=["N/A", "NULL"]) is None
    assert sentinel_to_null("", sentinels=[""]) is None


def test_cross_type_does_not_null():
    assert sentinel_to_null(1.0, sentinels=[1]) == 1.0        # float ≠ int sentinel
    assert sentinel_to_null(1, sentinels=[True]) == 1         # int ≠ bool sentinel (1==True trap)
    assert sentinel_to_null("", sentinels=["N/A"]) == ""      # empty string only nulled if listed
    assert sentinel_to_null(0, sentinels=[""]) == 0
