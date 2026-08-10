"""C17 claim_check (§12.3a) + fact_extractor (§12.1) — adversarial-first.

The wall's danger is a FALSE NEGATIVE (a fabrication passing), so the suite leads with the
fabrications that MUST be caught: an invented amount, an invented date, a hedged real number, and
the spelled-out paraphrase ("about a hundred"). Then the happy path (a faithful sentence passes).
"""
from __future__ import annotations

import datetime as dt

from databridge_core import SourcePointer

from databridge_convert.generate import (
    Fact,
    aggregate_fact,
    check_claims,
    extract_facts,
    render_fact,
    sum_fact,
)


def _ptr(cell="A1"):
    return SourcePointer(source_id="wb", kind="workbook_cell", workbook_sha="x",
                         sheet="S", cell=cell)


def _facts():
    rec = {"customer": "Acme Industries", "total": 1350.00, "date": dt.date(2024, 3, 15),
           "tax_rate": 8.0}
    return extract_facts(rec, kinds={"customer": "name", "total": "money", "date": "date",
                                     "tax_rate": "percent"},
                         pointers={"customer": _ptr("B1"), "total": _ptr("B2"),
                                   "date": _ptr("B3"), "tax_rate": _ptr("B4")})


# === ADVERSARIAL (must be caught) =================================================
def test_fabricated_amount_is_caught():
    facts = _facts()                      # total is $1,350.00
    out = "The invoice total is $9,999.00, due on 2024-03-15."   # 9,999 appears in NO fact
    res = check_claims(out, facts)
    assert not res.ok
    assert any(v.kind == "fabricated_number" and "9,999" in v.token for v in res.violations)


def test_fabricated_date_is_caught():
    facts = _facts()                      # date is 2024-03-15
    out = "Billed to Acme Industries on 2024-12-31."             # wrong date
    res = check_claims(out, facts)
    assert not res.ok
    assert any(v.kind == "fabricated_date" and v.token == "2024-12-31" for v in res.violations)


def test_hedged_real_number_is_caught():
    facts = _facts()                      # total IS $1,350.00 — but hedging it is still a violation
    out = "The total comes to approximately $1,350.00."
    res = check_claims(out, facts)
    assert not res.ok
    assert any(v.kind == "hedged_quantity" for v in res.violations)


def test_numeric_paraphrase_about_a_hundred_is_caught():
    facts = _facts()
    out = "There were about a hundred transactions in the period."
    res = check_claims(out, facts)
    assert not res.ok
    kinds = {v.kind for v in res.violations}
    assert "spelled_number" in kinds or "hedged_quantity" in kinds


def test_fabricated_percent_is_caught():
    facts = _facts()                      # tax_rate is 8.0%
    out = "A tax of 47.5% was applied."
    res = check_claims(out, facts)
    assert not res.ok
    assert any("47.5%" in v.token for v in res.violations)


def test_substring_masking_hole_closed():
    """A fabricated quantity that SHARES A SUFFIX with a real fact must NOT be masked away by it:
    fact '8.0%' must not let the fabricated '18.0%' escape (the S→U substring-masking false
    negative — the analogue of C15's grounding-wall B-bugs). [adversarial review]"""
    facts = _facts()                                   # tax_rate fact is 8.0%
    res = check_claims("A tax of 18.0% was charged.", facts)
    assert not res.ok
    assert any(v.token == "18.0%" for v in res.violations)
    # the real fact still passes cleanly
    assert check_claims("A tax of 8.0% was charged.", facts).ok


def test_symbolless_money_and_unit_number_caught():
    """A fabricated amount that drops the symbol ('250 dollars', '8 percent') must still be caught
    — it can't dodge the wall by spelling its unit. [adversarial review HIGH]"""
    facts = _facts()
    assert not check_claims("A discount of 250 dollars was applied.", facts).ok
    assert not check_claims("A fee of 8 percent applies.", facts).ok


def test_multiword_hedge_on_small_count_caught():
    """Multi-word hedges laundering a small (non-fact-shaped) count as an estimate. [review HIGH]"""
    facts = _facts()
    for out in ("There were upwards of 50 disputes.", "As many as 47 claims were filed.",
                "Give or take 200 units shipped.", "North of 90 refunds were issued."):
        assert not check_claims(out, facts).ok, out


# === HAPPY PATH (must pass) =======================================================
def test_faithful_sentence_passes():
    facts = _facts()
    out = ("Invoice for Acme Industries dated 2024-03-15: the total is $1,350.00 "
           "at a tax rate of 8.0%.")
    res = check_claims(out, facts)
    assert res.ok, [v.token for v in res.violations]
    assert "$1,350.00" in res.covered and "2024-03-15" in res.covered


def test_structural_small_integer_not_flagged():
    facts = _facts()
    out = "The top 5 customers by total are listed below."   # "5" is structural, not a fact
    res = check_claims(out, facts)
    assert res.ok, [v.token for v in res.violations]


def test_longest_match_consumes_grouped_number_whole():
    f = [Fact("F1", "rows", "int", "1,234,567", 1234567, _ptr())]
    out = "We processed 1,234,567 rows."
    res = check_claims(out, f)
    assert res.ok, [v.token for v in res.violations]


# === fact_extractor ===============================================================
def test_extract_facts_renders_canonically_and_grounds():
    facts = _facts()
    by_label = {f.label: f for f in facts}
    assert by_label["total"].rendered == "$1,350.00"
    assert by_label["date"].rendered == "2024-03-15"
    assert by_label["tax_rate"].rendered == "8.0%"
    assert by_label["customer"].rendered == "Acme Industries"
    # every fact is grounded (P4)
    for f in facts:
        assert f.grounded.pointer.kind == "workbook_cell"
        assert f.grounded.value == f.rendered


def test_extract_facts_skips_absent_fields():
    rec = {"customer": "Acme", "total": None}      # total absent → no fact (fallback path covers it)
    facts = extract_facts(rec, kinds={"customer": "name", "total": "money"},
                          pointers={"customer": _ptr(), "total": _ptr()})
    assert [f.label for f in facts] == ["customer"]


def test_extract_facts_requires_pointer():
    import pytest
    with pytest.raises(ValueError):
        extract_facts({"x": 1}, kinds={"x": "int"}, pointers={})


def test_aggregate_fact_carries_derivation():
    rows = [{"amt": 100}, {"amt": 250}, {"amt": 50}]
    ptrs = [_ptr("B2"), _ptr("B3"), _ptr("B4")]
    f = sum_fact("F1", "total_amount", rows, "amt", ptrs, kind="money")
    assert f.rendered == "$400.00"
    assert f.pointer.kind == "derived" and "SUM(amt)" in f.pointer.derivation
    assert len(f.pointer.derived_from) == 3        # grounded to each source row
