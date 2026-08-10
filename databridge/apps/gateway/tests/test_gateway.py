"""Gateway endpoint tests (FastAPI TestClient — no live DB, no hosted model).

The TestClient drives the lifespan, so the full demo state (read-only engine, draft, replay
store, artifacts, evals) is built deterministically with FakeBackend. One module-scoped client
amortises that boot across every test.
"""
from __future__ import annotations

import base64

import orjson
import pytest
from fastapi.testclient import TestClient

from databridge_gateway import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:                  # `with` runs the lifespan
        yield c


def test_health_ready(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # LLM-backend metadata (T08 16.5d): no key in CI → fake backend; graph counts surfaced
    assert body["llm_backend"] == "fake" and body["api_key_present"] is False
    assert body["profile"] == "cloud"
    assert body["graph_nodes"] >= 100 and body["graph_edges"] >= 150


def test_brain_query_marks_fallback_real_llm_false(client):
    # with no API key the deterministic fallback answers and flags is_real_llm=false
    r = client.post("/brain/query", json={"question": "What is the BigClient contract value?"})
    assert r.status_code == 200 and r.json()["is_real_llm"] is False


def test_llm_detect_and_context(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from databridge_gateway.llm import build_context, build_query_harness, detect_llm

    d = detect_llm()
    assert d["backend"] == "fake" and build_query_harness(d) is None
    from brain_server import BrainTools

    t = BrainTools()
    t.load_seed()
    ctx = build_context(t, "What is the BigClient contract value?")
    assert "COMPANY BRAIN CONTEXT" in ctx["context"] and ctx["nodes_used"] and ctx["citations"]
    assert "$2,400,000" in ctx["context"]      # the fact value made it into the LLM context


# -- ingest + schema + SSRF --------------------------------------------------------
def test_ingest_fixture_ok(client):
    r = client.post("/ingest", json={"source_url": "fintech", "source_type": "fixture"})
    assert r.status_code == 200 and r.json()["job_id"] == "fintech"


def test_ingest_ssrf_blocked(client):
    r = client.post("/ingest", json={"source_url": "postgres://evil-internal-host/db",
                                     "source_type": "postgres"})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "SOURCE_NOT_ALLOWED"


def test_schema_returns_tables(client):
    r = client.get("/schema/fintech")
    assert r.status_code == 200
    body = r.json()
    assert any(t.get("name") == "transactions" for t in body.get("tables", []))


# -- annotate / confirm / finalize (the P6 gate) -----------------------------------
def test_annotate_has_one_review_item(client):
    r = client.post("/annotate", json={"schema_extract": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["queue_counts"]["review"] == 1            # exactly one mandatory item seeded
    assert body["draft"]["confirmed_by"] is None


def test_finalize_blocked_until_review_resolved(client):
    draft = client.post("/annotate", json={"schema_extract": {}}).json()["draft"]
    # finalize with NO decisions → P6 refuses
    r = client.post("/annotate/finalize",
                    json={"draft": draft, "confirmed_by": "demo@databridge", "decisions": []})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "MANDATORY_REVIEW_PENDING"


def test_confirm_then_finalize_unlocks(client):
    draft = client.post("/annotate", json={"schema_extract": {}}).json()["draft"]
    decisions = [{"table": "transactions", "column": "status", "accepted_type": "status_categorical"}]
    c = client.post("/annotate/confirm", json={"draft": draft, "decisions": decisions})
    assert c.status_code == 200 and c.json()["remaining_mandatory"] == 0
    f = client.post("/annotate/finalize",
                    json={"draft": draft, "confirmed_by": "demo@databridge", "decisions": decisions})
    assert f.status_code == 200 and f.json()["schema"]["confirmed_by"] == "demo@databridge"


def test_finalize_rejects_tampered_draft(client):
    draft = client.post("/annotate", json={"schema_extract": {}}).json()["draft"]
    draft["tables"] = draft["tables"][:1]                 # drop tables → structural tamper
    r = client.post("/annotate/finalize",
                    json={"draft": draft, "confirmed_by": "x", "decisions": []})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "DRAFT_TAMPERED"


# -- ask (replay-first) ------------------------------------------------------------
def test_ask_replays_known_question(client):
    q = client.get("/ask/suggested").json()["questions"][0]
    r = client.post("/ask", json={"question": q, "confirmed_schema_id": "fintech"})
    assert r.status_code == 200
    body = r.json()
    assert body["refusal"] is None and body["sql"] and body["replayed"] is True
    assert isinstance(body["result"], list)


def test_ask_injection_is_refused(client):
    inj = client.get("/ask/suggested").json()["injection"]
    r = client.post("/ask", json={"question": inj, "confirmed_schema_id": "fintech"})
    body = r.json()
    assert body["refusal"] is not None
    assert body["refusal"]["code"] in {"SQL_MULTI_STATEMENT", "SQL_FORBIDDEN"}
    assert body["result"] == []                           # never executed


def test_ask_provenance_is_the_sql(client):
    q = client.get("/ask/suggested").json()["questions"][0]
    body = client.post("/ask", json={"question": q}).json()
    prov = body["provenance"]
    assert prov is not None and prov["kind"] == "derived"
    assert prov["derivation"] == body["sql"]              # SQL-as-provenance


def test_ask_unknown_question_graceful_miss(client):
    r = client.post("/ask", json={"question": "what is the meaning of life"})
    body = r.json()
    assert body["refusal"]["code"] == "QUERY_NOT_IN_LIBRARY"   # clean, not a 500


# -- convert -----------------------------------------------------------------------
def test_convert_json_to_csv_with_quarantine(client):
    from databridge_gateway import demo_state as ds
    src = base64.b64encode(ds.D3_INPUT_JSON.encode()).decode()
    r = client.post("/convert", json={
        "source_b64": src, "source_format": "json", "target_format": "csv",
        "transform_spec": ds.DEMO_TRANSFORM_SPEC})
    assert r.status_code == 200
    body = r.json()
    out = base64.b64decode(body["result_b64"]).decode()
    assert "user_name" in out and "Manila" in out
    assert body["quarantine"]["quarantined"] == 1         # the seeded bad 'amount' row
    assert body["artifact_id"]


# -- artifacts ---------------------------------------------------------------------
def test_artifacts_list_and_replay_deterministic(client):
    lst = client.get("/artifacts").json()["artifacts"]
    assert any(a["artifact_id"] == "D3" for a in lst)
    r = client.post("/artifacts/D3/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["deterministic"] is True and body["checksum_ok"] is True


def test_artifact_unknown_404(client):
    assert client.get("/artifacts/NOPE").status_code == 404


# -- evals -------------------------------------------------------------------------
def test_evals_e1_scorecard(client):
    body = client.get("/evals/e1").json()
    assert body["suite"] == "E1" and 0.0 <= body["value"] <= 1.0
    assert "reliability" in body and "per_table" in body


# -- company brain (T08) -----------------------------------------------------------
def test_brain_graph_seeded(client):
    body = client.get("/brain/graph").json()
    assert body["stats"]["nodes"] >= 100 and body["stats"]["edges"] >= 150   # dense investor seed
    assert body["nodes"] and body["edges"][0].get("source")    # React Flow shape


def test_brain_graph_seed_reload_idempotent(client):
    body = client.get("/brain/graph/seed").json()
    # idempotent on re-seed: either a fresh full load or 0 new (already seeded in lifespan)
    assert body["stats"]["nodes_created"] == 0 or body["stats"]["nodes_created"] >= 100
    assert body["nodes"]


def test_brain_query_grounded(client):
    r = client.post("/brain/query", json={"question": "What is the BigClient contract value?"})
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] and "$2,400,000" in body["answer"] and body["citations"]


def test_brain_query_no_match_refuses(client):
    r = client.post("/brain/query", json={"question": "what is the meaning of life"})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "UNGROUNDED_OR_NO_MATCH"


def test_brain_timeline(client):
    r = client.get("/brain/timeline/person:sarah@acmecorp.com")
    assert r.status_code == 200 and r.json()["events"]


def test_brain_timeline_unknown_404(client):
    assert client.get("/brain/timeline/person:nobody").status_code == 404


def test_brain_ingest_demo_sample(client):
    # the EV sample is a registered source; its entities are all in the seed → corroborate (0 new)
    r = client.post("/brain/ingest", json={"source_path": "demo_sample.txt"})
    assert r.status_code == 200 and r.json()["entities_added"] >= 1


def test_brain_ingest_fixture_adds_new_entities(client):
    # dropping a real demo fixture extracts its OWN entities and adds genuinely NEW nodes (Part 2)
    r = client.post("/brain/ingest", json={"source_path": "email_001.eml"})
    assert r.status_code == 200
    body = r.json()
    assert "Alice Wong" in body["new_entities"] and not body["refused"]


def test_brain_path(client):
    r = client.post("/brain/path", json={"from_entity": "Sarah Chen", "to_entity": "Data Migration"})
    assert r.status_code == 200 and r.json()["found"]


def test_brain_insights_grounded(client):
    body = client.get("/brain/insights").json()
    assert body["insights"] and all(i["citations"] and i["focus"] for i in body["insights"])


def test_brain_node_detail(client):
    r = client.get("/brain/node/person:sarah@acmecorp.com")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["label"] == "Sarah Chen" and body["sources"] and body["connections"]


def test_brain_node_unknown_404(client):
    assert client.get("/brain/node/person:ghost").status_code == 404


def test_brain_graph_has_provenance_breakdown(client):
    body = client.get("/brain/graph").json()
    # the 3 seed source docs are always present (a prior ingest test may add more to the shared store)
    assert {"email_001.eml", "meeting_001.txt", "contract_001.pdf"} <= set(body["provenance_by_source"])


def test_evals_brain_scorecard(client):
    body = client.get("/evals/brain").json()
    assert body["passed"] is True and body["gates"]["provenance_completeness_100"]


# -- websocket progress ------------------------------------------------------------
def test_ws_streams_progress(client):
    with client.websocket_connect("/ws/fintech") as ws:
        first = ws.receive_json()
        assert first["step"] == "annotate" and first["n"] >= 1
