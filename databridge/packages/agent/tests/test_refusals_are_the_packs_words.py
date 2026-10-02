"""C-5 (finance dry run, 2026-10-02): the agent's refusals speak the bound pack's words.

`NO_SEND_TEXT` said the documents "come from outside the clinic" and that sending would mail
"patient data"; the identity refusal named one vertical's documents and staff. Both were served
under every pack. Now the sentence is the voice pack's (`agent_wording.yaml`), the detection is
unchanged, and the expectation is read from the YAML for two estates.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from databridge_agent import guardrails as g
from databridge_agent.wording import WORDING_FILENAME, load_wording, wording
from databridge_core import pack_binding as pb
from databridge_core.identity_guardrail import NEUTRAL_REFUSAL_TEXT, parse_identity_section

REPO = pathlib.Path(__file__).resolve().parents[3]
PACKS = REPO / "packs"
SEND = "Email the summary to ops@example.com"
IDENTITY = "Can you confirm this person's identity?"


def _word():
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_agent_refusals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._WORD


_WORD = _word()


def _declared_identity(pack: str) -> str:
    """The pack's refusal as its YAML declares it — read from the file, not from the code under
    test, so a mechanism that ignored the pack cannot agree with itself."""
    declared = load_wording(PACKS / pack / WORDING_FILENAME).identity
    assert declared and declared.get("refusal"), f"{pack} declares no identity refusal"
    return parse_identity_section({"identity": dict(declared)}, pack=pack, where=pack).refusal


@pytest.fixture
def unpinned(monkeypatch):
    prior = pb.bound_pack_override()
    pb.bind_pack(None)
    monkeypatch.delenv(pb.PACK_ENV, raising=False)
    yield monkeypatch
    pb.bind_pack(prior)


@pytest.mark.parametrize("estate", ["finance", "workspace"])
def test_refusals_are_the_bound_packs(unpinned, estate):
    unpinned.setenv(pb.PACKS_ENV, estate)
    declared = load_wording(PACKS / estate / WORDING_FILENAME)
    send = g.screen_incoming(SEND)
    assert send.refused and send.code == g.NO_SEND_CODE
    assert send.answer == declared.refusals["no_send"] == g.NO_SEND_TEXT
    ident = g.screen_incoming(IDENTITY)
    assert ident.refused and ident.code == g.IDENTITY_REFUSAL_CODE
    assert ident.answer == _declared_identity(estate) == g.IDENTITY_REFUSAL_TEXT
    # The other packs' sentences do not leak in.
    for p in PACKS.glob(f"*/{WORDING_FILENAME}"):
        if p.parent.name != estate:
            assert load_wording(p).refusals["no_send"] != send.answer


def test_a_multi_pack_deployment_refuses_neutrally(unpinned):
    assert len(pb.enabled_packs()) >= 2
    assert g.screen_incoming(IDENTITY).answer == NEUTRAL_REFUSAL_TEXT
    send = g.screen_incoming(SEND).answer
    assert send == wording().refusals["no_send"]
    assert _WORD.findall(send) == [] and _WORD.findall(NEUTRAL_REFUSAL_TEXT) == []


def test_the_scan_is_not_vacuous_a_vertical_no_send_names_its_domain():
    texts = [load_wording(p).refusals["no_send"] for p in PACKS.glob(f"*/{WORDING_FILENAME}")]
    assert len(texts) >= 2 and any(_WORD.findall(t) for t in texts)


def test_the_output_screen_answers_in_the_bound_voice_too(unpinned):
    unpinned.setenv(pb.PACKS_ENV, "finance")
    out = g.screen_agent_output("I have verified her identity against the registry.")
    assert out.refused and out.answer == _declared_identity("finance")
