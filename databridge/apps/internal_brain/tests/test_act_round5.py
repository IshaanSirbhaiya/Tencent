"""Act, round-5 Codex review fixes (docs/finals/reviews/round5_codex_final.md #1; A2 partial).

The draft floor (``actions.supported``) now holds a sentence to its ANCHOR, the cited sentence it
overlaps most, not to the cited facts as a whole:

- every capitalised name must be in that anchor (a name from another cited sentence fails);
- one anchor must hold every name AND every number together (two tied anchors can't be mixed);
- negation is compared clause by clause against the anchor's clauses, so "Acme but not Bayfront"
  cannot become "Bayfront but not Acme".

A sentence that fails is dropped; when nothing survives, the reply falls back to the cited
sentences (test_act_round4.py covers the fallback end to end).
"""
from __future__ import annotations

from databridge_ib.actions import supported

TWO = "Staff can approve customer refund requests. Mallory manages billing."
WAIVE = "We will waive the fee for Acme but not for Bayfront."


def test_a_name_from_another_cited_sentence_fails():
    # Codex round 5 #1: "Mallory" is in the facts, but not in the anchor (sentence 1)
    assert not supported("Mallory can approve customer refund requests [C1].", TWO)
    assert supported("Staff can approve customer refund requests [C1].", TWO)
    assert supported("Mallory manages billing [C1].", TWO)


def test_names_swapped_between_two_cited_sentences_fail():
    src = "Ana Lim approves customer refund requests. Bob Tan manages billing disputes."
    assert supported("Ana Lim approves customer refund requests [C1].", src)
    assert supported("Bob Tan manages billing disputes [C1].", src)
    assert not supported("Bob Tan approves customer refund requests [C1].", src)
    assert not supported("Ana Lim manages billing disputes [C1].", src)


def test_a_name_and_a_number_from_two_tied_anchors_fail():
    src = "Acme pays 10 dollars. Bayfront pays 20 dollars."
    assert supported("Acme pays 10 dollars [C1].", src)
    assert supported("Bayfront pays 20 dollars [C1].", src)
    assert not supported("Acme pays 20 dollars [C1].", src)
    assert not supported("Bayfront pays 10 dollars [C1].", src)


def test_negation_is_compared_clause_by_clause():
    assert supported("We will waive the fee for Acme but not for Bayfront [C1].", WAIVE)
    assert not supported("We will waive the fee for Bayfront but not for Acme [C1].", WAIVE)
    assert not supported("We will not waive the fee for Acme [C1].", WAIVE)
    assert not supported("We will waive the fee for Bayfront [C1].", WAIVE)
    # dropping the negated clause also fails (the whole-sentence negation check is kept): the
    # reply then falls back to the cited sentence, which is the conservative side
    assert not supported("We will waive the fee for Acme [C1].", WAIVE)
    comma = "Refunds are allowed for damaged goods, not for opened software."
    assert supported("Refunds are allowed for damaged goods, not for opened software [C1].", comma)
    assert not supported("Refunds are allowed for opened software, not for damaged goods [C1].",
                         comma)
    assert not supported("We waive the fee for Bayfront and not for Acme [C1].",
                         "We waive the fee for Acme and not for Bayfront.")


def test_a_legitimate_paraphrase_of_one_cited_sentence_still_passes():
    src = "Customers on the Growth plan can open a multi-currency account. Fees are 0.4%."
    assert supported("Growth plan customers can open a multi-currency account [C1].", src)
    assert supported("Fees are 0.4% [C1].", src)
    assert supported("For Acme, we will waive the fee [C1].", "We will waive the fee for Acme.")
    assert supported("We do not refund vouchers or cash [C1].", "We do not refund cash or vouchers.")
    assert supported("Refunds of 1,000 or more need approval from Ana Lim [C1].",
                     "Refunds of 1,000 or more need approval from Ana Lim.")
    assert supported("Ana Lim, our billing lead, approves refund requests [C1].",
                     "Ana Lim, our billing lead, approves refund requests.")
