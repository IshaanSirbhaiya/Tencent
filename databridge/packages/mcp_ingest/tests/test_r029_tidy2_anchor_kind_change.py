"""R-029 TIDY2 item 9 (SETX cross-track): the fs corpus built 0 graph nodes under the finance pack
while recording its documents as BUILT.

Investigated (see R-029-TIDY2):
* the entities: the fixture's model names `organisation` / `person`, which the finance vocabulary
  does not bind, so the wall refuses them — an ontology mismatch of the TEST fixture, expected;
* the anchors: finance binds five kinds to the `document` meta-kind and the connection names none,
  so `document_kind` refuses (AMBIGUOUS) and no anchor is written — a configuration gap, reported
  in the run's notes, expected (IDN F-08);
* the DEFECT: the documents' state was advanced anyway, so once an operator names the connection's
  `document_kind`, every unchanged document stayed "unchanged" and was never anchored.

Now a document's state records the anchor kind it was built under, and a document whose anchor
kind has CHANGED since is rebuilt. On both packs; expectations derived from the vocabulary and the
run's own ledgers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import runner
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection
from conftest import test_principal as _principal

SID = "tidy2-anchor"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def _register(root: Path, **extra) -> None:
    mi.register_connection({**fs_connection(SID, root), "auto_approve": False, **extra})
    plan = mi.propose_plan(SID)
    unknown = [t for t, c in plan.tool_classes.items() if c.startswith("unclassified")]
    mi.approve_plan(SID, principal=_principal("Dana"), vouch_read_tools=unknown)


def _anchors(store, kind: str) -> int:
    return store.fetchone("SELECT COUNT(*) c FROM nodes WHERE kind = ? AND system_to IS NULL",
                          (kind,))["c"]


@pytest.mark.parametrize("pack", ["finance", "workspace"])
def test_a_document_is_rebuilt_when_its_anchor_kind_changes(pack, walnut_env, corpus,
                                                            fake_harness, tmp_path):
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    from databridge_mcp_ingest.build import document_kind

    root, _ = corpus
    with deployment(pack) as vocab:
        store = GraphStore(str(tmp_path / "g.db"))
        _register(root)
        first_kind, _ = document_kind(vocab, None)
        rep = mi.run_ingest(SID, store=store, harness=fake_harness, vocab=vocab)
        built = [r for r in read_jsonl(Path(rep.run_dir) / "build.jsonl")
                 if r.get("kind") == "document"]
        assert built                                                      # non-vacuous
        state = runner.load_state(SID)
        assert all(state[r["item_id"]].get("anchor_kind") == first_kind for r in built)

        # nothing changed: nothing is rebuilt
        again = mi.run_ingest(SID, store=store, harness=fake_harness, vocab=vocab)
        assert [r for r in read_jsonl(Path(again.run_dir) / "build.jsonl")
                if r.get("kind") == "document"] == []

        # the operator names the anchor kind: every document is rebuilt under it, and anchored
        bound = sorted(vocab.nodes_binding_to(NodeMetaKind.DOCUMENT))
        chosen = next(k for k in bound if k != first_kind)
        _register(root, document_kind=chosen)
        named = mi.run_ingest(SID, store=store, harness=fake_harness, vocab=vocab)
        rebuilt = {r["item_id"]: r for r in read_jsonl(Path(named.run_dir) / "build.jsonl")
                   if r.get("kind") == "document"}
        assert set(rebuilt) == {r["item_id"] for r in built}
        assert all(r.get("anchor") for r in rebuilt.values())
        assert _anchors(store, chosen) == len(rebuilt)
        if first_kind is not None:
            # the anchor under the kind it was first built with is closed, never left beside it
            assert _anchors(store, first_kind) == 0
        assert all(runner.load_state(SID)[i].get("anchor_kind") == chosen for i in rebuilt)

        # and then it is unchanged again
        last = mi.run_ingest(SID, store=store, harness=fake_harness, vocab=vocab)
        assert [r for r in read_jsonl(Path(last.run_dir) / "build.jsonl")
                if r.get("kind") == "document"] == []
