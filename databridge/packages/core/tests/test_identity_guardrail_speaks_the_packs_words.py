"""C-5 / FIN-09 (finance dry run, 2026-10-02): the identity refusal is the ENGINE's guarantee in the
PACK's words.

Before the move, ``databridge_intake.guardrails.REFUSAL_TEXT`` — "…whether an NRIC, FIN, passport
or insurance e-card is valid… verified in person by clinic staff" — was the answer to an identity
question on ``/brain/query`` and in the agent under EVERY binding, finance included. These tests
drive the real mechanism under two estates (the finance pack and the workspace pack) plus the
shipped multi-pack deployment, and derive every expected sentence from the pack YAML on disk.

What must NOT move is the detection rule: it is as strict under every binding as it was when its
nouns were literals in code. The questions below are read (by AST, without importing it) from the
intake suite's own ``IDENTITY_QUESTIONS`` / ``ORDINARY_QUESTIONS`` — the corpus that rule was
reviewed against — so the expectation is that suite's, not a copy.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest
import yaml

from databridge_core import identity_guardrail as ig
from databridge_core import pack_binding as pb

REPO = pathlib.Path(__file__).resolve().parents[3]                      # databridge/
PACKS = REPO / "packs"
GATE_TESTS = REPO / "packages" / "intake" / "tests" / "test_gate.py"


def _literal(name: str) -> list[str]:
    tree = ast.parse(GATE_TESTS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {GATE_TESTS}")


IDENTITY_QUESTIONS = _literal("IDENTITY_QUESTIONS")
ORDINARY_QUESTIONS = _literal("ORDINARY_QUESTIONS")


def _domain_word():
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_core_ig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._WORD


_WORD = _domain_word()


def _declared(pack: str) -> dict:
    data = yaml.safe_load((PACKS / pack / ig.WORDING_FILENAME).read_text(encoding="utf-8"))
    return data.get(ig.IDENTITY_SECTION) or {}


def _paras(text: str) -> str:
    return ig.parse_identity_section({"identity": {"refusal": text}}, pack=None, where="t").refusal


#: The two estates every new test covers (founder rule), plus the shipped multi-pack binding.
ESTATES = ("finance", "workspace")


@pytest.fixture
def unpinned(monkeypatch):
    prior = pb.bound_pack_override()
    pb.bind_pack(None)
    monkeypatch.delenv(pb.PACK_ENV, raising=False)
    monkeypatch.delenv(pb.PACKS_ROOT_ENV, raising=False)
    yield monkeypatch
    pb.bind_pack(prior)


def test_the_corpus_is_not_vacuous():
    assert len(IDENTITY_QUESTIONS) >= 10 and len(ORDINARY_QUESTIONS) >= 5
    shipped = [p for p in sorted(PACKS.glob(f"*/{ig.WORDING_FILENAME}")) if _declared(p.parent.name)]
    assert len(shipped) >= 2, "fewer than two packs declare identity wording"


@pytest.mark.parametrize("estate", ESTATES)
def test_a_single_pack_deployment_refuses_in_that_packs_words(unpinned, estate):
    """The C-5 reproduction: under ``DATABRIDGE_PACKS=finance`` the refusal named another
    vertical's identity documents and staff. Now it is the finance pack's own sentence."""
    unpinned.setenv(pb.PACKS_ENV, estate)
    expected = _paras(_declared(estate)["refusal"])
    for q in IDENTITY_QUESTIONS:
        r = ig.check_question(q)
        assert r.refused and r.code == ig.REFUSAL_CODE, q
        assert r.answer == expected, q
    # No OTHER pack's refusal leaks in.
    others = {_paras(_declared(p.parent.name)["refusal"])
              for p in PACKS.glob(f"*/{ig.WORDING_FILENAME}")
              if p.parent.name != estate and _declared(p.parent.name).get("refusal")}
    assert others and expected not in others


def test_a_multi_pack_deployment_refuses_neutrally(unpinned):
    """Three verticals bound together are none of theirs: the neutral, domain-free sentence."""
    enabled = pb.enabled_packs()
    assert len(enabled) >= 2, "the shipped deployment binds one pack; this case needs several"
    for q in IDENTITY_QUESTIONS:
        assert ig.check_question(q).answer == ig.NEUTRAL_REFUSAL_TEXT
    assert _WORD.findall(ig.NEUTRAL_REFUSAL_TEXT) == []


def test_the_workspace_refusal_names_no_domain(unpinned):
    unpinned.setenv(pb.PACKS_ENV, "workspace")
    assert _WORD.findall(ig.refusal_text()) == []


def test_the_scan_is_not_vacuous_a_vertical_refusal_does_name_its_domain():
    texts = [_paras(_declared(p.parent.name)["refusal"])
             for p in PACKS.glob(f"*/{ig.WORDING_FILENAME}") if _declared(p.parent.name).get("refusal")]
    assert any(_WORD.findall(t) for t in texts)


@pytest.mark.parametrize("binding", [*ESTATES, "__deployment__"])
def test_detection_is_as_strict_under_every_binding(unpinned, binding):
    """Only the wording moved. Every question the intake suite says is an identity question is
    refused under every binding, and every ordinary one is answered."""
    if binding != "__deployment__":
        unpinned.setenv(pb.PACKS_ENV, binding)
    for q in IDENTITY_QUESTIONS:
        assert ig.check_question(q).refused, f"{binding}: not refused: {q!r}"
    for q in ORDINARY_QUESTIONS:
        assert not ig.check_question(q).refused, f"{binding}: wrongly refused: {q!r}"


def test_a_partial_packs_root_cannot_weaken_detection(unpinned, tmp_path):
    """Pointing the packs root at a directory holding one bare pack must not drop the nouns the
    checkout's packs declare — the union also reads the checkout's own packs root."""
    (tmp_path / "solo").mkdir()
    unpinned.setenv(pb.PACKS_ROOT_ENV, str(tmp_path))
    unpinned.setenv(pb.PACKS_ENV, "solo")
    for q in IDENTITY_QUESTIONS:
        r = ig.check_question(q)
        assert r.refused, q
        assert r.answer == ig.NEUTRAL_REFUSAL_TEXT


def test_a_packs_declared_nouns_widen_detection_and_never_narrow_it():
    """Derived from the finance estate's declaration: its subject nouns are refused in the
    "is this the right <subject>?" shape only because the pack declares them."""
    docs, subjects, records = ig.detection_nouns()
    fin = _declared("finance")
    declared_subject = fin["subject_nouns"][0]
    declared_doc = fin["document_nouns"][-1]
    assert declared_subject in subjects and declared_doc in docs
    q_subject = f"Is this the right {declared_subject}?"
    q_doc = f"Is this {declared_doc} valid?"
    without = ig.compile_patterns((), (), ())
    with_nouns = ig.compile_patterns(docs, subjects, records)
    hit = lambda pats, q: any(p.search(q) for _, p in pats)            # noqa: E731
    assert not hit(without, q_subject) and hit(with_nouns, q_subject)
    assert not hit(without, q_doc) and hit(with_nouns, q_doc)
    assert ig.check_question(q_subject).refused and ig.check_question(q_doc).refused


@pytest.mark.parametrize("bad", ["a|b", "nric)", ".*", "x{2}", ""])
def test_a_pack_cannot_smuggle_regex_into_the_detection_rule(bad):
    with pytest.raises(ig.IdentityWordingError):
        ig.parse_identity_section({"identity": {"document_nouns": [bad]}}, pack="p", where="t")


def test_unknown_identity_keys_are_refused():
    with pytest.raises(ig.IdentityWordingError):
        ig.parse_identity_section({"identity": {"refusal": "x", "allow": ["y"]}}, pack="p",
                                  where="t")


def test_core_names_no_vertical_and_imports_no_vertical():
    for mod in (ig, pb):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert _WORD.findall(src) == [], mod.__name__
        imported = {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import)
                    for a in n.names} | {n.module or "" for n in ast.walk(ast.parse(src))
                                         if isinstance(n, ast.ImportFrom)}
        assert imported, mod.__name__
        assert not [m for m in imported if m.split(".")[0] in ("databridge_intake",
                                                               "databridge_graph")]
