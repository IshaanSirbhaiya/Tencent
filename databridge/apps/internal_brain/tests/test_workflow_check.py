"""scripts/workflow_check.py in-process: the sample no-model steps pass against the e2e world (the
REAL mock platforms, connectors, pipeline, ledger, projection, answer store and the real HTTP app
with its audit routes) through a TestClient, so no socket is opened; deliberately wrong steps fail.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from databridge_ib.app import Services, create_app
from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.audit.routes import make_audit_router
from databridge_ib.config import IbConfig
from e2e_world import E2E

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SAMPLE = _SCRIPTS / "workflow_steps" / "sample_no_model.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("workflow_check", _SCRIPTS / "workflow_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wc = _load_checker()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.delenv("IB_DEMO_PASSCODE", raising=False)
    tmp = tmp_path_factory.mktemp("workflow-check")
    cfg = IbConfig(var_root=tmp / "var", demo_login=True, deliverables=True)
    led, answers = AccessLedger(cfg), AnswerStore(cfg)
    e = E2E(tmp, ledger=led, answers=answers)
    proj = AuditProjection(led)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=led, answers=answers, pipeline=e.pipeline, ingestor=e.ingestor,
                   projection=proj)
    app = create_app(svc, audit_router_factory=lambda cur, rr: make_audit_router(
        led, answers, proj, rr))
    yield TestClient(app)
    mp.undo()


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _fails(report: dict, sid: str) -> list[dict]:
    row = next(r for r in report["steps"] if r["id"] == sid)
    return [c for c in row["checks"] if c["status"] == "fail"]


def test_sample_steps_pass_in_no_model_mode(client):
    doc = _sample()
    assert 8 <= len(doc["steps"]) <= 12
    report = wc.run_steps(doc, client)
    assert report["ok"], wc.format_table(report)
    assert report["passed"] == report["total"] == len(doc["steps"])
    assert report["status"]["models"]["active"] is False            # the quoted (no-model) path
    rows = {r["id"]: r for r in report["steps"]}
    # the checks are not vacuous: the P2 comparison, the re-check and the ledger really ran
    names = {c["name"] for c in rows["ken_bayfront"]["checks"] if c["status"] == "pass"}
    assert {"same_as", "refusal", "model_label"} <= names
    names = {c["name"] for c in rows["amy_velquarry"]["checks"] if c["status"] == "pass"}
    assert {"cite_recheck", "ledger.kinds", "ledger.no_answer_text", "action.draft.grounded"} <= names
    assert any(c["name"] == "action.deliverable.grounded" and c["status"] == "pass"
               for c in rows["leo_fee_table"]["checks"])
    assert any(c["name"] == "ledger.verify_chain" and c["status"] == "pass"
               for c in rows["maya_own_question"]["checks"])


def test_deliberately_wrong_steps_fail(client):
    doc = {"steps": [
        {"id": "amy", "persona": "amy.am",
         "question": "Why has Velquarry Trading's account been restricted, and what should I tell the customer?",
         # wrong on purpose: Amy is answered (not refused) and her answer is the script
         "expect": {"refusal": True, "contains": ["37 inbound transfers"],
                    "not_contains": ["routine review"], "citations_min": 5}},
        {"id": "rachel", "persona": "mlro.tan",
         "question": "Why has Velquarry Trading's account been restricted, and what should I tell the customer?",
         "same_as": "amy",                                           # Amy and Rachel differ
         "expect": {"ledger": {"kinds": ["request.accepted", "action.executed"]}}},
        {"id": "ken", "persona": "ken.ext",
         "question": "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?",
         "expect": {"refusal": False},
         "action": {"type": "deliverable", "kind": "table"}},       # refused answer: a 404
    ]}
    report = wc.run_steps(doc, client)
    assert not report["ok"] and report["passed"] == 0
    assert {c["name"] for c in _fails(report, "amy")} == {
        "refusal", "contains", "not_contains", "citations_min"}
    assert {c["name"] for c in _fails(report, "rachel")} == {"same_as", "ledger.kinds"}
    assert {c["name"] for c in _fails(report, "ken")} == {"refusal", "action.deliverable"}
    assert "FAIL" in wc.format_table(report)


def test_canary_and_model_label_checks_bite(client):
    """A forbidden term that IS present fails, and a model label naming a provider fails."""
    doc = _sample()
    amy = copy.deepcopy(next(s for s in doc["steps"] if s["id"] == "amy_velquarry"))
    amy.pop("action")
    amy["expect"] = {"not_contains": ["/routine\\s+REVIEW/"]}
    report = wc.run_steps({"steps": [amy]}, client)
    assert [c["name"] for c in _fails(report, "amy_velquarry")] == ["not_contains"]
    runner = wc.Runner(client, {"steps": []})
    resp = {"answer": "ok", "model": {"provider": "standin", "model": "standin-1"}}
    chk = runner.model_label("jdoe", resp, "ok", "neutral")
    assert chk.ok is False and "no model is active" in chk.detail
    for label in ({"provider": "deterministic", "model": "hy3"},
                  {"provider": "deterministic", "model": "Sonnet-x"}):
        chk = runner.model_label("jdoe", {"model": label}, "", "neutral")
        assert chk.ok is False, label


def test_only_pulls_in_dependencies_and_bad_files_are_rejected(client):
    report = wc.run_steps(_sample(), client, only=["ken_bayfront"])
    assert [r["id"] for r in report["steps"]] == ["ken_velquarry", "ken_bayfront"]
    assert report["ok"], wc.format_table(report)
    with pytest.raises(wc.StepsError):
        wc.normalise({"steps": [{"id": "a", "persona": "jdoe", "question": "q", "same_as": "b"}]})
    with pytest.raises(wc.StepsError):
        wc.normalise([{"id": "a", "persona": "jdoe"}])
    with pytest.raises(wc.StepsError):
        wc.select(wc.normalise([{"id": "a", "persona": "jdoe", "question": "q"}]), ["zzz"])


def test_p2_strip_rule():
    a = {"request_id": "1", "as_of": "t1", "answer": "x",
         "citations": [{"doc_id": "d", "content_checked_at": "t", "authz_checked_at": "t"}]}
    b = {"request_id": "2", "as_of": "t2", "answer": "x",
         "citations": [{"doc_id": "d", "content_checked_at": "u", "authz_checked_at": "u"}]}
    assert wc.p2_body(a) == wc.p2_body(b)
    assert wc.p2_body(a) != wc.p2_body({**b, "answer": "y"})
