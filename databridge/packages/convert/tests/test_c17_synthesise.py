"""C17 synthesiser (§12.2) + verifier (§12.3b) — ADVERSARIAL ABSENT-FACT FIRST.

The headline guarantee: a question implying a field NOT in the fact set must yield the FALLBACK
text, never a value — and even if the model fabricates anyway, the fabrication is QUARANTINED
(claim_check), never emitted as a valid turn. Built first, per the prompt.
"""
from __future__ import annotations

import datetime as dt

from databridge_core import SourcePointer
from databridge_harness import FakeBackend, Harness

from databridge_convert.generate import (
    extract_facts,
    get_template,
    synthesise,
    verify,
)

CFG = {"routing": {"data_to_text": "gen", "template_selection": "gen",
                   "verification_pass": "ver"},
       "aliases": {"gen": {"model": "openai/gpt-4.1"}, "ver": {"model": "anthropic/claude-sonnet-4-6"}},
       "profiles": {"cloud": {}, "airgapped": {}}}


def _ptr(cell="B1"):
    return SourcePointer(source_id="wb", kind="workbook_cell", workbook_sha="x", sheet="S", cell=cell)


def _facts():
    rec = {"customer": "Acme Industries", "total": 1350.00, "date": dt.date(2024, 3, 15)}
    return extract_facts(rec, kinds={"customer": "name", "total": "money", "date": "date"},
                         pointers={"customer": _ptr("B1"), "total": _ptr("B2"), "date": _ptr("B3")})


def _h(text: str) -> Harness:
    return Harness(FakeBackend(text), config=CFG, profile="cloud")


# === ADVERSARIAL ABSENT-FACT (the FIRST test) =====================================
def test_absent_fact_question_yields_fallback_not_a_value():
    facts = _facts()                                  # NO 'discount' fact exists
    tmpl = get_template("support_reply")
    # a well-behaved model writes the fallback for the absent field
    h = _h(tmpl.fallback)
    res = synthesise(h, facts, template=tmpl, question="What discount did Acme get?")
    assert res.ok
    assert res.text == tmpl.fallback
    assert res.facts_used == []                        # no fact cited (none applies)


def test_absent_fact_fabrication_is_quarantined_not_emitted():
    facts = _facts()
    tmpl = get_template("support_reply")
    # the model MISBEHAVES and invents a discount amount — must NOT escape as a valid turn
    h = _h("Acme received a discount of $250.00 on this order.")
    res = synthesise(h, facts, template=tmpl, question="What discount did Acme get?")
    assert res.outcome == "quarantined"
    assert "fabricated_number" in res.reason
    assert not res.ok


# === HAPPY PATH ===================================================================
def test_grounded_sentence_passes_and_cites_facts():
    facts = _facts()
    h = _h("Acme Industries was invoiced $1,350.00 on 2024-03-15.")
    res = synthesise(h, facts, template=get_template("record_narration"))
    assert res.ok
    assert res.template_ref == "record_narration@1"
    # facts_used cites the pointers of the facts whose canonical form appears
    used_cells = {p.cell for p in res.facts_used}
    assert used_cells == {"B1", "B2", "B3"}


def test_facts_used_not_falsely_cited_by_substring():
    """facts_used must be boundary-aware: the fact '8.0%' must NOT be cited as the source of a
    fabricated '18.0%' (a raw substring test would). [adversarial review MEDIUM]"""
    facts = _facts()                                   # includes tax_rate 8.0%
    h = _h("The tax was 18.0%.")
    res = synthesise(h, facts, template=get_template("metric_statement"))
    assert res.outcome == "quarantined"                # 18.0% is a fabrication
    # the 8.0% fact (in the facts) must NOT be falsely cited just because "8.0%" ⊂ "18.0%"
    assert all(f.rendered != "8.0%" or f.pointer not in res.facts_used for f in facts)


def test_hedged_output_is_quarantined():
    facts = _facts()
    h = _h("The total was approximately $1,350.00.")
    res = synthesise(h, facts, template=get_template("metric_statement"))
    assert res.outcome == "quarantined" and "hedged_quantity" in res.reason


# === VERIFIER (independent model, DIFFERENT alias) ================================
def test_verifier_uses_a_different_alias_than_the_generator():
    from databridge_convert.generate.synthesiser import GENERATION_TASK
    from databridge_convert.generate.verifier import VERIFICATION_TASK
    from databridge_core import resolve_model
    gen_model = resolve_model(GENERATION_TASK, profile="cloud", config=CFG)
    ver_model = resolve_model(VERIFICATION_TASK, profile="cloud", config=CFG)
    assert gen_model != ver_model                      # adversarial separation (07_PRD §2)


def test_verifier_flags_unsupported_claim():
    import json
    facts = _facts()
    report = {"verdicts": [
        {"claim": "Acme was invoiced $1,350.00", "supported": True, "reason": "matches total"},
        {"claim": "the order shipped to Tokyo", "supported": False, "reason": "no such fact"}]}
    h = Harness(FakeBackend(json.dumps(report)), config=CFG, profile="cloud")
    res = verify(h, "Acme was invoiced $1,350.00 and shipped to Tokyo.", facts)
    assert res.supported is False
    assert len(res.unsupported) == 1 and "Tokyo" in res.unsupported[0].claim


def test_verifier_passes_supported_text():
    import json
    facts = _facts()
    report = {"verdicts": [{"claim": "Acme invoiced $1,350.00", "supported": True}]}
    h = Harness(FakeBackend(json.dumps(report)), config=CFG, profile="cloud")
    res = verify(h, "Acme Industries was invoiced $1,350.00.", facts)
    assert res.supported is True
