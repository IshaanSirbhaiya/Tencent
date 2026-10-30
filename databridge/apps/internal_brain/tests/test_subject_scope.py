"""Briefing requests that name a project are scoped to it (``databridge_ib/subject.py``).

Unit tests use made-up names; the end-to-end tests run the synthetic Atlas scenario world
(``fixtures/scenarios/atlas_onboarding/world.json`` = Company A + the Atlas records) through the
real mock platforms, connectors, Gate 1 + Gate 2, ledger and the rules page builder. No model.
Expectations are read from the scenario's own manifest, not written down here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from databridge_ib.subject import mentions, scope, sections, subject, within

_DB = Path(__file__).resolve().parents[3]
_SCENARIO = _DB / "fixtures" / "scenarios" / "atlas_onboarding"
_ENTERPRISE = _DB / "fixtures" / "enterprise" / "world.json"


# -- unit -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("q,name", [
    ("Hi, I'm Dana Lee, the new lead for Project Zephyr. Bring me up to speed: status, owners.",
     "Zephyr"),
    ("I am the new owner of the Kestrel project; catch me up on blockers and decisions", "Kestrel"),
    ("Onboarding brief for programme Heron please: timeline, risks", "Heron"),
])
def test_briefing_on_named_project_is_detected(q, name):
    assert subject(q) == name
    parts = sections(q)
    assert parts and all(p.startswith(name + " ") or p == name for p in parts)
    assert scope(q) == ", ".join(parts)


@pytest.mark.parametrize("q", [
    "What is the status of Project Zephyr?",                     # names a project, not a briefing
    "Bring me up to speed on the payments migration",           # a briefing, no named project
    "Onboard me to this project please",                        # "this" is not a name
    "Who owns the Kestrel blockers?",
    "",
])
def test_other_questions_are_unchanged(q):
    assert subject(q) == ""
    assert sections(q) == []
    assert scope(q) == q


def test_self_introduction_and_section_words_are_not_subjects_on_their_own():
    q = ("Hi, I'm Dana Lee, the new product lead for Project Zephyr. Bring me up to speed with an "
         "onboarding brief: purpose, status, team table and contributions, decisions.")
    parts = sections(q)
    assert not any("Dana" in p or "Lee" in p for p in parts)     # the introduction is dropped
    assert {"Zephyr purpose", "Zephyr status", "Zephyr decisions"} <= set(parts)


class _Ix:
    def __init__(self, rows):
        self.rows = rows

    def doc(self, d):
        return self.rows.get(d)


class _H:
    def __init__(self, doc_id):
        self.doc_id = doc_id


def test_within_keeps_documents_about_the_subject_only():
    ix = _Ix({"a": {"title": "Zephyr ADR-1", "container_name": "eng", "text": "x"},
              "b": {"title": "Notes", "container_name": "proj-zephyr", "text": "x"},
              "c": {"title": "Project Heron", "container_name": "heron",
                    "text": "Its dates do not apply to Zephyr."},       # a passing mention
              "d": {"title": "Launch checklist", "container_name": "product", "text": "status"}})
    q = "I'm the new lead for Project Zephyr, onboarding brief: status, decisions"
    assert mentions(ix, "a", "Zephyr") and mentions(ix, "b", "Zephyr")
    assert not mentions(ix, "c", "Zephyr") and not mentions(ix, "missing", "Zephyr")
    assert [h.doc_id for h in within(q, [_H(d) for d in "abcd"], ix)] == ["a", "b"]
    assert within(q, ["a", "c", "d"], ix) == ["a"]                     # plain ids too
    assert [h.doc_id for h in within("status?", [_H(d) for d in "abcd"], ix)] == list("abcd")


def test_default_world_questions_are_never_rescoped():
    """The main eval's questions keep their exact retrieval query (the leak eval stays as it was)."""
    world = json.loads(_ENTERPRISE.read_text(encoding="utf-8"))
    qs = [q["question"] for q in world["questions"] if q.get("question")]
    assert qs and all(scope(q) == q for q in qs)


# -- end to end on the Atlas scenario world ---------------------------------------------------------
@pytest.fixture(scope="module")
def atlas(tmp_path_factory):
    from fastapi.testclient import TestClient

    from databridge_ib.app import Services, create_app
    from databridge_ib.audit.content import AnswerStore
    from databridge_ib.audit.ledger import AccessLedger
    from databridge_ib.audit.query import AuditProjection
    from databridge_ib.config import IbConfig
    from e2e_world import E2E

    world = _SCENARIO / "world.json"
    if not world.exists():
        pytest.skip("Atlas scenario world not present")
    manifest = json.loads((_SCENARIO / "manifest.json").read_text(encoding="utf-8"))
    tmp = tmp_path_factory.mktemp("atlas")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("IB_CLAUDE", "0")
        mp.setenv("IB_FORCE_OFFLINE", "1")
        mp.setenv("IB_DELIVERABLES", "1")
        cfg = IbConfig(var_root=tmp / "state", world_path=world, demo_login=True,
                       deliverables=True)
        ledger, answers = AccessLedger(cfg), AnswerStore(cfg)
        engine = E2E(tmp, world_path=world, ledger=ledger, answers=answers)
        svc = Services(cfg=cfg, directory=engine.directory, index=engine.index,
                       connectors=engine.connectors, ledger=ledger, answers=answers,
                       pipeline=engine.pipeline, ingestor=engine.ingestor,
                       projection=AuditProjection(cfg, ledger))
        app = create_app(svc)

        def client(persona):
            c = TestClient(app)
            c.post("/auth/demo-login", json={"persona": persona}).raise_for_status()
            return c

        yield {"client": client, "manifest": manifest}


def _ask(c, q):
    r = c.post("/ib/ask", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_lead_gets_a_cross_platform_brief_and_a_full_team_page(atlas):
    m = atlas["manifest"]
    alex = atlas["client"](m["signed_in_persona"])
    ans = _ask(alex, m["question"])
    assert {c["platform"] for c in ans["citations"]} == {"confluence", "drive", "jira", "slack"}
    view = alex.post("/ib/views", json={"req_id": ans["request_id"],
                                        "instruction": m["layout"]}).json()
    assert view["status"] == "ready"
    data = json.dumps(view["a2ui"][2]["updateDataModel"]["value"], ensure_ascii=False)
    assert all(p["name"] in data for p in m["team"])
    blob = json.dumps({"a": ans, "v": view}, ensure_ascii=False)
    assert m["never_reveal"] not in blob
    for excluded in m["excluded_for_alex"] + ["confluence:boreal-overview"]:
        assert excluded not in blob
    # every document the page rests on is about the named project
    project = {r["doc_id"] for r in m["records"]} - set(m["excluded_for_alex"])
    ids = {c["doc_id"] for c in view["citations"]} | {c["doc_id"] for c in ans["citations"]}
    assert ids and ids <= project, ids - project


def test_non_member_brief_is_the_same_as_for_a_project_that_does_not_exist(atlas):
    """Restricted ≡ non-existent: a contractor outside the project gets the same response for the
    real project as for an invented one (no citations, same text)."""
    m = atlas["manifest"]
    ken = atlas["client"]("ken.ext")
    real = _ask(ken, m["question"])
    fake = _ask(ken, m["question"].replace("Atlas", "Quillon"))
    assert not real["citations"] and not fake["citations"]
    assert real["answer"] == fake["answer"]


def test_another_employee_cannot_reuse_the_leads_answer(atlas):
    m = atlas["manifest"]
    ans = _ask(atlas["client"](m["signed_in_persona"]), m["question"])
    other = atlas["client"]("jdoe")
    r = other.post("/ib/views", json={"req_id": ans["request_id"], "instruction": "report"})
    assert r.status_code == 404
