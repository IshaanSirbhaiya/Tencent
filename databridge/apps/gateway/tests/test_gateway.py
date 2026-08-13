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
    # LLM-backend metadata. The backend is environment-dependent (a key in env selects a real
    # model), so assert the INVARIANT rather than one environment: the reported backend and the
    # key-present flag must agree. Asserting `== "fake"` made this test pass only on a keyless
    # machine, which is why it read as a permanent "known failure" for so long.
    assert (body["llm_backend"] == "fake") is (body["api_key_present"] is False)
    assert body["profile"] == "cloud"
    # The clinic seed (ADR-0009), not the old 100+/150+ Acme demo graph.
    assert body["graph_nodes"] >= 60 and body["graph_edges"] >= 100


def test_brain_query_reports_which_backend_answered(client):
    """`is_real_llm` tells the UI whether a model narrated the answer or the deterministic
    claim-checked fallback did. Which one runs depends on the environment; that the flag agrees
    with the health endpoint does not."""
    r = client.post("/brain/query", json={"question": "What does the WELL2 package include?"})
    assert r.status_code == 200, r.text
    health = client.get("/health").json()
    assert r.json()["is_real_llm"] is (health["llm_backend"] != "fake")


def test_llm_detect_and_context(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from databridge_gateway.llm import build_context, build_query_harness, detect_llm

    d = detect_llm()
    assert d["backend"] == "fake" and build_query_harness(d) is None
    from brain_server import BrainTools

    t = BrainTools()
    t.load_seed()
    ctx = build_context(t, "What does the WELL2 package include?")
    assert "CLINIC BRAIN CONTEXT" in ctx["context"] and ctx["nodes_used"] and ctx["citations"]
    assert "ages 40-59" in ctx["context"]      # the package's age band reached the LLM context


def test_build_context_renders_a_missing_candidate_count_as_unknown_not_none():
    """The bug this pins: `candidate_count` is only ever SET by `clinic_seed.py`'s `_edge` calls.
    A FOR_PATIENT edge minted by `extract_graph` (the live `brain_ingest` path) builds edges with
    empty `attrs`, so `e.attrs.get("candidate_count")` comes back `None` — and the old f-string
    printed that literally: `"doc" (1 of None candidate(s) — UNVERIFIED)`. A model reading that
    context sees a specific, slightly odd NUMBER where it should see an explicit unknown. Built as
    a synthetic graph rather than routing through `brain_ingest`'s extraction pipeline, so the
    missing `candidate_count` is asserted directly rather than hoping the fixture text happens to
    reproduce it."""
    import datetime as dt

    from databridge_core import SourcePointer
    from databridge_gateway.llm import build_context
    from databridge_graph import GraphEdge, GraphNode, GraphStore, upsert_edge, upsert_node
    from databridge_graph.schema import EdgeKind, NodeKind, node_id
    from brain_server import BrainTools

    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def ptr():
        return SourcePointer(source_id="ingested.txt", kind="doc_span",
                             document_id="ingested.txt", char_start=0, char_end=1)

    patient = GraphNode(id=node_id(NodeKind.PATIENT, "zorion-qualtrix"), kind=NodeKind.PATIENT,
                        label="Zorion Qualtrix", canonical_id="zorion-qualtrix",
                        sources=[ptr()], valid_from=t0, system_from=t0)
    document = GraphNode(id=node_id(NodeKind.DOCUMENT, "ingested.txt"), kind=NodeKind.DOCUMENT,
                         label="ingested.txt", canonical_id="ingested.txt",
                         sources=[ptr()], valid_from=t0, system_from=t0)
    # NO `attrs={"candidate_count": ...}` — exactly what `extract_graph` builds, unlike
    # `clinic_seed._edge`, which always sets it.
    claim = GraphEdge(id=f"{document.id}|for_patient|{patient.id}", src_id=document.id,
                      kind=EdgeKind.FOR_PATIENT, dst_id=patient.id, sources=[ptr()],
                      valid_from=t0, system_from=t0)

    store = GraphStore(clock=lambda: t0)
    upsert_node(store, patient)
    upsert_node(store, document)
    upsert_edge(store, claim)

    ctx = build_context(BrainTools(store=store), "Zorion Qualtrix")
    assert ctx["nodes_used"], "the synthetic patient must actually be retrieved"
    assert "None" not in ctx["context"], ctx["context"]
    assert "an unknown number of candidates" in ctx["context"]


def test_build_context_caveats_a_patient_no_document_names():
    """The LLM-path twin of the `nl_query` / `node_sheet` bug, and the worst of the three.

    The caveat was emitted only when a FOR_PATIENT edge existed. Seeding the whole registry left
    289 patients edge-less, so each of them entered the MODEL'S PROMPT as a bare name and date of
    birth — the one audience that will confidently paraphrase it back to staff as an identity.
    The caveat is keyed on the KIND now, so there is no branch that can drop it."""
    from brain_server import BrainTools
    from databridge_gateway.llm import build_context
    from databridge_graph import EdgeKind, NodeKind

    t = BrainTools()
    t.load_seed()
    # A patient with NO incoming FOR_PATIENT edge — one of the ~289 the registry seed adds.
    claimed = {e["dst_id"] for e in t.store.open_edges()
               if e["kind"] == EdgeKind.FOR_PATIENT.value}
    on_file_only = [r for r in t.store.open_nodes_of_kind(NodeKind.PATIENT)
                    if r["id"] not in claimed]
    assert on_file_only, "the seed no longer holds a patient nobody referred"
    row = on_file_only[0]

    ctx = build_context(t, row["id"])           # by id: never ambiguous, always resolves
    assert row["id"] in ctx["nodes_used"], ctx
    assert "IDENTITY:" in ctx["context"], ctx["context"]
    assert "not a verified identity" in ctx["context"], ctx["context"]


def test_build_context_refuses_a_name_that_means_several_people():
    """`top_k=8` put all three "Loh Wei Ming" in one prompt and asked a model to answer about
    "the patient" — delegating the choice between three people to the component least able to
    say it is not entitled to make it. It refuses now, and hands back the ids."""
    from brain_server import BrainTools
    from databridge_gateway.llm import build_context

    t = BrainTools()
    t.load_seed()
    ctx = build_context(t, "Who is Loh Wei Ming?")
    assert ctx["refusal"], "an ambiguous name must not build a context"
    assert ctx["context"] == "" and ctx["nodes_used"] == []
    assert len(ctx["candidates"]) > 1

    # ...and the refusal is a fork in the road, not a wall: asking again with one of the ids it
    # returned resolves cleanly. A refusal with no way forward would just be a dead end.
    again = build_context(t, ctx["candidates"][0])
    assert again["refusal"] == "" and again["nodes_used"]


def test_brain_query_refuses_an_ambiguous_name_before_reaching_a_model(client):
    """End to end, on whichever branch is live. The deterministic branch already refused; the
    LLM branch did not, so exporting a key silently removed the guarantee. Both do now."""
    r = client.post("/brain/query", json={"question": "Who is Loh Wei Ming?"})
    assert r.status_code == 422, r.text
    body = r.json()["refusal"]
    assert len(body.get("candidates", [])) > 1, body
    assert "will not choose" in body["message"]


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
    assert body["stats"]["nodes"] >= 60 and body["stats"]["edges"] >= 100    # the clinic seed
    assert body["nodes"] and body["edges"][0].get("source")    # React Flow shape


def test_brain_graph_seed_reload_idempotent(client):
    body = client.get("/brain/graph/seed").json()
    # idempotent on re-seed: either a fresh full load or 0 new (already seeded in lifespan)
    assert body["stats"]["nodes_created"] == 0 or body["stats"]["nodes_created"] >= 60
    assert body["nodes"]


def test_brain_query_grounded(client):
    r = client.post("/brain/query", json={"question": "What does the WELL2 package include?"})
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] and "WELL2" in body["answer"] and body["citations"]


def test_brain_query_no_match_refuses(client):
    # NB: not "the meaning of life" — two seeded insurers are "...Life Assurance", so token
    # overlap matches. See STATUS.md (token-overlap linking, Phase 4 guardrail pass).
    r = client.post("/brain/query",
                    json={"question": "What is the airspeed velocity of an unladen swallow?"})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "UNGROUNDED_OR_NO_MATCH"


def test_brain_timeline(client):
    r = client.get("/brain/timeline/org:meridian-life-assurance")
    assert r.status_code == 200 and r.json()["events"]


def test_brain_timeline_unknown_404(client):
    assert client.get("/brain/timeline/patient:nobody").status_code == 404


def test_brain_ingest_demo_sample(client):
    # the EV sample is a registered source; its entities are all in the seed → corroborate (0 new)
    r = client.post("/brain/ingest", json={"source_path": "demo_sample.txt"})
    assert r.status_code == 200 and r.json()["entities_added"] >= 1


def test_brain_ingest_refuses_an_unregistered_source(client):
    """`sources` is an allow-list: brain_ingest must never open an arbitrary path, or any agent
    with MCP access gains a file-read primitive.

    The refusal is reported in the body rather than as an error status — the route's contract is
    unchanged from Phase 1. What matters is that nothing is read and nothing is ingested."""
    r = client.post("/brain/ingest", json={"source_path": "../../etc/passwd"})
    body = r.json()
    assert body.get("error") and "no arbitrary file access" in body["error"]
    assert body["entities_added"] == 0


def test_brain_path(client):
    r = client.post("/brain/path", json={"from_entity": "Meridian Life Assurance Pte Ltd",
                                        "to_entity": "Chest X-Ray"})
    assert r.status_code == 200 and r.json()["found"]


def test_brain_insights_grounded(client):
    body = client.get("/brain/insights").json()
    assert body["insights"] and all(i["citations"] and i["focus"] for i in body["insights"])


def test_brain_node_detail(client):
    r = client.get("/brain/node/org:meridian-life-assurance")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["label"] == "Meridian Life Assurance Pte Ltd"
    assert body["sources"] and body["connections"]


def test_brain_node_unknown_404(client):
    assert client.get("/brain/node/patient:ghost").status_code == 404


def test_brain_graph_has_provenance_breakdown(client):
    body = client.get("/brain/graph").json()
    # the real fixture files ground the seed (a prior ingest test may add more to the shared store)
    prov = set(body["provenance_by_source"])
    assert {"coverage_rules.yaml", "patient_registration_synthetic.csv"} <= prov
    assert any(s.startswith("chit_") for s in prov)


def test_evals_brain_scorecard(client):
    body = client.get("/evals/brain").json()
    assert body["passed"] is True and body["gates"]["provenance_completeness_100"]


# -- websocket progress ------------------------------------------------------------
def test_ws_streams_progress(client):
    with client.websocket_connect("/ws/fintech") as ws:
        first = ws.receive_json()
        assert first["step"] == "annotate" and first["n"] >= 1
