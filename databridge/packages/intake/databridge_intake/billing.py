"""Billing draft — who pays, for what, and what the patient may owe.

**Every output of this module is a draft.** ``BillingDraft.status`` is ``Literal["draft"]``: there
is no code path that produces a final determination, because a bill is a financial claim against a
person and a machine reading a letter is not authority to make one. Staff approve it.

The numbers are honest about their own uncertainty:

* Prices come from ``coverage_rules.yaml``'s ``list_price_sgd``, which is **illustrative synthetic
  data** (PLAN.md §6.5). Every draft says so in ``caveats`` — a plausible-looking dollar figure
  with no such disclaimer is the single easiest thing for a demo to get wrong.
* A test the catalogue does not price contributes ``None``, not ``0``. A missing price is unknown,
  and silently treating it as free understates what the patient will be asked for at the counter.
* A test the catalogue does not even *recognise* is not asserted as patient-payable — coverage for
  it was never determined, so it lands in its own ``unknown_coverage_items`` bucket, not in
  ``patient_payable_items``. Whenever that bucket is non-empty the printed total is marked
  ``TOTAL_IS_LOWER_BOUND``, because it may not include cost the patient will in fact owe once staff
  price the item by hand.
* When coverage is ``UNDETERMINED`` (chit 8) nothing is billed to anyone: the draft says the payer
  could not be established from the document and leaves the total unknown.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .models import (BillingDraft, BillingParty, CoverageDetermination, CoverageStatus,
                     IntakeRecord)
from .rules import CoverageRules, Package, rules

SYNTHETIC_PRICE_CAVEAT = (
    "Prices are illustrative synthetic data from coverage_rules.yaml, not a Parkway Shenton "
    "price list. This draft must be checked against the live tariff before it is charged.")



def covered_by_rule(rule: Package, in_package: bool) -> bool:
    """Is a catalogue-matched test covered under this rule?

    A ``tests_from_document`` rule takes its scope FROM the document — a referral letter naming
    three tests authorises those three — so every catalogue-matched test printed on it is covered.
    Otherwise only what the package lists is.

    One expression, in one place, because ``claim.py`` re-derives the same split from committed
    ledger rows. Two copies of this rule would drift, and the drift would show up as a claim
    submitted to an insurer for tests the billing card told the patient they owed for.
    """
    return bool(rule.tests_from_document or in_package)


def _line(test) -> dict:
    return {
        "canonical": test.canonical,
        "as_printed": test.as_printed,
        "in_package": test.in_package,
        "list_price_sgd": (str(test.list_price_sgd)
                           if test.list_price_sgd is not None else None),
        "source_field": test.source_field,
    }


def draft_billing(record: IntakeRecord,
                  coverage: CoverageDetermination,
                  *,
                  table: Optional[CoverageRules] = None) -> BillingDraft:
    """Split the authorised tests into covered and patient-payable, and total what is knowable."""
    table = table or rules()
    caveats: list[str] = [SYNTHETIC_PRICE_CAVEAT]

    if coverage.status is CoverageStatus.UNDETERMINED:
        # No rule fired, so there is no payer to name. Defaulting to the patient here would hand a
        # bill to someone whose insurer simply did not state a package code.
        caveats.append(
            "coverage could not be determined from this document, so the payer is unknown — "
            "confirm with the issuer before charging anyone")
        return BillingDraft(billed_to="unknown", caveats=caveats,
                            estimated_patient_payable_sgd=None)

    rule = next((p for p in table.packages if p.id == coverage.matched_rule_id), None)
    if rule is None:
        # The self-pay rule (walk-in) is not in `packages`; it is its own entry.
        self_pay = table.self_pay
        if coverage.matched_rule_id == self_pay.id:
            return BillingDraft(
                billed_to=self_pay.billing.covered_by.value,
                patient_payable_items=[],
                estimated_patient_payable_sgd=None,
                caveats=caveats + ["walk-in self-pay: charges are the clinic's standard tariff "
                                   "for the consultation actually performed"])
        caveats.append(f"no billing rule found for {coverage.matched_rule_id!r}")
        return BillingDraft(billed_to="unknown", caveats=caveats)

    payer = rule.billing.covered_by
    issuer = table.issuer(coverage.issuer_code)

    covered, payable, unknown_coverage = [], [], []
    unpriced: list[str] = []
    seen: set[str] = set()
    for test in coverage.authorised_tests:
        line = _line(test)
        # One line per test. A test printed twice (a referral table and again in prose, or under
        # two aliases) would otherwise be listed — and priced — twice.
        key = test.canonical or f"as_printed:{test.as_printed.strip().lower()}"
        if key in seen:
            continue
        seen.add(key)
        if test.canonical is None:
            # Not in the catalogue: it is on the document, so staff must price it by hand. It is
            # neither covered nor confidently payable, and saying so is the only honest option.
            # Its own bucket — `unknown_coverage_items`, never `patient_payable_items` — because
            # putting it there would tell a UI the patient pays, when the honest statement is that
            # we do not know who does.
            unpriced.append(test.as_printed)
            unknown_coverage.append(line)
            continue
        # `in_package` is only meaningful for a rule that HAS a package list. For a
        # `tests_from_document` rule the document's own list IS the authorised scope, so every
        # catalogue-matched test on it is covered — splitting on `in_package` there billed the
        # patient for the whole referral letter (chit 2: SGD 195 for tests the insurer pays for).
        if covered_by_rule(rule, test.in_package):
            covered.append(line)
        else:
            payable.append(line)

    known = [Decimal(str(item["list_price_sgd"])) for item in payable
             if item["list_price_sgd"] is not None]
    missing = [item["as_printed"] for item in payable if item["list_price_sgd"] is None]
    # Nothing payable means the total is ZERO, not unknown. Returning None there is
    # indistinguishable on the card from "we could not price this", which is the one distinction
    # this module is otherwise careful to keep.
    total: Optional[Decimal] = (Decimal("0") if not payable
                                else (sum(known, Decimal("0")) if known else None))
    # The printed total understates what the patient may owe whenever EITHER a catalogue-matched
    # payable test is missing a price, OR an uncatalogued test exists whose coverage was never
    # determined at all. The second half used to be left out: with every printed test uncatalogued,
    # `payable` is empty, `missing` is therefore empty too (it is derived from `payable`), and the
    # total came back a confident SGD 0 sitting right next to a non-empty `unknown_coverage_items`
    # list — the display said "nothing owed" while staff still had unpriced tests to resolve by
    # hand. Gated on `total is not None`: when the total is already `None` (a payable test with no
    # price at all and nothing else known) the card shows "unknown", which needs no extra flag.
    lower_bound = total is not None and (bool(missing) or bool(unpriced))

    if missing:
        # A price we do not have is unknown, never zero — the total is explicitly partial.
        caveats.append(
            f"no catalogue price for {', '.join(repr(m) for m in missing)}; the estimate below "
            f"excludes them and is a LOWER BOUND")
    if unpriced:
        caveats.append(
            f"not in the test catalogue at all: {', '.join(repr(u) for u in unpriced)} — price "
            f"and coverage must be established manually; the estimate below does not include them "
            f"and is a LOWER BOUND")
    if coverage.eligibility_flags:
        caveats.append(
            f"coverage carries unresolved flag(s) {coverage.eligibility_flags}; the split below "
            f"assumes they are cleared in the patient's favour")

    tpa_required = bool(issuer.tpa_claim_required) if issuer is not None else False
    if tpa_required:
        caveats.append(f"{payer.value} requires a TPA claim submission after the visit")

    if lower_bound:
        # Machine-readable, not only prose: a consumer that renders the total but not every caveat
        # would otherwise present a lower bound as a complete estimate.
        caveats.insert(0, "TOTAL_IS_LOWER_BOUND")

    return BillingDraft(
        billed_to=payer.value,
        covered_items=covered,
        patient_payable_items=payable,
        unknown_coverage_items=unknown_coverage,
        estimated_patient_payable_sgd=total,
        tpa_claim_required=tpa_required,
        caveats=caveats)


def walk_in_draft(table: Optional[CoverageRules] = None) -> BillingDraft:
    """The self-pay path, for an intake with no referring document at all."""
    table = table or rules()
    return BillingDraft(
        billed_to=BillingParty.PATIENT.value,
        estimated_patient_payable_sgd=None,
        caveats=[SYNTHETIC_PRICE_CAVEAT,
                 "walk-in self-pay: no referring document, so nothing is billed to a third party"])
