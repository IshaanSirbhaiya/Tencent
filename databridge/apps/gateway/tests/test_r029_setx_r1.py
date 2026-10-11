"""R-029 SETX, Codex round 1, through the real `/adapter-setup` routes.

#1 (Critical): the hand-off BUILD is bound to the authorisation the person gave. Before the build
   reads the source it re-verifies that authorisation against the setup as it is then; a change
   in between (a new discovery) refuses the build, builds nothing and pauses the keeper. A document
   whose bytes changed after the decision is detected in what the build wrote: the keeper is
   paused and the Activity record says so. An already-active keeper hands off only under a NEW
   authorisation bound the same way.
#5 (Medium): `GET /adapter-setup/{id}` returns a public projection of the connection - never its
   command, arguments, environment, working directory or server root.

The corpus is the ingest package's own fixture corpus behind the real filesystem MCP server.
Expectations are derived from the run's own state.
"""
from __future__ import annotations

import json
import re

import pytest

from fastapi.testclient import TestClient

from conftest import auth_headers
from test_feat8_adapter_setup import (PERSON, SID, _handoff, _nodes,  # noqa: F401
                                      docs_source, env, estate)
from test_r029_setx_setup import _ABS, _approve_plan, _state


def _to_decision(c):
    """Start, approve the plan, discover, decide on the documents. Returns the documents view."""
    assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
    _approve_plan(c)
    assert c.post(f"/adapter-setup/{SID}/discover").status_code == 202
    docs = _state(c)["documents"]
    assert docs["digest"], docs
    r = c.post(f"/adapter-setup/{SID}/documents/approve",
               json={"discovery_run_id": docs["discovery_run_id"],
                     "documents_digest": docs["digest"]})
    assert r.status_code == 200, r.text
    return docs


def _build_records(c):
    return [a for a in _state(c)["activity"] if a["step"] == "setup.build"]


def test_c1_a_discovery_between_the_authorisation_and_the_build_refuses_the_build(
        docs_source, monkeypatch):
    import databridge_mcp_ingest as mi

    app, rt, store = docs_source["app"], docs_source["rt"], docs_source["store"]
    real_activate = rt.manager.activate

    def activate_then_rediscover(agent_id, *, by=""):
        spec = real_activate(agent_id, by=by)
        mi.run_ingest(SID, store=None, capture_only=True)        # the setup changes under it
        return spec

    monkeypatch.setattr(rt.manager, "activate", activate_then_rediscover)
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        _to_decision(c)
        before = _nodes(store)
        r = _handoff(c)
        assert r.status_code == 202, r.text
        builds = _build_records(c)
        assert builds and builds[-1]["status"] == "failed", builds
        assert "changed" in builds[-1]["reason"] and "hand off again" in builds[-1]["reason"]
        assert _state(c)["keeper"]["status"] == "paused"
    assert _nodes(store) == before                                # nothing was built


@pytest.mark.parametrize("pack", ["workspace", "finance"])
def test_c1_a_document_changed_after_the_decision_is_not_built(docs_source, monkeypatch,
                                                              tmp_path, pack):
    """SETX #1 closed (TABX pinned builds): the hand-off build is pinned to the capture digests
    the person authorised; a document whose bytes changed after the decision is NOT built, the
    build reports it and the keeper is paused with that reason."""
    import hashlib

    from databridge_graph.packs import clear_caches, deployment
    from databridge_mcp_ingest.runner import latest_discovery, load_state

    app, rt = docs_source["app"], docs_source["rt"]
    corpus = tmp_path / "corpus"
    target = corpus / "projects" / "alpha" / "notes.txt"
    assert target.is_file()
    real_activate = rt.manager.activate
    edited = {}

    def activate_then_edit(agent_id, *, by=""):
        spec = real_activate(agent_id, by=by)
        target.write_bytes(target.read_bytes() + b"\nAppended after the decision.\n")
        edited["sha"] = hashlib.sha256(target.read_bytes()).hexdigest()
        return spec

    monkeypatch.setattr(rt.manager, "activate", activate_then_edit)
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    clear_caches()
    with deployment(pack), TestClient(app, headers=auth_headers(PERSON)) as c:
        _to_decision(c)
        disc = latest_discovery(SID)
        [item] = [d["item_id"] for d in disc["documents"]["items"]
                  if str(d.get("uri", "")).replace("\\", "/").endswith("alpha/notes.txt")]
        assert _handoff(c).status_code == 202
        builds = _build_records(c)
        assert builds and builds[-1]["status"] == "failed", builds
        assert re.search(r"1 item\(s\) changed since the decision",
                         builds[-1]["reason"]), builds[-1]["reason"]
        assert "NOT built" in builds[-1]["reason"]
        assert _state(c)["keeper"]["status"] == "paused"
    assert (load_state(SID).get(item) or {}).get("sha256") != edited["sha"]    # not built
    others = [d for d in disc["documents"]["items"]
              if d["decision"] == "allow" and not d.get("held") and d["item_id"] != item]
    assert others and all(load_state(SID).get(d["item_id"]) for d in others)   # the rest built


def test_c1_a_table_changed_after_the_approval_is_not_built(estate):
    """Both estates: a person-approved table whose rows change between the discovery and the
    hand-off build is not built (pinned to the approved capture); the keeper is paused."""
    from test_feat8_adapter_setup import _approve, _explain, _plan_approved_and_discovered

    from databridge_mcp_ingest.runner import load_state

    app, db = estate["app"], estate["db"]
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        disc = _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200
        table = proposed[0]
        schema, tname = table.rsplit(".", 1)
        cols = [r for r in db.execute(f"PRAGMA {schema}.table_info('{tname}')").fetchall()]
        text_col = next(r[1] for r in cols if r[5] == 0 and "CHAR" in str(r[2]).upper()
                        or (r[5] == 0 and "TEXT" in str(r[2]).upper()))
        db.execute(f'UPDATE {schema}."{tname}" SET "{text_col}" = "{text_col}" || ? '
                   f'WHERE rowid = (SELECT MIN(rowid) FROM {schema}."{tname}")', (" (edited)",))
        db.commit()
        assert _handoff(c).status_code == 202
        builds = _build_records(c)
        assert builds[-1]["status"] == "failed", builds[-1]
        assert "changed since the decision" in builds[-1]["reason"], builds[-1]
        assert _state(c)["keeper"]["status"] == "paused"
    item = disc["tables"][table]["item_id"]
    assert not (load_state(SID).get(item) or {}).get("graph")              # never built


def test_c1_an_active_keeper_hands_off_only_under_a_new_bound_authorisation(docs_source):
    from databridge_adapter_agents.setup import handoff_ledger, keeper_id

    app, rt = docs_source["app"], docs_source["rt"]
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        _to_decision(c)
        assert _handoff(c).status_code == 202
        assert _state(c)["keeper"]["status"] == "active"
        led = handoff_ledger(rt.manager.registry.root, SID)
        n = len(led.read())
        assert _handoff(c).status_code == 202, "an active, sealed keeper may hand off again"
        recs = led.read()
        assert len(recs) == n + 1 and recs[-1]["agent_id"] == keeper_id(SID)
        assert recs[-1]["principal_id"] == PERSON
        builds = _build_records(c)
        assert builds[-1]["status"] == "completed", builds[-1]


def test_m5_the_setup_detail_returns_a_public_projection_of_the_connection(docs_source,
                                                                           tmp_path):
    app = docs_source["app"]
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
        body = c.get(f"/adapter-setup/{SID}").json()
    conn = body["connection"]
    assert conn["id"] == SID and conn["transport"] == {"type": "stdio"}
    text = json.dumps(body)
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "\\\\") not in text
    assert not _ABS.search(json.dumps(conn)), conn
    for k in ("command", "args", "env", "env_from", "cwd"):
        assert k not in json.dumps(conn["transport"])


def test_h3_a_stopped_one_time_run_is_not_reported_done():
    from databridge_gateway.one_time_routes import task_state

    assert task_state("stopped", []) == "stopped"
    assert task_state("completed", []) == "done"
    assert task_state("stopped", ["proposed"]) == "waiting_for_approval"
