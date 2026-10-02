"""C-5 (finance dry run, 2026-10-02): `/brain/query`'s identity refusal speaks the bound pack's words.

The dry run asked the finance deployment an identity question and was told that identity "is
verified in person by clinic staff". The detection is the engine's; the sentence is the pack's
(`packs/<pack>/agent_wording.yaml` → `identity.refusal`), and a deployment binding several packs
gets the engine's neutral sentence. Expectations are read from the YAML on disk, for two estates.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

REPO = pathlib.Path(__file__).resolve().parents[3]
PACKS = REPO / "packs"
QUESTION = "Can you confirm this person's identity?"


def _expected(pack: str) -> str:
    from databridge_core.identity_guardrail import parse_identity_section

    data = yaml.safe_load((PACKS / pack / "agent_wording.yaml").read_text(encoding="utf-8"))
    return parse_identity_section(data, pack=pack, where=pack).refusal


@pytest.fixture(scope="module")
def client():
    from databridge_gateway import create_app

    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        yield c


@pytest.mark.parametrize("estate", ["finance", "workspace"])
def test_brain_query_refuses_in_the_bound_packs_words(client, monkeypatch, estate):
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    monkeypatch.setenv("DATABRIDGE_PACKS", estate)
    r = client.post("/brain/query", json={"question": QUESTION})
    assert r.status_code == 422, r.text
    ref = r.json()["refusal"]
    assert ref["code"] == "IDENTITY_VERIFICATION_OUT_OF_SCOPE"
    assert ref["message"] == _expected(estate)
    others = {_expected(p.parent.name) for p in PACKS.glob("*/agent_wording.yaml")
              if p.parent.name != estate}
    assert others and ref["message"] not in others


def test_brain_query_refuses_neutrally_when_several_packs_are_bound(client, monkeypatch):
    from databridge_core.identity_guardrail import NEUTRAL_REFUSAL_TEXT
    from databridge_core.pack_binding import enabled_packs

    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    assert len(enabled_packs()) >= 2
    r = client.post("/brain/query", json={"question": QUESTION})
    assert r.status_code == 422 and r.json()["refusal"]["message"] == NEUTRAL_REFUSAL_TEXT
