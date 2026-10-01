"""`/evidence` over a graph the engine has never seen the vocabulary for.

The clinical tests (`test_evidence_routes.py`) pin R-26/R-28 under the healthcare pack. These pin
the same page under a synthetic pack (`generic_graph.deskpack`) and a pack with no subject at all
(`plainpack`), over a real capture store and real ingestion ledgers written to a temp directory:

* an ordinary entity is released by POINTER PROVENANCE, with each value's captured snippet, the
  source's uri / title / retrieved_at / content hash, and a re-verify that recomputes the hash;
* a subject, a claim-capable document, and an entity grounded only in a document that names a
  subject are all refused with AWAITING_HUMAN_CONFIRMATION — derived from `subject_kinds` and the
  `about` meta-edge, never from a kind name;
* the runs that produced an entity and every exclusion its source recorded are reported, found by
  pattern (the fixture writes `excluded_ocr.jsonl`, not `excluded.jsonl`);
* tampering with the captured bytes flips the re-verify to `hash_mismatch`.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.evidence_routes import register_evidence_routes

from conftest import AUTH_HEADERS
from generic_graph import bound, build_deskpack_graph, write_packs


@pytest.fixture()
def world(tmp_path, monkeypatch):
    packs = write_packs(tmp_path)
    with bound(packs, "deskpack"):
        g = build_deskpack_graph(tmp_path)
        monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(g.capture_dir))
        monkeypatch.setenv("WALNUT_INGEST_DIR", str(g.ingest_dir))
        app = FastAPI()
        app.state.graph_store = g.store          # the shared store; no brain, no ledger
        register_evidence_routes(app)
        with TestClient(app, headers=AUTH_HEADERS) as client:
            yield g, client


def test_an_ordinary_entity_is_released_by_its_pointers_with_the_captured_snippet(world):
    g, client = world
    r = client.get("/evidence/trace/topic:release-trains")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authorisation"]["basis"] == "pointer_provenance"
    assert body["authorisation"]["policy"]["subject_kinds"] == ["person"]
    assert body["sources"], "an entity with no pointer should not exist"
    for src in body["sources"]:
        capture = src["capture"]
        assert capture, "the pointer carries a capture digest, so the snippet must be read back"
        pointer = src["pointer"]
        # The highlighted span IS the cited characters of the captured text — derived, not typed.
        assert capture["snippet"]["match"] == g.texts["wiki"][
            pointer["char_start"]:pointer["char_end"]]
        assert capture["source"]["uri"].startswith("file:///synthetic/wiki")
        assert capture["source"]["retrieved_at"]
        assert capture["capture_digest"] == f"sha256:{g.digests['wiki']}"
        assert src["verify"]["status"] == "verified"
        assert src["verify"]["hash_matches"] is True
        assert src["ingestion_route"] == "ingest"
        assert src["ingest_source"] == "wiki"


def test_each_value_is_reverified_against_its_own_span(world):
    g, client = world
    node = client.get("/evidence/trace/topic:release-trains").json()["node"]
    evidence = node["attribute_evidence"]
    assert set(evidence) == set(node["attrs"]) and evidence, "every value needs its own pointer"
    for key, item in evidence.items():
        assert item["value"] == node["attrs"][key]
        assert item["capture"]["snippet"]["match"] == str(item["value"])
        assert item["verify"]["value_in_span"] is True
        assert item["verify"]["status"] == "verified"


def test_tampered_capture_bytes_fail_the_reverify(world):
    g, client = world
    digest = g.digests["wiki"]
    raw = g.capture_dir / digest[:2] / digest
    raw.write_bytes(raw.read_bytes() + b" edited later")
    src = client.get("/evidence/trace/topic:release-trains").json()["sources"][0]
    assert src["verify"]["status"] == "hash_mismatch"
    assert src["verify"]["hash_matches"] is False


def test_a_missing_capture_is_reported_missing_not_verified(world):
    g, client = world
    digest = g.digests["wiki"]
    folder = g.capture_dir / digest[:2]
    for path in folder.glob(f"{digest}*"):
        path.unlink()
    src = client.get("/evidence/trace/topic:release-trains").json()["sources"][0]
    assert src["verify"]["status"] == "capture_missing"
    assert src["capture"]["snippet"] is None


@pytest.mark.parametrize("entity", [
    "person:sn-0042",               # a subject kind
    "memo:memos/rota.txt",          # a document kind that can claim a subject
    "topic:rota-change",            # an ordinary kind grounded only in a subject-naming memo
    "memo:memos/rota.txt|concerns|person:sn-0042",   # the claim edge itself
])
def test_subject_bearing_entities_wait_for_a_human(world, entity):
    _g, client = world
    r = client.get(f"/evidence/trace/{entity}")
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"
    assert "node" not in body and "sources" not in body


def test_a_released_neighbour_list_does_not_label_a_subject_bearing_neighbour(world):
    _g, client = world
    body = client.get("/evidence/trace/topic:release-trains").json()
    neighbours = {r["other_id"]: r for r in body["relationships"]}
    assert "memo:memos/rota.txt" in neighbours, "the fixture links the memo to this topic"
    assert neighbours["memo:memos/rota.txt"]["other_label"] is None
    assert neighbours["wiki_page:wiki/runbook.md"]["other_label"] == "Harbour Lights runbook"


def test_the_runs_and_exclusions_behind_an_entity_are_reported(world):
    g, client = world
    ingestion = client.get("/evidence/trace/topic:release-trains").json()["ingestion"]
    assert {r["source"] for r in ingestion["runs"]} == {"wiki"}
    wiki = next(s for s in ingestion["sources"] if s["source"] == "wiki")
    assert wiki["excluded"] == sum(g.excluded_reasons.values()) > 0
    reported = {x["reason"]: x["n"] for x in wiki["exclusion_reasons"]}
    assert reported == g.excluded_reasons


def test_the_source_register_lists_every_source_and_its_exclusions(world):
    g, client = world
    body = client.get("/evidence/sources").json()
    sources = {s["source"]: s for s in body["sources"]}
    assert set(sources) == {p.name for p in g.ingest_dir.iterdir()}
    assert sources["wiki"]["latest"]["excluded"] == sum(g.excluded_reasons.values())
    one = client.get("/evidence/sources/wiki").json()
    assert one["runs"][0]["ledger_files"], "the ledgers were found by pattern"
    assert client.get("/evidence/sources/nope").status_code == 404
    assert client.get("/evidence/sources/..").status_code == 404


def test_the_browse_list_names_only_what_it_could_release(world):
    g, client = world
    body = client.get("/evidence/recent").json()
    listed = {e["id"] for e in body["entities"]}
    assert listed, "nothing listed, so nothing checked"
    assert "person:sn-0042" not in listed and "memo:memos/rota.txt" not in listed
    assert body["withheld"] > 0 and body["withheld_reason"]
    assert body["total"] == len(g.store.open_nodes())
    for entity_id in listed:
        assert client.get(f"/evidence/trace/{entity_id}").status_code == 200


def test_an_empty_ingest_directory_is_an_empty_register_said_out_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "nothing-here"))
    app = FastAPI()
    register_evidence_routes(app)
    with TestClient(app, headers=AUTH_HEADERS) as client:
        body = client.get("/evidence/sources").json()
        assert body["sources"] == [] and body["note"]
        assert client.get("/evidence/trace/x:y").json()["refusal"]["code"] == \
            "EVIDENCE_UNAVAILABLE"


def test_a_pack_with_no_subject_releases_everything_by_pointer(tmp_path, monkeypatch):
    """No subject kinds declared → nothing is gated. The gate is a consequence of a pack's own
    declaration, never a default the engine applies to documents in general."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind
    from databridge_graph.store import GraphStore

    from generic_graph import WHEN, write_capture

    packs = write_packs(tmp_path)
    text = "Garden notes: tomatoes need sun."
    digest = write_capture(tmp_path / "cap", text, {"source": "notes", "uri": "file:///n.txt"})
    monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(tmp_path / "cap"))
    with bound(packs, "plainpack"):
        store = GraphStore(str(tmp_path / "plain.db"))
        pointer = SourcePointer(source_id="notes", kind="doc_span", document_id="n.txt",
                                char_start=0, char_end=12, capture_digest=digest)
        store.add_node(GraphNode(id="page:n.txt", kind=NodeKind("page"), label="Garden notes",
                                 sources=[pointer], valid_from=WHEN, system_from=WHEN))
        app = FastAPI()
        app.state.graph_store = store
        register_evidence_routes(app)
        with TestClient(app, headers=AUTH_HEADERS) as client:
            body = client.get("/evidence/trace/page:n.txt").json()
    assert body["authorisation"]["basis"] == "pointer_provenance"
    assert body["authorisation"]["policy"]["subject_kinds"] == []
    assert body["sources"][0]["capture"]["snippet"]["match"] == text[0:12]


# --- R-017 (Codex t08) ------------------------------------------------------------------------


class _FakeLedger:
    """The two methods `/evidence` reads (`entries`, `pointer_of`), over committed rows built here.
    A committed row names a DOCUMENT by its pointer and a SUBJECT by `selected_patient_id`."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def entries(self) -> list[dict]:
        return list(self._rows)

    @staticmethod
    def pointer_of(row: dict):
        from databridge_core import SourcePointer
        return SourcePointer.model_validate_json(row["source_pointer_json"])


def _committed_row(pointer, selected: str, rowid: int = 1) -> dict:
    import json as _json
    return {"rowid_pk": rowid, "intake_id": f"intake-{rowid}", "field_path": "subject",
            "value_json": _json.dumps(selected), "source_pointer_json": pointer.model_dump_json(),
            "extracted_at": "2026-09-01T00:00:00Z", "confirmed_by": "staff:test",
            "confirmed_at": "2026-09-01T00:00:00Z", "selected_patient_id": selected,
            "supersedes": None}


def _memo_commit_selecting(g, selected: str) -> _FakeLedger:
    # The committed row's own pointer names the memo — the document the person node cites.
    pointer = g.store.sources_for("memo:memos/rota.txt")[0]
    return _FakeLedger([_committed_row(pointer, selected)])


def test_a_document_commit_selecting_someone_else_does_not_release_this_subject(world):
    """t08 #1. The memo was committed, but the human selected a DIFFERENT person. The subject the
    memo also names is exactly the one nobody confirmed; the memo's commit must not release it."""
    g, client = world
    client.app.state.ledger = _memo_commit_selecting(g, "sn-9999")
    r = client.get("/evidence/trace/person:sn-0042")
    assert r.status_code == 403, r.text
    assert r.json()["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"
    # …and a confirmation naming THIS subject does release it (the gate still opens).
    client.app.state.ledger = _memo_commit_selecting(g, "sn-0042")
    assert client.get("/evidence/trace/person:sn-0042").status_code == 200


def test_a_confirmed_document_does_not_label_its_unconfirmed_candidate(world):
    """t08 #2. The memo is releasable (committed), its claim to sn-0042 is a claim, and sn-0042's
    NAME is that unconfirmed subject's value: it is not printed beside the claim."""
    g, client = world
    client.app.state.ledger = _memo_commit_selecting(g, "sn-9999")
    body = client.get("/evidence/trace/memo:memos/rota.txt").json()
    claims = {c["other_id"]: c for c in body["identity_claims"]}
    assert "person:sn-0042" in claims, "the fixture's claim edge must be reported as a claim"
    assert claims["person:sn-0042"]["other_label"] is None
    person_label = g.store.open_node_row("person:sn-0042")["label"]
    assert person_label and person_label not in str(body["identity_claims"])


def test_a_safe_document_id_cannot_launder_an_unrelated_capture_digest(world):
    """t08 #3. A pointer pairing a safe document's id with the digest of a subject-bearing
    capture the graph does not index must fail closed — one safe key proves nothing about the
    others, and the digest is what the capture store is read by."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind

    from generic_graph import WHEN, write_capture

    g, client = world
    secret = "Private note: Quinn Vale's home address is 4 Harbour Row."
    digest = write_capture(g.capture_dir, secret, {"source": "stray", "uri": "file:///x"})
    laundered = SourcePointer(source_id="wiki", kind="doc_span", document_id="wiki/runbook.md",
                              char_start=0, char_end=len(secret),
                              capture_digest=f"sha256:{digest}")
    g.store.add_node(GraphNode(id="topic:laundered", kind=NodeKind("topic"), label="laundered",
                               sources=[laundered], valid_from=WHEN, system_from=WHEN))
    r = client.get("/evidence/trace/topic:laundered")
    assert r.status_code == 403, r.text
    assert "Harbour Row" not in r.text


def test_evidence_surfaces_produced_by_when_the_entity_carries_it(world):
    """BUILD_PLAN §5.8: a node written from model output records who produced it; the evidence page
    shows it. Additive — an entity without it reports ``None``, not an error."""
    from databridge_graph.schema import GraphNode, NodeKind

    from generic_graph import WHEN, doc_pointer

    g, client = world
    assert client.get("/evidence/trace/topic:release-trains").json()["node"]["produced_by"] is None
    record = {"agent": "adapter_agent:wiki", "model": "offline/gold", "task_class": "extract",
              "run_id": "run-001", "at": "2026-09-01T10:00:00Z"}
    ptr = doc_pointer(g.digests["wiki"], g.texts["wiki"], "deployment freeze", source="wiki",
                      document_id="wiki/runbook.md")
    g.store.add_node(GraphNode(id="topic:freeze", kind=NodeKind("topic"), label="freeze",
                               sources=[ptr], attrs={"produced_by": record},
                               attr_sources={"produced_by": ptr},
                               valid_from=WHEN, system_from=WHEN))
    node = client.get("/evidence/trace/topic:freeze").json()["node"]
    assert node["produced_by"] == record


def test_an_edited_extracted_text_file_is_not_verified_by_the_raw_bytes_hash(world):
    """t08 #5. The digest authenticates the raw BYTES; the span is read from `<digest>.txt`. When
    the two no longer agree (the bytes hash fine, the text file was rewritten), "verified" would
    vouch for text the hash never covered."""
    g, client = world
    digest = g.digests["wiki"]
    txt = g.capture_dir / digest[:2] / f"{digest}.txt"
    txt.write_text(g.texts["wiki"].replace("Harbour", "Harpoon"), encoding="utf-8")
    src = client.get("/evidence/trace/topic:release-trains").json()["sources"][0]
    assert src["verify"]["hash_matches"] is True
    assert src["verify"]["status"] == "text_unbound"
    assert src["verify"]["text_bound"] is False


def test_a_text_digest_in_the_mutable_meta_sidecar_does_not_bind_edited_text(world):
    """R-019 t08 #6 (replaces R-017's "bound by its own recorded digest" test, which asserted the
    hole). `<digest>.json` sits beside the text in the same mutable folder and nothing
    authenticates it: rewrite the text AND its `text_sha256` and the old code said `verified`.
    A digest is a binding only when something the capture digest covers — or the pointer —
    carries it; the sidecar is neither, so the span is `text_unbound`."""
    import hashlib
    import json as _json

    g, client = world
    digest = g.digests["wiki"]
    folder = g.capture_dir / digest[:2]
    forged = g.texts["wiki"].replace("Harbour", "Harpoon")
    (folder / f"{digest}.txt").write_bytes(forged.encode("utf-8"))
    meta = _json.loads((folder / f"{digest}.json").read_text(encoding="utf-8"))
    meta["text_sha256"] = hashlib.sha256(forged.encode("utf-8")).hexdigest()
    (folder / f"{digest}.json").write_text(_json.dumps(meta), encoding="utf-8")
    src = client.get("/evidence/trace/topic:release-trains").json()["sources"][0]
    assert src["verify"]["hash_matches"] is True, "precondition: the raw bytes are untouched"
    assert src["verify"]["status"] == "text_unbound", src["verify"]
    assert src["verify"]["text_bound"] is False


# --- R-019 (Codex batch 2, t08) -----------------------------------------------------------------
def test_process_memory_cannot_move_a_commit_onto_another_document(world):
    """t08 r2 #1. A committed row whose OWN pointer names the handbook must not authorise the
    memo because the in-memory intakes map currently says that intake read the memo. The
    authorisation root is the ledger alone."""
    from types import SimpleNamespace

    g, client = world
    handbook_ptr = g.store.sources_for("wiki_page:handbook/team.md")[0]
    client.app.state.ledger = _FakeLedger([_committed_row(handbook_ptr, "sn-0042")])
    draft = SimpleNamespace(record=SimpleNamespace(
        document=SimpleNamespace(document_id="memos/rota.txt", channel="upload")))
    client.app.state.intakes = {"intake-1": {"draft": draft}}
    r = client.get("/evidence/trace/memo:memos/rota.txt")
    assert r.status_code == 403, r.text
    claim = "memo:memos/rota.txt|concerns|person:sn-0042"
    body = client.get(f"/evidence/trace/{claim}")
    assert body.status_code == 403 or body.json()["entity_type"] == "identity_claim", body.text


def _add_person(g, node_id: str, canonical: str) -> None:
    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN

    pointer = g.store.sources_for("person:sn-0042")[0]
    g.store.add_node(GraphNode(id=node_id, kind=NodeKind("person"), label="Another Person",
                               canonical_id=canonical, sources=[pointer], valid_from=WHEN,
                               system_from=WHEN))


def test_a_bare_selected_key_naming_two_subjects_authorises_neither(world):
    """t08 r2 #2. `selected_patient_id` is a bare key; two subject nodes answering to it (a
    duplicate canonical id, or the same key under two subject kinds) made one confirmation release
    both. An ambiguous bare key releases nobody; a TYPED selection releases exactly its node."""
    g, client = world
    _add_person(g, "person:dup", "sn-0042")
    client.app.state.ledger = _memo_commit_selecting(g, "sn-0042")
    assert client.get("/evidence/trace/person:dup").status_code == 403
    assert client.get("/evidence/trace/person:sn-0042").status_code == 403
    client.app.state.ledger = _memo_commit_selecting(g, "person:sn-0042")
    assert client.get("/evidence/trace/person:sn-0042").status_code == 200
    assert client.get("/evidence/trace/person:dup").status_code == 403


def test_a_document_key_owned_by_two_documents_authorises_neither(world):
    """t08 r2 #3. Two document nodes answering to one key ("report.pdf" from two paths) made a
    confirmation of one release the other. Ownership is a SET; a collision fails closed."""
    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN

    g, client = world
    client.app.state.ledger = _memo_commit_selecting(g, "sn-0042")
    assert client.get("/evidence/trace/memo:memos/rota.txt").status_code == 200, "precondition"
    pointer = g.store.sources_for("memo:memos/rota.txt")[0]
    g.store.add_node(GraphNode(id="memo:elsewhere/rota.txt", kind=NodeKind("memo"),
                               label="Another rota memo", canonical_id="memos/rota.txt",
                               sources=[pointer.model_copy(update={"document_id":
                                                                   "elsewhere/rota.txt"})],
                               valid_from=WHEN, system_from=WHEN))
    assert client.get("/evidence/trace/memo:memos/rota.txt").status_code == 403
    assert client.get("/evidence/trace/memo:elsewhere/rota.txt").status_code == 403


def test_a_connection_source_id_is_not_a_document_identity(world):
    """t08 r2 #3. A document span that names no document and no capture used to be keyed by its
    `source_id` — which for a connector is the whole connection, and here equals a safe
    document's id. It names no provable document, so it fails closed."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN

    g, client = world
    # `doc_span` requires a document id; an OCR box does not — it names a media item.
    anonymous = SourcePointer(source_id="wiki/runbook.md", kind="ocr_bbox", media_id="scan-7",
                              page=1, x0=0, y0=0, x1=1, y1=1, ocr_conf=0.9)
    g.store.add_node(GraphNode(id="topic:anonymous", kind=NodeKind("topic"), label="anonymous",
                               sources=[anonymous], valid_from=WHEN, system_from=WHEN))
    assert client.get("/evidence/trace/topic:anonymous").status_code == 403


def test_a_derived_branch_into_an_unconfirmed_document_is_not_released_by_another_commit(world):
    """t08 r2 #4. Exposure walked `derived_from`; authorisation did not, and accepted ANY
    committed document it met. A pointer into the committed handbook released an entity whose
    other pointer derives from the unconfirmed memo."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN

    g, client = world
    handbook_ptr = g.store.sources_for("wiki_page:handbook/team.md")[0]
    memo_ptr = g.store.sources_for("memo:memos/rota.txt")[0]
    derived = SourcePointer(source_id="summariser", kind="derived",
                            derivation="summarised", derived_from=[memo_ptr])
    g.store.add_node(GraphNode(id="topic:mixed", kind=NodeKind("topic"), label="mixed",
                               sources=[handbook_ptr, derived], valid_from=WHEN,
                               system_from=WHEN))
    client.app.state.ledger = _FakeLedger([_committed_row(handbook_ptr, "sn-9999")])
    r = client.get("/evidence/trace/topic:mixed")
    assert r.status_code == 403, r.text
    # Both branches confirmed: released.
    client.app.state.ledger = _FakeLedger([_committed_row(handbook_ptr, "sn-9999", rowid=1),
                                           _committed_row(memo_ptr, "sn-0042", rowid=2)])
    assert client.get("/evidence/trace/topic:mixed").status_code == 200


def test_a_claim_edge_is_released_only_by_a_commit_of_its_own_document(world):
    """t08 r2 #5. A claim edge `D1 -> S` was released (as a claim, with S's key) because its
    pointer cited a DIFFERENT committed document D2."""
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind
    from generic_graph import WHEN

    g, client = world
    memo_ptr = g.store.sources_for("memo:memos/rota.txt")[0]
    g.store.add_node(GraphNode(id="memo:memos/other.txt", kind=NodeKind("memo"),
                               label="Other memo", sources=[memo_ptr], valid_from=WHEN,
                               system_from=WHEN))
    edge_id = "memo:memos/other.txt|concerns|person:sn-0042"
    g.store.add_edge(GraphEdge(id=edge_id, src_id="memo:memos/other.txt",
                               dst_id="person:sn-0042", kind=EdgeKind("concerns"),
                               sources=[memo_ptr], valid_from=WHEN, system_from=WHEN))
    client.app.state.ledger = _memo_commit_selecting(g, "sn-9999")      # commits rota.txt only
    r = client.get(f"/evidence/trace/{edge_id}")
    assert r.status_code == 403, r.text


def _write_run(g, source: str, run: str, report: dict, ledgers: dict) -> None:
    import json as _json

    run_dir = g.ingest_dir / source / run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(_json.dumps(report), encoding="utf-8")
    for name, rows in ledgers.items():
        (run_dir / name).write_text("".join(_json.dumps(r) + "\n" for r in rows),
                                    encoding="utf-8")


def test_the_source_register_publishes_no_free_text_a_document_wrote(world):
    """t08 r2 #7. `report.json` scalars (a message quoting extracted entity names, a filesystem
    path) and free-text exclusion reasons were published to any caller, outside the gate."""
    g, client = world
    name = g.store.open_node_row("person:sn-0042")["label"]
    _write_run(g, "memos", "run-002",
               {"run_id": "run-002", "status": "unverified", "started_at": "2026-09-02T00:00:00Z",
                "message": f"2 pointers do not resolve: ['person:{name}']",
                "run_dir": f"C:/corpus/{name}/run-002", "title": f"{name} intake"},
               {"extract_skipped.jsonl": [{"reason": f"chunk 1: entity '{name}': unknown kind"}]})
    for path in ("/evidence/sources", "/evidence/sources/memos"):
        text = client.get(path).text
        assert name not in text, f"{path} published a subject's name"
    latest = {s["source"]: s for s in client.get("/evidence/sources").json()["sources"]}
    report = latest["memos"]["latest"]["report"]
    assert report["status"] == "unverified" and report["run_id"] == "run-002"


def test_screen_decisions_in_the_governance_ledger_are_counted_and_listed(world):
    """E2E F-14. The ingestion screen's block / review decisions live in `governance.jsonl`;
    the register counted `excluded`/`blocked` only and reported 0 exclusions for a run whose
    screen blocked three documents and held one for review."""
    g, client = world
    rows = [
        {"decision": "block", "item_id": "a" * 64, "fired": [{"rule": "private_key_block"}],
         "reverse": "Remove the key from the file."},
        {"decision": "block", "item_id": "b" * 64,
         "fired": [{"rule": "pricing_heading"}, {"rule": "currency_table"}],
         "reverse": "Owner approves the document in the register."},
        {"decision": "review", "item_id": "c" * 64, "fired": [{"rule": "contract_value_wording"}],
         "reverse": "Owner approves the document in the register."},
        {"decision": "allow", "item_id": "d" * 64, "fired": [], "reverse": ""},
    ]
    _write_run(g, "memos", "run-002", {"run_id": "run-002", "status": "ok"},
               {"governance.jsonl": rows})
    latest = {s["source"]: s for s in client.get("/evidence/sources").json()["sources"]}
    summary = latest["memos"]["latest"]
    held = [r for r in rows if r["decision"] != "allow"]
    assert summary["excluded"] == len(held)
    screen = summary["screen_decisions"]
    assert sum(d["n"] for d in screen) == len(held)
    listed = {(d["decision"], tuple(d["rules"])): d for d in screen}
    for r in held:
        entry = listed[(r["decision"], tuple(f["rule"] for f in r["fired"]))]
        assert entry["reverse"] == r["reverse"]
    one = client.get("/evidence/sources/memos").json()["runs"][-1]
    assert one["excluded"] == len(held)
