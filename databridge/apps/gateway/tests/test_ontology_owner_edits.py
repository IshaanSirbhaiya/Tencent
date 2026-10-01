"""`/ontology` owner edits — "the ontology page allows me to define the relations and entities and
the system only maps to these."

What is proven here, each against a PRIVATE pack tree in `tmp_path` (``DATABRIDGE_PACKS_ROOT``), so
the repo's own packs are never touched:

* an owner-defined relation (with source and target kinds) is written to
  ``packs/<pack>/ontology.yaml``, appears in ``active_vocabulary()`` on the very next call (the
  caches were reloaded), and is ENFORCED by extraction — the right endpoints are accepted, the
  wrong ones refused (integration with the closed-vocabulary extractor through a fake harness);
* an endpoint kind the pack does not declare is refused, and the file is left byte-identical;
* a subject (identity-protected) change without an explicit confirmation is refused;
* every change leaves an append-only audit line naming the authenticated principal;
* every write route answers 401 without a credential — the list of routes is DERIVED from the app;
* per-kind counts are derived from a store this test builds, and off-ontology refusal counts from
  an ingestion report this test writes from a real extraction's ``refused`` output.
"""
from __future__ import annotations

import collections
import datetime as dt
import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS, TEST_TOKENS
from databridge_gateway.ontology_routes import register_ontology_routes

#: A minimal pack, written by this test, so the assertions do not depend on the shipped workspace
#: pack's current content (another workstream edits it). The shape mirrors it: one subject kind,
#: comments that must survive every edit, and a relation whose `dst` is a MULTI-LINE flow list.
PACK_YAML = """\
# packs/workspace — test copy. This header comment must survive every owner edit.
pack: workspace
title: Workspace (test)
version: "0.1"

node_kinds:
  - name: person
    binds_to: entity
    description: A natural person.

  - name: organisation
    binds_to: organisation
    description: A company.

  - name: document
    binds_to: document
    description: A captured file.

# --- relations: this column-0 comment sits between the two blocks ---
edge_kinds:
  - name: works_for
    binds_to: role_of
    src: [person]
    dst: [organisation]
    description: A person works for an organisation.

  - name: mentions
    binds_to: references
    src: [document]
    dst: [person,
          organisation]
    description: A document names something.

subject_kinds:
  - kind: person
    identifiers: [email, employee_id]
    never_fuzzy_merge: true
    attestation: verified_channel
    grant_type: PersonReadGrant
"""


@pytest.fixture()
def packs(tmp_path, monkeypatch):
    from databridge_graph.packs import clear_caches

    root = tmp_path / "packs"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "ontology.yaml").write_text(PACK_YAML, encoding="utf-8")
    (root / "deployment.yaml").write_text("enabled_packs:\n  - workspace\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(tmp_path / "state" / "ontology_state.json"))
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "ingest"))
    monkeypatch.delenv("WALNUT_ONTOLOGY_AUDIT_PATH", raising=False)
    # Owner edits need an OWNER principal (review R-017 finding 2) — configured, never requested.
    # Derived from the conftest directory: whoever `AUTH_HEADERS` authenticates as.
    monkeypatch.setenv("WALNUT_ONTOLOGY_OWNERS",
                       TEST_TOKENS[AUTH_HEADERS["Authorization"].split(" ", 1)[1]])
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    clear_caches()
    yield root
    clear_caches()


@pytest.fixture()
def app(packs):
    a = FastAPI()
    register_ontology_routes(a)
    return a


@pytest.fixture()
def client(app):
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


def _pack_file(packs):
    return packs / "workspace" / "ontology.yaml"


def _audit(tmp_path):
    path = tmp_path / "state" / "ontology_audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _define(client, **body):
    return client.post("/ontology/kinds", json=body)


# --- 1. the default target is the bound pack ------------------------------------------------
def test_the_default_target_is_the_first_bound_pack(client):
    from databridge_graph.packs import configured_packs

    body = client.get("/ontology").json()
    assert body["pack"] == configured_packs()[0]
    listed = client.get("/ontology/packs").json()
    assert listed["default"] == configured_packs()[0]


# --- 2. define a relation: persisted, in the vocabulary, enforced by extraction ---------------
def test_an_owner_defined_relation_persists_and_is_in_the_active_vocabulary(client, packs):
    from databridge_graph.packs import active_vocabulary

    before = active_vocabulary()
    assert "vendor" not in before.node_kinds          # non-vacuous: genuinely new

    ent = _define(client, axis="node", name="vendor", binds_to="organisation",
                  description="A supplier of goods.", by="Owner O.")
    assert ent.status_code == 200, ent.text
    rel = _define(client, axis="edge", name="supplies", binds_to="party_to",
                  src=["vendor"], dst=["organisation"], description="A vendor supplies a company.",
                  by="Owner O.")
    assert rel.status_code == 200, rel.text
    assert rel.json()["in_active_vocabulary"] is True
    assert rel.json()["pack_bound"] is True

    parsed = yaml.safe_load(_pack_file(packs).read_text(encoding="utf-8"))
    written = next(e for e in parsed["edge_kinds"] if e["name"] == "supplies")
    assert written["src"] == ["vendor"] and written["dst"] == ["organisation"]
    assert any(n["name"] == "vendor" for n in parsed["node_kinds"])
    # Every comment in the original survives (text-level edit, never a re-dump).
    text = _pack_file(packs).read_text(encoding="utf-8")
    for line in PACK_YAML.splitlines():
        if line.lstrip().startswith("#"):
            assert line in text

    # The very next vocabulary read sees it — the caches were reloaded, no restart.
    vocab = active_vocabulary()
    assert vocab.generation != before.generation
    assert vocab.edge_kinds["supplies"].src == ("vendor",)
    assert vocab.edge_kinds["supplies"].dst == ("organisation",)
    assert "vendor" in vocab.node_kinds


def _extract(text, ents, rels, store=None):
    from databridge_convert.ir.models import Block, DocumentRecord
    from databridge_core import SnapshotIdentity
    from databridge_graph import extract_graph, gold_extraction_handler
    from databridge_harness import FakeBackend, Harness

    snap = SnapshotIdentity(source_id="doc-1", as_of=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
                            content_hash="h", schema_hash="s")
    doc = DocumentRecord(document_id="doc-1", sha256="x", snapshot=snap,
                         blocks=[Block(kind="Paragraph", text=text)])
    harness = Harness(FakeBackend(gold_extraction_handler(ents, rels)), profile="cloud")
    return extract_graph(doc, harness, store=store)


TEXT = "Acme Steel Ltd supplies Northwind Corp with steel under a signed order."


def _at(needle):
    start = TEXT.find(needle)
    assert start >= 0
    return {"char_start": start, "char_end": start + len(needle)}


def test_extraction_maps_only_to_the_owner_defined_relation(client):
    """Before the owner defines `supplies`, extraction refuses it; after, the right endpoints are
    accepted and the wrong ones refused. Nothing restarts in between."""
    ents = [{"name": "Acme Steel Ltd", "kind": "vendor", **_at("Acme Steel Ltd")},
            {"name": "Northwind Corp", "kind": "organisation", **_at("Northwind Corp")}]
    right = [{"src_name": "Acme Steel Ltd", "rel_type": "supplies", "dst_name": "Northwind Corp",
              "evidence_char_start": 0, "evidence_char_end": len(TEXT)}]
    wrong = [{"src_name": "Northwind Corp", "rel_type": "supplies", "dst_name": "Acme Steel Ltd",
              "evidence_char_start": 0, "evidence_char_end": len(TEXT)}]

    before = _extract(TEXT, ents, right)
    assert before.relations == [] and any("vendor" in r for r in before.refused)

    assert _define(client, axis="node", name="vendor", binds_to="organisation").status_code == 200
    assert _define(client, axis="edge", name="supplies", binds_to="party_to",
                   src=["vendor"], dst=["organisation"]).status_code == 200

    accepted = _extract(TEXT, ents, right)
    assert [e.kind.value for e in accepted.relations] == ["supplies"]
    refused = _extract(TEXT, ents, wrong)
    assert refused.relations == []
    assert any("wrong endpoint kinds for 'supplies'" in r for r in refused.refused)


# --- 3. invalid endpoints are refused, and nothing is written --------------------------------
def test_an_endpoint_kind_the_pack_does_not_declare_is_refused(client, packs):
    before = _pack_file(packs).read_text(encoding="utf-8")
    r = _define(client, axis="edge", name="ships_to", binds_to="references",
                src=["organisation"], dst=["warehouse"])
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "UNKNOWN_ENDPOINT_KIND"
    missing = _define(client, axis="edge", name="ships_to", binds_to="references",
                      src=["organisation"], dst=[])
    assert missing.json()["refusal"]["code"] == "ENDPOINTS_REQUIRED"
    assert _pack_file(packs).read_text(encoding="utf-8") == before


def test_setting_endpoints_rewrites_a_multi_line_list_and_nothing_else(client, packs):
    from databridge_graph.packs import active_vocabulary, load_pack

    assert _define(client, axis="node", name="vendor", binds_to="organisation").status_code == 200
    before = load_pack("workspace", use_cache=False)
    mentions = before.edge_kinds["mentions"]
    want_dst = [*mentions.dst, "vendor"]
    # `mentions` touches the subject `person`, so this is identity-protected: refused first...
    r = client.post("/ontology/kinds/endpoints", json={
        "kind": "mentions", "src": list(mentions.src), "dst": want_dst})
    assert r.status_code == 409 and r.json()["refusal"]["code"] == "IDENTITY_CONFIRMATION_REQUIRED"
    # ...then applied with the explicit confirmation.
    r = client.post("/ontology/kinds/endpoints", json={
        "kind": "mentions", "src": list(mentions.src), "dst": want_dst, "confirm_identity": True})
    assert r.status_code == 200, r.text
    after = active_vocabulary().edge_kinds["mentions"]
    assert list(after.dst) == want_dst and after.src == mentions.src
    # Every other declaration is unchanged.
    reloaded = load_pack("workspace", use_cache=False)
    assert {k: (v.src, v.dst) for k, v in reloaded.edge_kinds.items() if k != "mentions"} == \
           {k: (v.src, v.dst) for k, v in before.edge_kinds.items() if k != "mentions"}


# --- 4. subject kinds are identity-protected ------------------------------------------------
def test_a_subject_kind_change_without_confirmation_is_refused(client, packs):
    from databridge_graph.packs import active_vocabulary

    before = _pack_file(packs).read_text(encoding="utf-8")
    body = dict(axis="node", name="contractor", binds_to="entity",
                subject={"identifiers": ["email"], "attestation": "verified_channel",
                         "grant_type": "ContractorReadGrant"})
    r = _define(client, **body)
    assert r.status_code == 409, r.text
    assert r.json()["refusal"]["code"] == "IDENTITY_CONFIRMATION_REQUIRED"
    assert "subject_declaration" in r.json()["refusal"]["identity_classes"]
    assert _pack_file(packs).read_text(encoding="utf-8") == before

    ok = _define(client, **body, confirm_identity=True, reason="contractors are people")
    assert ok.status_code == 200, ok.text
    assert active_vocabulary().is_subject("contractor")

    # Deprecating (or renaming) an existing subject is refused without confirmation too.
    subject = next(iter(active_vocabulary().subject_kinds))
    dep = client.post("/ontology/kinds/deprecate", json={"axis": "node", "kind": subject,
                                                         "by": "Owner O."})
    assert dep.status_code == 422 and dep.json()["refusal"]["code"] == "KIND_NOT_EDITABLE"
    ren = client.post("/ontology/kinds/rename", json={"axis": "node", "kind": subject,
                                                      "new_name": "individual"})
    assert ren.status_code == 409
    assert "subject_rename" in ren.json()["refusal"]["identity_classes"]


def test_a_relation_touching_a_subject_needs_confirmation(client):
    from databridge_graph.packs import active_vocabulary

    subject = next(iter(active_vocabulary().subject_kinds))
    r = _define(client, axis="edge", name="signed_by", binds_to="party_to",
                src=["document"], dst=[subject])
    assert r.status_code == 409
    assert r.json()["refusal"]["identity_classes"] == ["subject_touching"]


# --- 5. rename = deprecate old + add new ----------------------------------------------------
def test_rename_adds_the_new_kind_deprecates_the_old_and_carries_relations(client, packs):
    from databridge_graph.packs import active_vocabulary

    old_edges = {k: v for k, v in active_vocabulary().edge_kinds.items()
                 if "organisation" in v.src or "organisation" in v.dst}
    assert old_edges                                   # non-vacuous
    r = client.post("/ontology/kinds/rename", json={
        "axis": "node", "kind": "organisation", "new_name": "company", "by": "Owner O.",
        "confirm_identity": True})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["relations_widened"]) == sorted(old_edges)
    vocab = active_vocabulary()
    assert "company" in vocab.node_kinds and "organisation" in vocab.node_kinds  # never deleted
    for name in old_edges:
        ends = (*vocab.edge_kinds[name].src, *vocab.edge_kinds[name].dst)
        assert "company" in ends
    view = client.get("/ontology").json()
    old = next(k for k in view["node_kinds"] if k["kind"] == "organisation")
    assert old["deprecated"] is True


# --- 6. audit ------------------------------------------------------------------------------
def test_every_owner_change_leaves_an_audit_line_naming_the_principal(client, tmp_path):
    principal = TEST_TOKENS[AUTH_HEADERS["Authorization"].split(" ", 1)[1]]
    assert _audit(tmp_path) == []
    assert _define(client, axis="node", name="vendor", binds_to="organisation",
                   by="Owner O.", reason="we buy from them").status_code == 200
    client.post("/ontology/kinds/deprecate", json={"axis": "node", "kind": "vendor",
                                                   "by": "Owner O."})
    everything = _audit(tmp_path)
    # R-019 finding 5: every change is preceded by a durable intent line carrying the op_id its
    # completion line repeats — the intent is written BEFORE anything changes.
    intents = [r for r in everything if r["action"] == "intent"]
    rows = [r for r in everything if r["action"] != "intent"]
    assert [r["action"] for r in rows] == ["define", "deprecate"]
    assert [i["intended_action"] for i in intents] == [r["action"] for r in rows]
    assert [i["op_id"] for i in intents] == [r["op_id"] for r in rows]
    assert everything.index(intents[0]) < everything.index(rows[0])
    assert all(r["principal"] == principal and r["kind"] == "vendor" and r["at"] for r in rows)
    assert rows[0]["reason"] == "we buy from them"
    served = client.get("/ontology/audit").json()["entries"]
    assert [r["action"] for r in served] == ["deprecate", "define"]      # newest first


# --- 7. every write route requires a principal ----------------------------------------------
def test_every_write_route_is_401_without_a_credential(app):
    writes = sorted({(m, r.path) for r in app.routes if r.path.startswith("/ontology")
                     for m in getattr(r, "methods", set()) if m in {"POST", "PUT", "PATCH",
                                                                    "DELETE"}})
    assert ("POST", "/ontology/kinds") in writes and len(writes) >= 5
    with TestClient(app) as anonymous:
        for method, path in writes:
            r = anonymous.request(method, path.replace("{proposal_id}", "prop-0001"), json={})
            assert r.status_code == 401, (method, path, r.status_code, r.text)
        # INT1 (2026-10-01): reads are gated too — the read reports kind counts from the shared
        # store, and the gateway serves nothing anonymously except `/health`.
        assert anonymous.get("/ontology").status_code == 401
        assert anonymous.get("/ontology/packs").status_code == 401


# --- 8. counts come from the store and from ingestion ---------------------------------------
def test_counts_derive_from_the_shared_store_and_the_ingest_report(app, client, tmp_path):
    from databridge_graph import GraphStore

    assert _define(client, axis="node", name="vendor", binds_to="organisation").status_code == 200
    assert _define(client, axis="edge", name="supplies", binds_to="party_to",
                   src=["vendor"], dst=["organisation"]).status_code == 200

    empty = client.get("/ontology").json()
    assert empty["data"]["store_available"] is False       # honest: no store attached
    assert all(k["count"] is None for k in empty["node_kinds"])

    store = GraphStore(":memory:")
    ents = [{"name": "Acme Steel Ltd", "kind": "vendor", **_at("Acme Steel Ltd")},
            {"name": "Northwind Corp", "kind": "organisation", **_at("Northwind Corp")}]
    right = [{"src_name": "Acme Steel Ltd", "rel_type": "supplies", "dst_name": "Northwind Corp",
              "evidence_char_start": 0, "evidence_char_end": len(TEXT)}]
    wrong = [{"src_name": "Northwind Corp", "rel_type": "supplies", "dst_name": "Acme Steel Ltd",
              "evidence_char_start": 0, "evidence_char_end": len(TEXT)},
             {"src_name": "Northwind Corp", "rel_type": "invented_rel",
              "dst_name": "Acme Steel Ltd", "evidence_char_start": 0,
              "evidence_char_end": len(TEXT)}]
    res = _extract(TEXT, ents, right, store=store)
    refused = _extract(TEXT, ents, wrong).refused
    app.state.graph_store = store

    run = tmp_path / "ingest" / "files" / "run-0001"
    run.mkdir(parents=True)
    (run / "report.json").write_text(json.dumps({"extract": {"refused": refused}}),
                                     encoding="utf-8")

    body = client.get("/ontology").json()
    want_nodes = collections.Counter(n.kind.value for n in res.entities)
    want_edges = collections.Counter(e.kind.value for e in res.relations)
    assert want_nodes and want_edges                                     # non-vacuous
    got_nodes = {k["kind"]: k["count"] for k in body["node_kinds"]}
    got_edges = {k["kind"]: k["count"] for k in body["edge_kinds"]}
    for kind, n in want_nodes.items():
        assert got_nodes[kind] == n
    for kind, n in want_edges.items():
        assert got_edges[kind] == n
    assert set(body["data"]["kinds_without_data"]) == (
        {k for k, n in got_nodes.items() if not n} | {k for k, n in got_edges.items() if not n})

    endpoint_refusals = sum("wrong endpoint kinds for 'supplies'" in r for r in refused)
    assert endpoint_refusals >= 1
    supplies = next(k for k in body["edge_kinds"] if k["kind"] == "supplies")
    assert supplies["refused_off_ontology"] == endpoint_refusals
    assert body["data"]["ingest"]["unknown_edge_kinds"].get("invented_rel") == \
        sum("'invented_rel'" in r for r in refused)


def test_meta_kinds_carry_a_plain_english_explanation_from_the_meta_model(client):
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind

    cat = client.get("/ontology").json()["meta_kinds"]
    served = {m["value"]: m["explanation"] for m in (*cat["node"], *cat["edge"])}
    for m in (*NodeMetaKind, *EdgeMetaKind):
        assert served[m.value], f"meta-kind {m.value!r} has no explanation in meta.py's docstring"
