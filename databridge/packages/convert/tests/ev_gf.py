"""EV-GF scorer — grounded-generation factuality (09_FINAL_PRD §23). Gate: **0 fabrications escape.**

Two adversarial suites over the C17 wall (claim_check, §12.3a):
  * **absent-fact** (≥5): a generated text that asserts a value for a field NOT in the fact set —
    must be caught (the faithful counterpart uses the fallback and passes);
  * **numeric-paraphrase**: a hedged/spelled value ("about a hundred", "approximately $1,350",
    "nearly 50%") — the launder-a-fabrication-as-an-estimate trap — must be caught.

Metric: ``escapes`` = fabrications that pass the wall (gate 0). Canary: disabling the wall must
LEAK the seeded fabrications (so the 0 is real, not vacuous). Deterministic — no model.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from databridge_core import SourcePointer

from databridge_convert.generate import Fact, check_claims, extract_facts


def _ptr(cell="B1"):
    return SourcePointer(source_id="wb", kind="workbook_cell", workbook_sha="x", sheet="S", cell=cell)


def _invoice_facts() -> list[Fact]:
    rec = {"customer": "Acme Industries", "total": 1350.00, "date": dt.date(2024, 3, 15),
           "tax_rate": 8.0}
    return extract_facts(rec, kinds={"customer": "name", "total": "money", "date": "date",
                                     "tax_rate": "percent"},
                         pointers={k: _ptr() for k in ("customer", "total", "date", "tax_rate")})


@dataclass
class GfCase:
    name: str
    facts: list[Fact]
    output: str
    is_fabrication: bool       # ground truth
    suite: str


def absent_fact_cases() -> list[GfCase]:
    f = _invoice_facts()                                # has customer/total/date/tax_rate; NO discount/refund/shipping
    return [
        GfCase("absent_discount_fabricated", f,
               "Acme Industries received a discount of $250.00.", True, "absent_fact"),
        GfCase("absent_discount_fallback", f,
               "I don't have that information in the source data.", False, "absent_fact"),
        GfCase("absent_refund_fabricated", f,
               "A refund of $500.00 was issued on 2024-04-01.", True, "absent_fact"),
        GfCase("absent_shipping_fabricated", f,
               "Shipping cost was $42.00.", True, "absent_fact"),
        GfCase("absent_count_fabricated", f,
               "The account has 12,000 open tickets.", True, "absent_fact"),
        # the symbol-less-money dodge: a fabricated amount spelling its unit instead of using "$"
        GfCase("absent_symbolless_money", f,
               "A discount of 250 dollars was applied.", True, "absent_fact"),
        GfCase("present_only_faithful", f,
               "Acme Industries was invoiced $1,350.00 on 2024-03-15 at 8.0% tax.", False, "absent_fact"),
    ]


def numeric_paraphrase_cases() -> list[GfCase]:
    f = _invoice_facts()
    return [
        GfCase("hedged_money", f, "The total was approximately $1,350.00.", True, "paraphrase"),
        GfCase("spelled_about_hundred", f, "There were about a hundred line items.", True, "paraphrase"),
        GfCase("nearly_percent", f, "Tax was nearly 50%.", True, "paraphrase"),
        GfCase("around_grouped", f, "Roughly 2,000 units shipped.", True, "paraphrase"),
        # multi-word hedge laundering a small (non-fact-shaped) count as an estimate
        GfCase("upwards_of_count", f, "There were upwards of 50 disputes.", True, "paraphrase"),
        GfCase("as_many_as_count", f, "As many as 47 claims were filed.", True, "paraphrase"),
        GfCase("exact_faithful", f, "The total is $1,350.00 at 8.0% tax.", False, "paraphrase"),
    ]


def run_ev_gf() -> dict:
    cases = absent_fact_cases() + numeric_paraphrase_cases()
    escapes, false_alarms, caught = [], [], 0
    for c in cases:
        detected = not check_claims(c.output, c.facts).ok
        if c.is_fabrication and not detected:
            escapes.append(c.name)                      # a fabrication slipped the wall
        if (not c.is_fabrication) and detected:
            false_alarms.append(c.name)                 # a faithful text wrongly flagged
        if c.is_fabrication and detected:
            caught += 1

    # CANARY (real, not a tautology): each fabrication must be caught FOR THE RIGHT REASON — the
    # value is absent. Prove it: re-run the check with the flagged token GROUNDED (added to the
    # allow-set); the value-based violation must then VANISH. If it does, the wall caught it
    # precisely because the value wasn't a fact (a hedge/spelled catch is inherent and also valid).
    n_fab = sum(1 for c in cases if c.is_fabrication)
    caught_for_right_reason = sum(1 for c in cases
                                  if c.is_fabrication and _caught_due_to_absence(c))

    return {
        "n_cases": len(cases), "n_fabrications": n_fab,
        "escapes": escapes, "n_escapes": len(escapes),
        "false_alarms": false_alarms, "n_false_alarms": len(false_alarms),
        "caught": caught,
        "gate_zero_escapes": len(escapes) == 0,
        "caught_for_right_reason": caught_for_right_reason,
        "canary_valid": caught_for_right_reason == n_fab,
        "absent_fact_count": sum(1 for c in cases if c.suite == "absent_fact"),
        "paraphrase_count": sum(1 for c in cases if c.suite == "paraphrase"),
    }


_VALUE_KINDS = {"fabricated_number", "fabricated_date"}


def _caught_due_to_absence(case: GfCase) -> bool:
    """The fabrication is caught, AND grounding the flagged value makes the VALUE violation
    disappear (so absence is the cause) — or it was caught by an inherent hedge/spelled violation.
    This is the EV-DX canary shape: disabling the specific defence leaks the specific fabrication."""
    base = check_claims(case.output, case.facts)
    if base.ok:
        return False                                    # not caught at all → canary fails
    value_tokens = [v.token for v in base.violations if v.kind in _VALUE_KINDS]
    if not value_tokens:
        return True                                     # caught by hedge/spelled — inherent, valid
    relaxed = check_claims(case.output, case.facts, allow=value_tokens)
    # grounding the value must remove the value-based violations (any leftover is hedge/spelled)
    return all(v.kind not in _VALUE_KINDS for v in relaxed.violations)
