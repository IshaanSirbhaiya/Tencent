"""R-029 SETX (E2E run 5, 2026-10-03): W-05 and W-06 through the real `/adapter-setup` routes.

W-05: a document source that a Sources run ALREADY built (run 5: `larkspur-files`) is discovered
as "unchanged" - and the hand-off answered 409 `HANDOFF_NOT_READY` "the discovery captured nothing
the screen allowed to build" for ever. Now a person decides to KEEP the already-built documents
(the same digest-bound, person-only documents decision), the keeper is handed the source, and
nothing is rebuilt; nothing is built or adopted before that decision.

W-06: setup surfaces carried absolute server paths (a held document's ``table``/``what``, the last
run's ``run_dir``) and the explanation's model was the literal "model".

The corpus is the ingest package's own fixture corpus, read through the real filesystem MCP server;
both packs bound in turn. Expectations are derived from the run's own state, never written down.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers
from test_feat8_adapter_setup import (PERSON, SID, _handoff, _nodes,  # noqa: F401
                                      docs_source, env)

#: an absolute filesystem path: a drive letter, or a root-anchored POSIX path of 2+ segments
_ABS = re.compile(r"(?:\b[A-Za-z]:[\\/]|(?<![\w.:/])/(?:[\w.-]+/){2,})")


def _approve_plan(c):
    d = c.get(f"/adapter-setup/{SID}").json()
    vouch = [t for t, k in (d["plan"].get("tool_classes") or {}).items()
             if str(k).startswith("unclassified")]
    r = c.post(f"/adapter-setup/{SID}/plan/approve",
               json={"plan_digest": d["plan_digest"], "vouch_read_tools": vouch})
    assert r.status_code == 200, r.text


def _state(c):
    return c.get(f"/adapter-setup/{SID}").json()["state"]


@pytest.mark.parametrize("pack", ["workspace", "finance"])
def test_w05_an_already_built_document_source_is_kept_by_its_keeper_on_a_persons_decision(
        docs_source, pack, monkeypatch):
    import databridge_mcp_ingest as mi
    from databridge_graph.packs import clear_caches, deployment
    from databridge_mcp_ingest.runner import load_state

    app, store = docs_source["app"], docs_source["store"]
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)     # the repo's packs: both
    clear_caches()
    with deployment(pack), TestClient(app, headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
        _approve_plan(c)
        # a Sources run builds the source BEFORE its keeper exists (run 5's order)
        built = mi.run_ingest(SID, store=store)
        assert built.status in ("ok", "ok_with_warnings", "completed"), built.status
        nodes_before, state_before = _nodes(store), load_state(SID)
        assert state_before                                            # non-vacuous
        assert c.post(f"/adapter-setup/{SID}/discover").status_code == 202
        st = _state(c)
        docs = st["documents"]
        assert docs["allowed"] == 0 and docs["kept"] > 0, docs           # the run-5 shape
        assert docs["digest"] and not docs["decided"]
        steps = {s["id"]: s for s in st["steps"]}
        assert steps["approve_documents"]["status"] == "waiting_for_person"
        assert steps["approve_mapping"]["status"] == "done"            # documents only
        # nothing is handed off before a person decides
        r = _handoff(c)
        assert r.status_code == 409, r.text
        assert "captured nothing" not in r.text and "already built" in r.text
        assert c.get(f"/adapter-setup/{SID}").json()["state"]["keeper"]["status"] == "draft"
        # the decision is bound to what was shown
        r = c.post(f"/adapter-setup/{SID}/documents/approve",
                   json={"discovery_run_id": docs["discovery_run_id"],
                         "documents_digest": "sha256:not-shown"})
        assert r.status_code == 409
        r = c.post(f"/adapter-setup/{SID}/documents/approve",
                   json={"discovery_run_id": docs["discovery_run_id"],
                         "documents_digest": docs["digest"]})
        assert r.status_code == 200, r.text
        dec = r.json()["decision"]
        assert (dec["decision"], dec["to_build"], dec["to_keep"], dec["principal_id"]) == \
            ("adopt", 0, docs["kept"], PERSON)
        assert r.json()["state"]["documents"]["decided"] is True
        r = _handoff(c)
        assert r.status_code == 202, r.text
        assert _state(c)["keeper"]["status"] == "active"
    # the keeper keeps the source; nothing was rebuilt
    assert _nodes(store) == nodes_before
    assert {k: v.get("sha256") for k, v in load_state(SID).items()} == \
        {k: v.get("sha256") for k, v in state_before.items()}


def test_w06_setup_state_carries_no_server_path(docs_source):
    import databridge_mcp_ingest as mi

    app, store = docs_source["app"], docs_source["store"]
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
        _approve_plan(c)
        mi.run_ingest(SID, store=store)
        assert c.post(f"/adapter-setup/{SID}/discover").status_code == 202
        st = _state(c)
        assert st["last_run"] and st["pending_decisions"] is not None
        for d in st["pending_decisions"]:
            assert not _ABS.search(str(d.get("table") or "")), d
            assert not _ABS.search(d["what"]), d
        assert "run_dir" not in (st["last_run"] or {})
        import json

        assert not _ABS.search(json.dumps(st["last_run"])), st["last_run"]


@pytest.mark.parametrize("path", [
    r"C:\DataBridge\DataBridge POC\databridge\packs\finance\evals\agent_contract.jsonl",
    "/srv/walnut/packs/workspace/evals/agent_contract.jsonl",
    "packs/workspace/evals/agent_contract.jsonl"])
def test_w06_a_one_time_plan_names_its_golden_set_without_the_server_directory(path):
    """The run-5 one-time plan carried ``grants.golden_set`` as the server's absolute path."""
    from types import SimpleNamespace

    from databridge_gateway.one_time_routes import _grants

    compiled = SimpleNamespace(purpose="p", grants=SimpleNamespace(grants=[]),
                               evals=SimpleNamespace(golden_set=path, records=7),
                               citation="required_per_field",
                               budget=SimpleNamespace(tokens_per_run=60000))
    got = _grants(SimpleNamespace(decision=SimpleNamespace(compiled=compiled)))["golden_set"]
    assert not _ABS.search(got) and not got.startswith(("/", "\\")), got
    assert got.startswith("packs/") and got.endswith("agent_contract.jsonl")
