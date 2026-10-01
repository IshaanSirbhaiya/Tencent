"""Gateway endpoint tests (FastAPI TestClient — no live DB, no hosted model).

The TestClient drives the lifespan. One module-scoped client amortises that boot across every
test. The DataBridge-era fakes (/ingest, /schema, /annotate*, /ws, /ask, /convert/sample) and the
canned boot state behind them were removed on 2026-10-01; `test_legacy_fake_routes_are_gone`
pins that. The SQL safety wall and the P6 review gate they used to exercise are still tested
where they live: `packages/convert/tests/test_c09_safety.py`,
`packages/semantic/tests/test_confirmation.py`.
"""
from __future__ import annotations

import base64

import orjson
import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS
from databridge_gateway import create_app


@pytest.fixture(scope="module")
def client():
    # Every /brain route requires a principal (E2E F-25), so the shared client authenticates by
    # default; the 401 tests use `_anon(client)`, a second client on the same app with no header.
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:   # `with` runs the lifespan
        yield c


def _anon(client) -> TestClient:
    """The same booted app, no credential (no `with`: the lifespan already ran)."""
    return TestClient(client.app)


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
    # The header is DERIVED from the bound packs' own titles, never a literal customer name
    # (STATUS §2a item 4). Expected value computed from the same binding the code reads.
    from databridge_graph.packs import active_vocabulary, load_pack

    titles = [load_pack(p).title for p in active_vocabulary().packs]
    assert titles, "no pack is bound — the header assertion below would be vacuous"
    header = ctx["context"].splitlines()[0]
    for title in titles:
        assert title in header
    assert "Parkway" not in header or any("Parkway" in t for t in titles)
    assert ctx["nodes_used"] and ctx["citations"]
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

    # By id: a NAME never selects a subject, not even a unique one (Codex f1 #1).
    ctx = build_context(BrainTools(store=store), patient.id)
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


def test_build_context_with_an_explicit_id_puts_only_that_person_in_the_prompt():
    """R-019 finding 2: an id typed beside a shared name lifted the ambiguity refusal while BM25
    still put EVERY same-named person into the prompt — the human picked one, the model got all
    three."""
    from brain_server import BrainTools
    from databridge_gateway.llm import build_context

    t = BrainTools()
    t.load_seed()
    refused = build_context(t, "Who is Loh Wei Ming?")
    candidates = refused["candidates"]
    assert len(candidates) > 1, "the seed no longer holds a shared name — the test is vacuous"
    chosen, others = candidates[0], set(candidates[1:])

    ctx = build_context(t, f"Tell me about Loh Wei Ming {chosen}")
    assert ctx["refusal"] == ""
    assert ctx["nodes_used"] == [chosen], ctx["nodes_used"]
    cited = {c["entity_id"].split("#")[0] for c in ctx["citations"]}
    assert not (cited & others), f"another same-named person reached the prompt: {cited & others}"


def test_build_context_returns_a_pointer_for_every_fact_it_puts_in_the_prompt():
    """R-019 finding 3: the context printed attribute values and relationships, and the citations
    carried only the matched nodes' entity pointers — a date the model repeated had no citation
    that grounded it. Every attribute shown now returns ITS OWN pointer, every relationship its
    edge's, and every node a relationship names is cited."""
    from brain_server import BrainTools
    from databridge_core import SourcePointer
    from databridge_gateway.llm import build_context
    from databridge_graph.query import link_entities

    t = BrainTools()
    t.load_seed()
    question = "What does the WELL2 package include?"
    ctx = build_context(t, question)
    by_key = {c["entity_id"]: c for c in ctx["citations"]}
    checked = 0
    for node in link_entities(t.store, question, top_k=8):
        for attr, value in node.attrs.items():
            line = f"{attr}: {value}"
            if line not in ctx["context"]:
                continue
            cite = by_key.get(f"{node.id}#{attr}")
            assert cite is not None, f"{node.id}.{attr} reached the prompt with no citation"
            assert SourcePointer.model_validate(cite["pointers"][0]) == node.source_for(attr)
            checked += 1
    assert checked, "no attribute reached the prompt — the assertion above would be vacuous"
    rels = [c for c in ctx["citations"] if c["kind"] == "relationship"]
    rel_lines = [ln for ln in ctx["context"].splitlines() if "--[" in ln]
    assert len(rels) == len(rel_lines), (len(rels), len(rel_lines))
    for r in rels:
        assert r["pointers"], "a relationship citation with no pointer grounds nothing"
        src, _, dst = r["entity_id"].partition(" --[")
        assert src in by_key and dst.split("]--> ")[1] in by_key, r["entity_id"]


def test_brain_query_refuses_an_ambiguous_name_before_reaching_a_model(client):
    """End to end, on whichever branch is live. The deterministic branch already refused; the
    LLM branch did not, so exporting a key silently removed the guarantee. Both do now."""
    r = client.post("/brain/query", json={"question": "Who is Loh Wei Ming?"})
    assert r.status_code == 422, r.text
    body = r.json()["refusal"]
    assert len(body.get("candidates", [])) > 1, body
    assert "will not choose" in body["message"]


# -- removed legacy fakes ----------------------------------------------------------
# Each of these answered from data built at boot, whatever the request said. Removal, not
# relabelling: the real capabilities live at /sources (connect + approve a mapping) and
# /brain/query + /agents (ask). The list is the removal itself, so it is written down here.
_REMOVED = [
    ("POST", "/ingest"), ("GET", "/schema/fintech"), ("POST", "/annotate"),
    ("POST", "/annotate/confirm"), ("POST", "/annotate/finalize"), ("POST", "/ask"),
    ("GET", "/ask/suggested"), ("GET", "/convert/sample"),
]


@pytest.mark.parametrize("method,path", _REMOVED)
def test_legacy_fake_routes_are_gone(client, method, path):
    r = client.request(method, path, json={})
    assert r.status_code in (404, 405), (path, r.status_code, r.text[:200])


def test_no_legacy_route_or_websocket_is_registered(client):
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert paths, "no routes registered — the check below would pass vacuously"
    removed = {p for _m, p in _REMOVED} | {"/schema/{job_id}", "/ws/{job_id}"}
    assert not (paths & removed), paths & removed


def test_boot_builds_no_canned_state(client):
    """The lifespan no longer builds the fintech draft, the /ask replay store, the read-only
    fintech engine or a pre-registered demo artifact."""
    st = client.app.state
    for attr in ("extract", "report", "draft", "engine", "replay_store", "replay_artifacts",
                 "eval_e1", "jobs"):
        assert not hasattr(st, attr), attr
    # every artifact present was registered by a real /convert call in this module
    assert all(aid.startswith("CV") for aid in st.artifacts), list(st.artifacts)


# -- convert -----------------------------------------------------------------------
# The input is built HERE, from the test's own rows, and the expected quarantine count is
# derived from those rows — not read from a module that used to be wired into the gateway.
# Enough good rows that one bad row stays under the 1% quarantine threshold (the run succeeds and
# still accounts for the bad row).
_ROWS = [{"id": f"{i:03d}", "user": {"name": f"u{i}", "addr": {"city": f"c{i % 3}"}},
          "amount": f"{i * 7 + 1}.{i % 100:02d}"} for i in range(199)]
_ROWS.append({"id": "199", "user": {"name": "bad", "addr": {"city": "c0"}},
              "amount": "not-a-number"})
_SPEC = {
    "language": "jsonata",
    "expression": '$.{ "id": id, "user_name": user.name, "city": user.addr.city, "amount": amount }',
    "coercions": [{"name": "str_to_decimal", "params": {"locale": "us"}, "columns": ["amount"]}],
    "unmappable": [],
}


def _is_decimal(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _convert(client):
    src = base64.b64encode(orjson.dumps(_ROWS)).decode()
    return client.post("/convert", headers=AUTH_HEADERS, json={
        "source_b64": src, "source_format": "json", "target_format": "csv",
        "transform_spec": _SPEC})


def test_convert_json_to_csv_with_quarantine(client):
    r = _convert(client)
    assert r.status_code == 200, r.text
    body = r.json()
    out = base64.b64decode(body["result_b64"]).decode()
    assert "user_name" in out and _ROWS[0]["user"]["name"] in out
    expected_bad = sum(1 for row in _ROWS if not _is_decimal(row["amount"]))
    assert expected_bad >= 1                              # non-vacuous
    assert body["quarantine"]["quarantined"] == expected_bad
    assert body["artifact_id"]


# -- artifacts ---------------------------------------------------------------------
def test_artifacts_list_and_replay_deterministic(client):
    aid = _convert(client).json()["artifact_id"]
    lst = client.get("/artifacts").json()["artifacts"]
    assert any(a["artifact_id"] == aid for a in lst)
    r = client.post(f"/artifacts/{aid}/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["deterministic"] is True and body["checksum_ok"] is True


def test_artifact_unknown_404(client):
    assert client.get("/artifacts/NOPE").status_code == 404


# -- evals -------------------------------------------------------------------------
def test_evals_e1_scorecard(client):
    """R-019 round 2, main #3: the gold-backend score is UNWIRED (a fixed backend's fixed score
    is a canned answer however it is labelled) — the route 404s and the helper stays for CI."""
    from databridge_gateway import demo_state as ds

    assert client.get("/evals/e1").status_code == 404
    body = ds.run_eval_e1()
    assert body["suite"] == "E1" and 0.0 <= body["value"] <= 1.0
    assert "reliability" in body and "per_table" in body
    # a gold-backend score must say so on the wire, or it reads as model accuracy
    assert body["kind"] == "plumbing_proof" and body["backend"] == "deterministic_gold"


# -- company brain (T08) -----------------------------------------------------------
def test_brain_graph_seeded(client):
    body = client.get("/brain/graph").json()
    assert body["stats"]["nodes"] >= 60 and body["stats"]["edges"] >= 100    # the clinic seed
    assert body["nodes"] and body["edges"][0].get("source")    # React Flow shape


def test_brain_graph_seed_reload_idempotent(client):
    body = client.post("/brain/graph/seed", headers=AUTH_HEADERS).json()
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
    r = client.post("/brain/ingest", json={"source_path": "demo_sample.txt"},
                    headers=AUTH_HEADERS)
    assert r.status_code == 200 and r.json()["entities_added"] >= 1


def test_brain_ingest_refuses_an_unregistered_source(client):
    """`sources` is an allow-list: brain_ingest must never open an arbitrary path, or any agent
    with MCP access gains a file-read primitive.

    The refusal is reported in the body rather than as an error status — the route's contract is
    unchanged from Phase 1. What matters is that nothing is read and nothing is ingested."""
    r = client.post("/brain/ingest", json={"source_path": "../../etc/passwd"},
                    headers=AUTH_HEADERS)
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
    """Unwired like /evals/e1 (R-019 round 2, main #3): 404 on the app, helper kept."""
    from databridge_gateway.main import evals_brain_report

    assert client.get("/evals/brain").status_code == 404
    assert _anon(client).get("/evals/brain").status_code == 404
    body = evals_brain_report()
    assert body["passed"] is True and body["gates"]["provenance_completeness_100"]
    assert body["kind"] == "plumbing_proof" and body["backend"] == "deterministic_gold"


# -- /brain/reload is gated ---------------------------------------------------------
def test_brain_reload_requires_a_credential(client):
    """It re-opens the shared store and discloses its path and counts: anonymous -> 401."""
    r = _anon(client).post("/brain/reload")
    assert r.status_code == 401, r.text
    assert "graph_db_path" not in r.text
    ok = client.post("/brain/reload", headers=AUTH_HEADERS)
    assert ok.status_code == 200 and "counts" in ok.json()


# -- R-019 (Codex batch 2, n-main-legacy) ---------------------------------------------
_CONVERT_BODY = {"source_format": "json", "target_format": "csv", "transform_spec": _SPEC}


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/convert", {**_CONVERT_BODY, "source_b64": base64.b64encode(b"[]").decode()}),
    ("POST", "/brain/ingest", {"source_path": "demo_sample.txt"}),
    ("POST", "/brain/graph/seed", None),
    # E2E F-25: every DISCLOSING /brain route too (they read the user's shared graph)
    ("GET", "/brain/graph", None),
    ("GET", "/brain/insights", None),
    ("GET", "/brain/timeline/org:meridian-life-assurance", None),
    ("GET", "/brain/node/org:meridian-life-assurance", None),
    ("GET", "/brain/node/org:meridian-life-assurance/sheet", None),
    ("GET", "/brain/neighbors/org:meridian-life-assurance", None),
    ("POST", "/brain/query", {"question": "What does the WELL2 package include?"}),
    ("POST", "/brain/path", {"from_entity": "a", "to_entity": "b"}),
])
def test_writes_to_shared_state_require_a_credential(client, method, path, body):
    """#3/#5/#6 + DP: every route that writes (an artifact, the shared graph, the demo seed)
    refuses an anonymous caller — and writes nothing."""
    before_nodes = client.app.state.graph_store.counts()["nodes"]
    before_artifacts = set(client.app.state.artifacts)
    r = _anon(client).request(method, path, json=body)
    assert r.status_code == 401, (path, r.status_code, r.text[:200])
    assert "refusal" in r.json()                          # the shape the frontend parses
    assert client.app.state.graph_store.counts()["nodes"] == before_nodes
    assert set(client.app.state.artifacts) == before_artifacts


def test_convert_artifact_is_approved_by_the_principal_not_a_literal(client):
    """#3 / DP: the artifact records WHO ran the conversion — the signed-in principal."""
    from conftest import TEST_TOKENS, auth_headers

    who = "analyst-7"
    assert who in TEST_TOKENS.values()                   # non-vacuous: a real test identity
    src = base64.b64encode(orjson.dumps(_ROWS)).decode()
    r = client.post("/convert", headers=auth_headers(who),
                    json={**_CONVERT_BODY, "source_b64": src})
    assert r.status_code == 200, r.text
    art = client.get(f"/artifacts/{r.json()['artifact_id']}").json()
    assert art["approved_by"] == who


def test_convert_registers_a_replayable_artifact_for_csv_and_jsonl(client):
    """#9: the artifact frame is read by the source format's own adapter, so a CSV or JSONL
    conversion gets an artifact that exists and replays (it used to 404 / 500)."""
    rows = _ROWS[:20]
    csv_text = "id,amount\n" + "".join(f"{r['id']},{r['amount']}\n" for r in rows)
    jsonl_text = "".join(orjson.dumps(r).decode() + "\n" for r in rows)
    flat = {"language": "jsonata", "expression": '$.{ "id": id, "amount": amount }',
            "coercions": [], "unmappable": []}
    for fmt, text, spec in (("csv", csv_text, flat), ("jsonl", jsonl_text, _SPEC)):
        r = client.post("/convert", headers=AUTH_HEADERS, json={
            "source_b64": base64.b64encode(text.encode()).decode(), "source_format": fmt,
            "target_format": "csv", "transform_spec": spec})
        assert r.status_code == 200, (fmt, r.text)
        aid = r.json()["artifact_id"]
        assert client.get(f"/artifacts/{aid}").status_code == 200, fmt
        rep = client.post(f"/artifacts/{aid}/replay")
        assert rep.status_code == 200 and rep.json()["deterministic"] is True, (fmt, rep.text)
        replayed = base64.b64decode(rep.json()["result_b64"]).decode()
        assert rows[0]["amount"] in replayed, fmt             # replayed over the real input


def test_convert_artifact_ids_are_unique_not_a_count(client):
    """#10: ids were `CV{len(artifacts)}` — two conversions racing picked the same one."""
    ids = {_convert(client).json()["artifact_id"] for _ in range(3)}
    assert len(ids) == 3
    assert all(i.startswith("CV") for i in ids)


def test_brain_query_refuses_as_of_rather_than_echoing_it(client):
    """#7: neither branch reads the store as of an instant, so the instant is refused, not
    returned beside a present-day answer."""
    r = client.post("/brain/query", json={"question": "What does the WELL2 package include?",
                                          "as_of": "2020-01-01T00:00:00+00:00"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "AS_OF_UNSUPPORTED"


def test_value_token_check_is_membership_not_a_list():
    """#2: the check computes — a digit-bearing token of the answer passes iff it is in the
    evidence or the question."""
    from databridge_gateway.main import unsupported_value_tokens

    evidence = "WELL2 package: ages 40-59, price 300.00 on 2026-05-01."
    assert unsupported_value_tokens("WELL2 costs 300.00 (ages 40-59).", evidence) == []
    bad = unsupported_value_tokens("WELL2 costs 450.00 since 2025-12-31.", evidence)
    assert bad == ["450.00", "2025-12-31"]
    # round 2, main #1: a value the QUESTION holds is not evidence
    assert unsupported_value_tokens("Anything 7?", evidence) == ["7"]


def test_llm_narration_with_an_invented_value_is_not_served_as_grounded(client, monkeypatch):
    """#2 end to end: a model that invents a value its evidence does not hold is not labelled
    grounded — its answer is dropped for the claim-checked deterministic one."""
    from databridge_gateway.llm import build_context

    q = "What does the WELL2 package include?"
    ctx = build_context(client.app.state.brain, q)
    assert ctx["nodes_used"], "the seed must ground this question or the test is vacuous"
    invented = "9999-12-31"
    assert invented not in ctx["context"]

    class _Res:
        def __init__(self, text):
            self.text = text

    class _Stub:
        def __init__(self, text):
            self.text = text
            self.calls = 0

        def complete_text(self, task, messages):
            self.calls += 1
            return _Res(self.text)

    lying = _Stub(f"WELL2 was withdrawn on {invented}.")
    monkeypatch.setattr(client.app.state, "brain_llm", lying, raising=False)
    r = client.post("/brain/query", json={"question": q})
    assert lying.calls == 1
    assert r.status_code == 200, r.text
    assert r.json()["is_real_llm"] is False and invented not in r.json()["answer"]

    # A narration whose values are all in the evidence IS served, and says what was checked.
    token = next(t for t in ctx["context"].split() if any(c.isdigit() for c in t))
    honest = _Stub(f"WELL2: see {token}")
    monkeypatch.setattr(client.app.state, "brain_llm", honest, raising=False)
    body = client.post("/brain/query", json={"question": q}).json()
    from databridge_gateway.main import NARRATION_CLAIM_CHECK

    assert body["is_real_llm"] is True and body["claim_check"] == NARRATION_CLAIM_CHECK
    assert body["claim_check_scope"]


def test_health_is_degraded_when_the_store_cannot_be_read(client, monkeypatch):
    """#11: a store that cannot be counted is not "ok" with zero counts."""
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(client.app.state.brain.store, "counts", broken)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert r.json()["graph_nodes"] is None


def _spy_store_close(monkeypatch, closes: list):
    import brain_server

    real_open = brain_server.open_shared_store

    def spy(path):
        store = real_open(path)
        orig = store.close

        def close():
            closes.append(1)
            return orig()

        store.close = close
        return store

    monkeypatch.setattr(brain_server, "open_shared_store", spy)


def test_lifespan_closes_the_store_when_boot_fails_after_opening_it(tmp_path, monkeypatch):
    """#8: `start_platform` raising after the store is opened must still close it."""
    from databridge_gateway import platform_runtime

    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(tmp_path / "g.db"))
    closes: list = []
    _spy_store_close(monkeypatch, closes)

    async def boom(app):
        raise RuntimeError("platform failed to start")

    monkeypatch.setattr(platform_runtime, "start_platform", boom)
    with pytest.raises(RuntimeError, match="platform failed"):
        with TestClient(create_app()):
            pass
    assert closes == [1]


def test_lifespan_shutdown_continues_past_a_failing_step(tmp_path, monkeypatch):
    """#8: one cleanup raising does not skip the store close."""
    from databridge_gateway import main as gw_main

    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(tmp_path / "g2.db"))
    closes: list = []
    _spy_store_close(monkeypatch, closes)

    def fleet_boom(app):
        raise RuntimeError("fleet dispose failed")

    monkeypatch.setattr(gw_main, "dispose_fleet", fleet_boom)
    with TestClient(create_app()):
        pass
    assert closes == [1]


def test_every_brain_route_on_the_app_is_gated(client):
    """F-25, derived: every `/brain/*` route registered on the app answers 401 anonymously —
    the list comes from the app's own routes, so a new ungated /brain route fails here."""
    from fastapi.routing import APIRoute

    # main.py's /brain routes (other modules' /brain routes — export, intake's screen/suggested —
    # are gated or not in their own tracks).
    routes = [r for r in client.app.routes
              if isinstance(r, APIRoute)
              and r.path.startswith(("/brain", "/artifacts", "/convert"))
              and r.endpoint.__module__ == "databridge_gateway.main"]
    # non-vacuous for the widened prefixes too: replay re-emits converted data (R-019)
    assert any(r.path.startswith("/artifacts") for r in routes)
    assert len(routes) >= 10, [r.path for r in routes]           # non-vacuous
    anon = _anon(client)
    ungated = []
    for r in routes:
        url = r.path.replace("{entity_id:path}", "x").replace("{", "").replace("}", "")
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            resp = anon.request(method, url, json={})
            if resp.status_code != 401:
                ungated.append((method, r.path, resp.status_code))
    assert ungated == []


def test_health_reports_the_llm_routing_so_a_model_fallback_is_visible(client, monkeypatch):
    """R-020 F-21 follow-up: `/health` carries `detect_llm()`'s per-alias routing, so a model
    that fell back from its configured route is visible without reading logs."""
    meta = client.app.state.llm_meta
    assert "routing" in meta                                   # the key detect_llm() returns
    assert client.get("/health").json()["llm_routing"] == meta["routing"]
    # non-vacuous: a recorded fallback is reported exactly as detect_llm() recorded it
    fallback = {"sonnet": {"configured": "anthropic/claude-sonnet-5-5",
                           "effective": "openai/gpt-4o", "fallback": True}}
    monkeypatch.setattr(client.app.state, "llm_meta", {**meta, "routing": fallback})
    assert client.get("/health").json()["llm_routing"] == fallback


@pytest.mark.parametrize("narration", [
    "WELL2 includes about a hundred tests.",                 # hedged + spelled magnitude, no digit
    "WELL2 has been taken by thousands of patients.",       # spelled magnitude, no digit
])
def test_llm_narration_is_run_through_check_claims_like_nl_query(client, monkeypatch, narration):
    """R-019-real-connections deferred #3: the LLM answer gets the same deterministic
    `check_claims` gate the deterministic `nl_query` applies. A quantity with no digit slips past
    the value-token check; `check_claims` flags it, so the answer is dropped (not served as
    grounded) and the violations are reported."""
    from databridge_convert.generate.claim_check import check_claims
    from databridge_gateway.llm import build_context
    from databridge_gateway.main import unsupported_value_tokens

    q = "What does the WELL2 package include?"
    ctx = build_context(client.app.state.brain, q)
    assert ctx["nodes_used"]                                            # non-vacuous
    assert not unsupported_value_tokens(narration, ctx["context"])      # the old check passes it
    expected = [v.token for v in check_claims(narration, facts=[]).violations]
    assert expected                                                     # derived, non-empty

    class _Res:
        text = narration

    class _Stub:
        def complete_text(self, task, messages):
            return _Res()

    monkeypatch.setattr(client.app.state, "brain_llm", _Stub(), raising=False)
    r = client.post("/brain/query", json={"question": q})
    body = r.json()
    assert body.get("is_real_llm") is not True
    assert narration not in str(body.get("answer", ""))
    assert body["llm_answer_dropped"]["violations"] == expected


# -- R-019 round 2 (Codex, n-main-legacy) ---------------------------------------------
_R2_CTX = {"context": "Node: Alice Tan (person). Document: WELL2 package brochure, price 300.00.",
           "nodes_used": ["person:alice"], "citations": [], "passages": []}


class _R2Res:
    def __init__(self, text):
        self.text = text


class _R2Stub:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete_text(self, task, messages):
        self.calls += 1
        return _R2Res(self.text)


def _r2_ask(client, monkeypatch, narration, question="Summarise the WELL2 brochure."):
    from databridge_gateway import llm as gw_llm

    monkeypatch.setattr(gw_llm, "build_context", lambda brain, q: dict(_R2_CTX))
    stub = _R2Stub(narration)
    monkeypatch.setattr(client.app.state, "brain_llm", stub, raising=False)
    r = client.post("/brain/query", json={"question": question})
    assert stub.calls == 1
    return r.json()


def test_r2_a_value_only_the_question_holds_is_not_evidence(client, monkeypatch):
    """#1: 'Is the price 987654?' does not make 987654 supported."""
    value = "987654"
    assert value not in _R2_CTX["context"]                              # non-vacuous
    body = _r2_ask(client, monkeypatch, f"The WELL2 price is {value}.",
                   question=f"Is the WELL2 price {value}?")
    assert body.get("is_real_llm") is not True
    assert value not in str(body.get("answer", ""))
    assert value in body["llm_answer_dropped"]["violations"]


def test_r2_the_narration_is_screened_for_identity_assertions(client, monkeypatch):
    """#2: an innocent question's answer may not assert who someone is."""
    for narration in ("The brochure names Alice Tan, who is the same person as the registry "
                      "record.", "Alice Tan is the person in the brochure.",
                      "Alice Tan was identified as the brochure's holder."):
        body = _r2_ask(client, monkeypatch, narration)
        assert body.get("is_real_llm") is not True, narration
        assert "identity" in body["llm_answer_dropped"]["reason"], narration


def test_r2_the_narration_may_only_name_what_its_evidence_names(client, monkeypatch):
    """#2: an invented name carries no digit, and is caught anyway."""
    invented = "Zorblax"
    assert invented.lower() not in _R2_CTX["context"].lower()
    body = _r2_ask(client, monkeypatch, f"The WELL2 brochure is published by {invented}.")
    assert body.get("is_real_llm") is not True
    assert invented in body["llm_answer_dropped"]["violations"]


def test_r2_a_narration_that_passes_every_check_is_served_and_says_what_was_checked(
        client, monkeypatch):
    """Control: the checks do not refuse everything — a supported narration is served."""
    from databridge_gateway.main import NARRATION_CLAIM_CHECK, NARRATION_CLAIM_SCOPE

    body = _r2_ask(client, monkeypatch, "The WELL2 package brochure lists Alice Tan and a price "
                                        "of 300.00.")
    assert body["is_real_llm"] is True and body["grounded"] is True
    assert body["claim_check"] == NARRATION_CLAIM_CHECK
    assert body["claim_check_scope"] == NARRATION_CLAIM_SCOPE
    # INT3 (R-020 f2 hand-off 4): what the narrator dropped and which subjects the served text
    # names travel with the answer, as structured data
    assert isinstance(body["dropped_sentences"], list)
    assert isinstance(body["subject_mentions"], list)


def test_r2_identity_screen_units():
    from databridge_gateway.main import narration_identity_claims

    for bad in ("Alice is the patient.", "They are the same individual.",
                "Her identity has been verified.", "Record 7 is a match for the visitor."):
        assert narration_identity_claims(bad), bad
    for ok in ("The document names Alice as the policy holder named on page 1.",
               "Which package matches this record is WELL2.",
               "Two registry candidates share the surname; a person decides."):
        assert narration_identity_claims(ok) == [], ok


def test_r2_evals_routes_are_unwired_not_deleted(client):
    """#3: no route answers with the gold score; the helpers still exist for CI."""
    from fastapi.routing import APIRoute

    from databridge_gateway import demo_state as ds
    from databridge_gateway.main import evals_brain_report

    paths = {r.path for r in client.app.routes if isinstance(r, APIRoute)}
    assert not {p for p in paths if p.startswith("/evals")}
    assert callable(ds.run_eval_e1) and callable(evals_brain_report)


def _convert_to(client, target):
    src = base64.b64encode(orjson.dumps(_ROWS)).decode()
    return client.post("/convert", headers=AUTH_HEADERS, json={
        "source_b64": src, "source_format": "json", "target_format": target,
        "transform_spec": _SPEC})


def test_r2_replay_honours_the_target_format_and_compares_with_the_original(client):
    """#4: JSON stays JSON, and `deterministic` means 'reproduces the original conversion'."""
    r = _convert_to(client, "json")
    assert r.status_code == 200, r.text
    original = base64.b64decode(r.json()["result_b64"])
    orjson.loads(original)                                               # it IS json
    rep = client.post(f"/artifacts/{r.json()['artifact_id']}/replay").json()
    assert rep["target_format"] == "json"
    assert base64.b64decode(rep["result_b64"]) == original
    assert rep["deterministic"] is True and rep["matches_original"] is True
    # a replay that does not reproduce the original is NOT reported deterministic
    aid = r.json()["artifact_id"]
    client.app.state.artifacts[aid]["result_sha256"] = "0" * 64
    rep = client.post(f"/artifacts/{aid}/replay").json()
    assert rep["deterministic"] is False and rep["matches_original"] is False


def test_r2_the_quarantine_denominator_is_parsed_records_not_newlines():
    """#6: one CSV record with many quoted newlines is ONE record."""
    from databridge_gateway.demo_state import source_record_count

    body = "\n".join(f"line {i}" for i in range(40))
    csv_bytes = f'id,note\n1,"{body}"\n'.encode()
    assert csv_bytes.count(b"\n") > 40                                   # the old estimate
    assert source_record_count(csv_bytes, "csv") == 1


def test_r2_artifact_ids_are_full_uuids_and_a_collision_is_redrawn(client, monkeypatch):
    """#9: a collision never makes a later conversion answer with an earlier artifact."""
    import uuid

    real = uuid.uuid4
    fixed = uuid.UUID(int=7)
    seq = iter([fixed, fixed])
    monkeypatch.setattr(uuid, "uuid4", lambda: next(seq, None) or real())
    a = _convert(client).json()["artifact_id"]
    b = _convert(client).json()["artifact_id"]
    assert a == f"CV-{fixed.hex}" and b != a and len(b) == len(a) == 3 + 32
    assert client.app.state.artifacts[a]["artifact"].artifact_id == a
    assert client.app.state.artifacts[b]["artifact"].artifact_id == b


def test_r2_listing_artifacts_while_converting_never_fails(client):
    """#8: the registry is read under its lock, as a snapshot."""
    import concurrent.futures as cf

    def work(i):
        return (_convert(client) if i % 2 else client.get("/artifacts")).status_code

    with cf.ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(work, range(24)))
    assert codes and set(codes) == {200}


class _SpyStore:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _shutdown_app(monkeypatch):
    from types import SimpleNamespace

    from databridge_gateway import main as gw_main

    monkeypatch.setattr(gw_main, "dispose_fleet", lambda app: None)
    monkeypatch.setattr(gw_main, "dispose_orchestrator_fleet", lambda app: None)
    return SimpleNamespace(state=SimpleNamespace(graph_store=_SpyStore())), gw_main


def test_r2_shutdown_closes_the_store_when_stop_is_cancelled(monkeypatch):
    """#5: CancelledError is a BaseException; the store is still closed, then it propagates."""
    import asyncio

    from databridge_gateway import platform_runtime

    app, gw_main = _shutdown_app(monkeypatch)
    disposed = []
    monkeypatch.setattr(gw_main, "dispose_fleet", lambda a: disposed.append(1))

    async def cancelled(a):
        raise asyncio.CancelledError()

    monkeypatch.setattr(platform_runtime, "stop_platform", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gw_main._shutdown(app))
    assert app.state.graph_store.closed == 1 and disposed == [1]


def test_r2_shutdown_closes_the_store_when_the_platform_import_fails(monkeypatch):
    """#5: an import failure is one failed step, not an escape past the close."""
    import asyncio
    import sys

    app, gw_main = _shutdown_app(monkeypatch)
    monkeypatch.setitem(sys.modules, "databridge_gateway.platform_runtime", None)
    asyncio.run(gw_main._shutdown(app))
    assert app.state.graph_store.closed == 1


def test_r2_the_seed_is_not_a_get_route(client):
    """#7: GET never mutates — the seed route accepts POST only."""
    from fastapi.routing import APIRoute

    seed = [r for r in client.app.routes
            if isinstance(r, APIRoute) and r.path == "/brain/graph/seed"]
    assert seed and all("GET" not in r.methods for r in seed)
    assert client.get("/brain/graph/seed").status_code == 405


# -- FXB hand-off (R-020-EF2-brain-routing, "Round: Codex f1") --------------------------
def _node_ids_by_subjectness(client):
    from databridge_graph.packs import active_vocabulary

    vocab = active_vocabulary()
    nodes = client.app.state.brain.graph(limit=10_000)["nodes"]
    subj = [n["id"] for n in nodes if vocab.is_subject(str(n.get("kind")))]
    other = [n["id"] for n in nodes if not vocab.is_subject(str(n.get("kind")))]
    assert subj and other, "the seed must hold both kinds or the test proves nothing"
    return subj, other


def test_fxb_the_llm_branch_returns_the_narrators_value_bindings(client, monkeypatch):
    """(1) every value served carries its [C<n>] binding as structured `values` too."""
    from databridge_gateway import llm as gw_llm

    bindings = [{"value": "300.00", "tags": ["C1"], "pointers": [{"source_id": "brochure"}]}]

    class _Res:
        text = "The WELL2 package brochure lists a price of 300.00 [C1]."

        def __init__(self):
            self.bindings = bindings

    class _Stub:
        def complete_text(self, task, messages):
            return _Res()

    monkeypatch.setattr(gw_llm, "build_context", lambda brain, q: dict(_R2_CTX))
    monkeypatch.setattr(client.app.state, "brain_llm", _Stub(), raising=False)
    body = client.post("/brain/query", json={"question": "Summarise the WELL2 brochure."}).json()
    assert body["is_real_llm"] is True
    assert body["values"] == bindings


@pytest.mark.parametrize("branch", ["llm", "deterministic"])
def test_fxb_a_name_landing_on_a_subject_has_its_own_refusal_code(client, monkeypatch, branch):
    """(2) SUBJECT_NEEDS_SELECTION when the candidates are subjects; AMBIGUOUS_ENTITY stays for a
    shared name that selects no subject. Decided from the candidates' kinds, not the text."""
    from databridge_gateway import llm as gw_llm

    subj, other = _node_ids_by_subjectness(client)
    for cands, code in (([subj[0]], "SUBJECT_NEEDS_SELECTION"),
                        (other[:2], "AMBIGUOUS_ENTITY")):
        refusal = {"refusal": "choose one", "candidates": cands}
        if branch == "llm":
            monkeypatch.setattr(gw_llm, "build_context",
                                lambda brain, q, r=refusal: {**_R2_CTX, **r, "nodes_used": []})
            monkeypatch.setattr(client.app.state, "brain_llm", _R2Stub("unused"), raising=False)
        else:
            monkeypatch.setattr(client.app.state, "brain_llm", None, raising=False)
            monkeypatch.setattr(client.app.state.brain, "brain_query",
                                lambda q, as_of=None, r=refusal: {
                                    "grounded": False, "answer": "", **r})
        r = client.post("/brain/query", json={"question": "Tell me about them."})
        assert r.status_code == 422, r.text
        assert r.json()["refusal"]["code"] == code, (branch, cands)
        assert r.json()["refusal"]["candidates"] == cands
